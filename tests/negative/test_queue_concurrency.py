"""Story 5.3 / AC2·AC4 (FR-13, Story 5.10 연계) — PostgreSQL-gated negative/concurrency.

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) ``FOR UPDATE SKIP LOCKED`` 동시 claim → 같은 job 을 두 claim 이 가져가도 정확히 하나만
      success(``double Agent claim`` negative). 나머지는 빈 응답.
  (2) lease 만료 → ``recover_stale`` → 다른 Agent 재할당이 실 PG 에서 동작.
  (3) 재할당된 job 의 옛 소유자 ``complete`` 가 LEASE_LOST(라우트 409) — 이중 success 차단.

**SQLite 로 SKIP LOCKED 를 흉내내지 않는다**(락 의미가 달라 오탐) — 현 WSL/venv 에 Postgres
부재 시 전부 skip 하고 Completion Notes 에 투명 명기한다. in-memory 계약 테스트
(``tests/server/test_queue_backend.py``)가 단일-claim·lease 의미를 항상 잠근다.
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
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). in-memory 계약으로 잠금.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

# 유효 UUID agent_id — PG ``jobs.agent_id`` 는 ``agents.id`` FK + ``Uuid`` 타입(비-UUID 는
# _as_uuid ValueError + FK 위반). 시드한 agents 행과 1:1.
_AGENT_1 = "11111111-1111-1111-1111-111111111111"
_AGENT_2 = "22222222-2222-2222-2222-222222222222"


async def _seed_agents(session_factory, agent_ids) -> None:
    """PG ``agents`` 행을 시드한다(jobs.agent_id FK 충족)."""
    from rider_server.db.models.agent import Agent

    async with session_factory() as session:
        for aid in agent_ids:
            session.add(
                Agent(
                    id=uuid.UUID(aid),
                    name="negative-test-agent",
                    machine_id="test-machine",
                    version="0.0.0",
                    os="linux",
                    status="active",
                    capacity_json={},
                )
            )
        await session.commit()


def _fresh_pg_backend():
    """빈 PG 에 0001+0002 적용 후 PostgresQueueBackend + teardown 을 돌려준다."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.queue import PostgresQueueBackend

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)
    factory = create_session_factory(engine)
    # jobs.agent_id FK 충족 — claim/complete agent UUID 를 미리 시드.
    asyncio.run(_seed_agents(factory, (_AGENT_1, _AGENT_2)))
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

    return backend, _teardown


@pytest.fixture
def pg_backend():
    backend, teardown = _fresh_pg_backend()
    try:
        yield backend
    finally:
        teardown()


@_pg_gate
def test_concurrent_claim_exactly_one_wins_skip_locked(pg_backend):
    from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

    async def _run():
        job_id = await pg_backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        # 두 Agent 가 같은 PENDING job 을 동시에 claim(FOR UPDATE SKIP LOCKED).
        a, b = await asyncio.gather(
            pg_backend.claim(
                agent_id=_AGENT_1,
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=1,
                lease_seconds=120,
                now=_T0,
            ),
            pg_backend.claim(
                agent_id=_AGENT_2,
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=1,
                lease_seconds=120,
                now=_T0,
            ),
        )
        winners = [r for r in (a, b) if r]
        assert len(winners) == 1, "정확히 하나만 claim 해야 한다"
        assert winners[0][0].job_id == job_id

    asyncio.run(_run())


@_pg_gate
def test_lease_expiry_recover_and_reassign_real_pg(pg_backend):
    from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

    async def _run():
        job_id = await pg_backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        first = await pg_backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        assert len(first) == 1
        recovered = await pg_backend.recover_stale(now=_T0 + timedelta(seconds=31))
        assert recovered == 1
        again = await pg_backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=62),
        )
        assert len(again) == 1
        assert again[0].job_id == job_id

    asyncio.run(_run())


@_pg_gate
def test_stale_owner_complete_returns_lease_lost_real_pg(pg_backend):
    from rider_server.queue.backend import COMPLETE_LEASE_LOST
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED, JOB_TYPE_CRAWL_BAEMIN

    async def _run():
        job_id = await pg_backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await pg_backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        await pg_backend.recover_stale(now=_T0 + timedelta(seconds=31))
        await pg_backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=62),
        )
        # 옛 소유자(agent-1)가 뒤늦게 success 보고 → LEASE_LOST(라우트 409)
        outcome = await pg_backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            now=_T0 + timedelta(seconds=63),
        )
        assert outcome.result == COMPLETE_LEASE_LOST

    asyncio.run(_run())
