"""Story 5.4 / AC4 (Scheduler Rules 멱등성) — PostgreSQL-gated negative/concurrency.

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) due 질의(``next_run_at <= now``)가 **활성 대상만** 반환(PAUSED/future 제외).
  (2) **동시 tick 멱등성** — 두 tick/세션이 같은 due 대상에 정확히 1 CrawlJob(conditional UPDATE
      가 ``WHERE next_run_at <= now`` 로 경합 차단; 둘 다 enqueue 하지 않음).
  (3) 활성 CrawlJob(PENDING)이 있는 대상은 재-enqueue 0(멱등 — 중복 due 작업 차단).

**SQLite 로 경합/조건부 UPDATE 를 흉내내지 않는다**(의미 차이 오탐) — 현 WSL/venv 에 Postgres
부재 시 전부 skip 하고 Completion Notes 에 투명 명기한다. always-run in-memory tick 테스트
(``tests/server/test_scheduler_tick.py``)가 멱등/throttle/게이트 의미를 결정적으로 잠근다.

PG fixture 는 **유효 UUID + 부모 행 시드**(5.3 HIGH-1 교훈 — 비-UUID/미시드 FK 는 실행 즉시
에러): tenants(lifecycle ACTIVE)·subscriptions(PAYMENT_ACTIVE)·platform_accounts(BAEMIN)·
agents(capacity)·monitoring_targets(ACTIVE). fake 값만(실제 토큰/전화/이메일/chat_id 없음).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_gate = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). in-memory tick 으로 잠금.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

# 유효 UUID — PG FK(Uuid 타입) 충족. 시드한 부모 행과 1:1.
_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ACCOUNT = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_AGENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_TARGET_DUE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_TARGET_FUTURE = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_TARGET_PAUSED = "ffffffff-ffff-ffff-ffff-ffffffffffff"


async def _seed(session_factory) -> None:
    """due/future/paused 대상 + 부모 체인을 시드한다."""
    from rider_server.db.models.account import MonitoringTarget, PlatformAccount
    from rider_server.db.models.agent import Agent
    from rider_server.db.models.tenancy import Subscription, Tenant
    from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

    async with session_factory() as session:
        session.add(
            Tenant(id=uuid.UUID(_TENANT), name="t", status="ACTIVE", created_at=_T0)
        )
        await session.flush()
        session.add(
            Subscription(
                id=uuid.uuid4(),
                tenant_id=uuid.UUID(_TENANT),
                plan="basic",
                status="PAYMENT_ACTIVE",
                quotas={},
            )
        )
        session.add(
            PlatformAccount(
                id=uuid.UUID(_ACCOUNT),
                tenant_id=uuid.UUID(_TENANT),
                platform="BAEMIN",
                label="l",
                username="vault://u",
                password="vault://p",
                auth_state="UNKNOWN",
            )
        )
        session.add(
            Agent(
                id=uuid.UUID(_AGENT),
                name="sched-test-agent",
                machine_id="m",
                version="0.0.0",
                os="linux",
                status="active",
                last_heartbeat_at=_T0,
                capacity_json={
                    "max_in_flight": 10,
                    "capabilities": [JOB_TYPE_CRAWL_BAEMIN],
                },
            )
        )
        await session.flush()
        # due(next_run_at NULL = 즉시 due), future(미래), paused(상태 비활성).
        session.add(
            MonitoringTarget(
                id=uuid.UUID(_TARGET_DUE),
                tenant_id=uuid.UUID(_TENANT),
                platform_account_id=uuid.UUID(_ACCOUNT),
                name="due",
                center_name="c",
                external_id="",
                url="",
                interval_minutes=10,
                status="ACTIVE",
                next_run_at=None,
            )
        )
        session.add(
            MonitoringTarget(
                id=uuid.UUID(_TARGET_FUTURE),
                tenant_id=uuid.UUID(_TENANT),
                platform_account_id=uuid.UUID(_ACCOUNT),
                name="future",
                center_name="c",
                external_id="",
                url="",
                interval_minutes=10,
                status="ACTIVE",
                next_run_at=_T0 + timedelta(minutes=30),
            )
        )
        session.add(
            MonitoringTarget(
                id=uuid.UUID(_TARGET_PAUSED),
                tenant_id=uuid.UUID(_TENANT),
                platform_account_id=uuid.UUID(_ACCOUNT),
                name="paused",
                center_name="c",
                external_id="",
                url="",
                interval_minutes=10,
                status="PAUSED",
                next_run_at=None,
            )
        )
        await session.commit()


def _fresh_pg() -> tuple[object, object, object, object]:
    """빈 PG 에 0001+0002+0003 적용 후 (repo, queue_backend, session_factory, teardown)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.queue import PostgresQueueBackend
    from rider_server.scheduler import PostgresSchedulerRepository

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)
    factory = create_session_factory(engine)
    asyncio.run(_seed(factory))
    repo = PostgresSchedulerRepository(factory)
    backend = PostgresQueueBackend(factory)

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return repo, backend, factory, _teardown


@pytest.fixture
def pg_env():
    repo, backend, factory, teardown = _fresh_pg()
    try:
        yield repo, backend, factory
    finally:
        teardown()


@_pg_gate
def test_due_query_returns_active_due_targets_only(pg_env):
    repo, _backend, _factory = pg_env

    async def _run():
        due = await repo.due_targets(now=_T0)
        ids = {t.target_id for t in due}
        assert _TARGET_DUE in ids
        assert _TARGET_FUTURE not in ids  # next_run_at 미래 → due 아님
        assert _TARGET_PAUSED not in ids  # status PAUSED → 제외

    asyncio.run(_run())


@_pg_gate
def test_concurrent_ticks_create_exactly_one_job_real_pg(pg_env):
    from rider_server.scheduler import SchedulerService

    repo, backend, _factory = pg_env

    async def _run():
        svc = SchedulerService()
        r1, r2 = await asyncio.gather(
            svc.run_tick(repo, backend, now=_T0),
            svc.run_tick(repo, backend, now=_T0),
        )
        total = r1.enqueued_count + r2.enqueued_count
        # 두 tick 모두 due target 을 봤어도 conditional UPDATE 가 한 tick 만 통과 → 1 job.
        assert total == 1, f"동시 tick 이 {total} job 생성 — 멱등성 깨짐"

    asyncio.run(_run())


@_pg_gate
def test_active_crawl_job_blocks_reenqueue_real_pg(pg_env):
    from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN
    from rider_server.scheduler import SchedulerService

    repo, backend, _factory = pg_env

    async def _run():
        # due 대상에 이미 활성 CrawlJob(PENDING)을 만든다.
        await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_BAEMIN, target_id=_TARGET_DUE, now=_T0
        )
        assert await repo.has_active_crawl_job(_TARGET_DUE) is True
        result = await SchedulerService().run_tick(repo, backend, now=_T0)
        due_outcome = {o.target_id: o for o in result.outcomes}.get(_TARGET_DUE)
        assert due_outcome is not None
        assert due_outcome.enqueued is False  # 활성 job 있으면 재-enqueue 0(멱등).

    asyncio.run(_run())
