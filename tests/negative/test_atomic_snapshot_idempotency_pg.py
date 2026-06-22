"""Atomic snapshot complete 의 completion_id 멱등성(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) 같은 ``completion_id`` + 같은 hash 로 재시도하면 이미 terminal 인 job 을 LEASE_LOST/
      conflict 가 아니라 이전 성공과 같은 ACCEPTED 로 돌려준다(outbox replay/클라 재시도 안전).
  (2) 멱등 재시도는 Snapshot/Message 를 중복 INSERT 하지 않는다(결정적 PK 충돌 회피).
  (3) 다른 ``completion_id`` 또는 다른 hash 는 LEASE_LOST(다른 payload/소유자 차단).

기존 atomic complete 경로는 ``completion_id`` 를 무시해 crawl 성공(snapshot) 재전송 시
LEASE_LOST 로 잘못 처리됐다. 이 테스트가 그 회귀를 잠근다.
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
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재. atomic 멱등 의미는 PG 로만 잠근다.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ACCOUNT = "a1111111-1111-1111-1111-111111111111"
_TARGET = "11111111-1111-1111-1111-111111111111"
_AGENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_JOB = "d0000000-0000-0000-0000-000000000001"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import MonitoringTarget, PlatformAccount
    from rider_server.db.models.agent import Agent, Job
    from rider_server.db.models.tenancy import Subscription, Tenant

    async with session_factory() as session:
        session.add(Tenant(id=uuid.UUID(_TENANT), name="t", status="ACTIVE", created_at=_T0))
        await session.flush()
        session.add(
            Subscription(
                id=uuid.uuid4(), tenant_id=uuid.UUID(_TENANT),
                plan="basic", status="PAYMENT_ACTIVE", quotas={},
            )
        )
        session.add(
            PlatformAccount(
                id=uuid.UUID(_ACCOUNT), tenant_id=uuid.UUID(_TENANT), platform="BAEMIN",
                label="l", username="vault://u", password="vault://p", auth_state="ACTIVE",
            )
        )
        session.add(
            Agent(
                id=uuid.UUID(_AGENT), name="agent-1", machine_id="m", version="1.0.0",
                os="windows", status="active", capacity_json={},
            )
        )
        await session.flush()
        session.add(
            MonitoringTarget(
                id=uuid.UUID(_TARGET), tenant_id=uuid.UUID(_TENANT),
                platform_account_id=uuid.UUID(_ACCOUNT), name="대상", center_name="c",
                external_id="", url="", interval_minutes=10, status="ACTIVE", next_run_at=None,
            )
        )
        await session.flush()
        # claim 된(CLAIMED, 유효 lease) crawl job. payload 에 server-owned scope 를 둔다.
        session.add(
            Job(
                id=uuid.UUID(_JOB), type="CRAWL_BAEMIN", target_id=uuid.UUID(_TARGET),
                agent_id=uuid.UUID(_AGENT), status="CLAIMED",
                claimed_at=_T0, lease_expires_at=_T0 + timedelta(seconds=300),
                attempts=1,
                payload_json={
                    "tenant_id": _TENANT,
                    "platform": "BAEMIN",
                    "platform_account_id": _ACCOUNT,
                },
            )
        )
        await session.commit()


def _record():
    from rider_server.services.job_result_ingest_service import SnapshotIngestRecord

    return SnapshotIngestRecord(
        job_id=_JOB,
        agent_id=_AGENT,
        target_id=_TARGET,
        tenant_id=_TENANT,
        platform="baemin",
        platform_account_id=_ACCOUNT,
        collected_at=_T0,
        parser_version="baemin-v1",
        quality_state="OK",
        normalized_json={"center_name": "배민센터A", "completed_count": 102},
        artifact_refs=[],
        completed_at=_T0,
    )


def _fresh_pg():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.services.snapshot_repository_postgres import (
        PostgresSnapshotIngestRepository,
    )

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
    repo = PostgresSnapshotIngestRepository(factory)

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return repo, factory, _teardown


@pytest.fixture
def pg_repo():
    repo, factory, teardown = _fresh_pg()
    try:
        yield repo, factory
    finally:
        teardown()


async def _count(factory, model_cls) -> int:
    from sqlalchemy import func, select

    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model_cls))).scalar_one())


@_pg_gate
def test_same_completion_id_replays_as_accepted_without_duplicate_inserts(pg_repo) -> None:
    from rider_server.db.models.messaging import Message as MessageRow, Snapshot as SnapshotRow
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory = pg_repo

    async def _run():
        first = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="c1", completion_payload_hash="h1",
            now=_T0,
        )
        assert first.result == COMPLETE_ACCEPTED
        assert first.final_status == JOB_STATUS_SUCCEEDED
        snaps_after_first = await _count(factory, SnapshotRow)
        msgs_after_first = await _count(factory, MessageRow)
        assert snaps_after_first == 1 and msgs_after_first == 1

        # 같은 completion_id + 같은 hash 재시도(outbox replay/클라 재시도) → 멱등 ACCEPTED.
        second = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="c1", completion_payload_hash="h1",
            now=_T0 + timedelta(seconds=5),
        )
        assert second.result == COMPLETE_ACCEPTED
        assert second.final_status == JOB_STATUS_SUCCEEDED
        # Snapshot/Message 중복 INSERT 없음(결정적 PK 충돌 회피).
        assert await _count(factory, SnapshotRow) == 1
        assert await _count(factory, MessageRow) == 1

    asyncio.run(_run())


@_pg_gate
def test_different_completion_id_after_terminal_is_lease_lost(pg_repo) -> None:
    from rider_server.queue import COMPLETE_ACCEPTED, COMPLETE_LEASE_LOST
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory = pg_repo

    async def _run():
        first = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="c1", completion_payload_hash="h1",
            now=_T0,
        )
        assert first.result == COMPLETE_ACCEPTED

        # 다른 completion_id → terminal job 재완료 시도 차단(LEASE_LOST).
        other_id = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="c2", completion_payload_hash="h1",
            now=_T0 + timedelta(seconds=5),
        )
        assert other_id.result == COMPLETE_LEASE_LOST

        # 같은 completion_id 지만 다른 hash → 다른 payload 로 보고 LEASE_LOST.
        other_hash = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="c1", completion_payload_hash="h2",
            now=_T0 + timedelta(seconds=6),
        )
        assert other_hash.result == COMPLETE_LEASE_LOST

    asyncio.run(_run())
