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

#: 다중 Agent scale 테스트용 Agent 풀(20대) — work-order Task 10 시나리오.
_SCALE_AGENT_COUNT = 20
_SCALE_AGENT_IDS = [f"a{n:03d}0000-0000-0000-0000-000000000000" for n in range(_SCALE_AGENT_COUNT)]


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


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@_pg_gate
def test_recover_stale_does_not_starve_stale_auth_under_healthy_pending_backlog(pg_backend):
    """healthy PENDING backlog 가 batch 를 채워도 만료 AUTH_COUPANG_2FA PENDING 이 cleanup 된다.

    예전엔 LIMIT 을 먼저 걸고 Python 에서 healthy PENDING 을 skip 해, 운영 batch(100)에서 정상
    PENDING 이 batch 를 채우면 만료 auth PENDING 이 영원히 뒤로 밀렸다(검토 High). 이제 PENDING
    후보를 **실제로 만료된**(``expires_at <= now``) 행으로만 SQL 단계에서 좁혀, 정상 PENDING 은
    (expires_at 가 없든, 아직 미래든) batch 를 먹지 않는다.

    여기서는 두 종류의 정상 PENDING 을 batch 보다 많이 섞는다:
      (a) expires_at 없는 PENDING(KAKAO_SEND),
      (b) expires_at 가 **아직 미래**인 scheduled crawl PENDING — 이게 핵심 회귀 케이스다.
    그 뒤에 진짜 만료된 AUTH_COUPANG_2FA 가 작은 batch 로도 닫혀야 한다.
    """
    from rider_server.queue.states import (
        JOB_STATUS_FAILED,
        JOB_STATUS_PENDING,
        JOB_TYPE_AUTH_COUPANG_2FA,
        JOB_TYPE_CRAWL_COUPANG,
        JOB_TYPE_KAKAO_SEND,
        RESULT_REASON_STALE_AUTH_JOB_EXPIRED,
    )

    async def _run():
        # (1) expires_at 없는 healthy PENDING 10개 — KAKAO_SEND.
        for _ in range(10):
            await pg_backend.enqueue(job_type=JOB_TYPE_KAKAO_SEND, now=_T0)
        # (2) expires_at 가 **아직 미래**인 정상 scheduled crawl PENDING 10개 — 회귀 핵심.
        #     (id 정렬상 뒤의 만료 auth job 앞에 와서 batch 를 먹을 수 있는 위치.)
        future = _T0 + timedelta(hours=1)
        for _ in range(10):
            await pg_backend.enqueue(
                job_type=JOB_TYPE_CRAWL_COUPANG,
                payload_json={
                    "job_type": JOB_TYPE_CRAWL_COUPANG,
                    "job_origin": "scheduler",
                    "scheduled_at": _iso(_T0),
                    "expires_at": _iso(future),  # 미래 — 아직 stale 아님
                },
                now=_T0,
            )
        # (3) payload TTL 이 이미 지난 AUTH_COUPANG_2FA PENDING 하나.
        expired = _T0 - timedelta(minutes=1)
        auth_id = await pg_backend.enqueue(
            job_type=JOB_TYPE_AUTH_COUPANG_2FA,
            payload_json={
                "job_type": JOB_TYPE_AUTH_COUPANG_2FA,
                "platform": "coupang",
                "recovery_mode": "coupang_auto_email_2fa",
                "expires_at": _iso(expired),
            },
            now=_T0,
        )

        # 작은 batch(=5)로 recovery — 정상 PENDING 20개가 batch 를 먹으면 auth job 이 안 닫힌다.
        recovered = await pg_backend.recover_stale(now=_T0, batch_size=5)

        assert recovered == 1  # 정상 PENDING 은 후보에서 빠지고 만료 auth 만 닫힌다.
        status, result_json = await _job_status_and_result(pg_backend, auth_id)
        assert status == JOB_STATUS_FAILED
        assert (result_json or {}).get("reason") == RESULT_REASON_STALE_AUTH_JOB_EXPIRED
        # 정상 PENDING 20개는 그대로 보존(닫히지 않음).
        healthy_still_pending = await _count_status(pg_backend, JOB_STATUS_PENDING)
        assert healthy_still_pending == 20

    asyncio.run(_run())


async def _job_status_and_result(backend, job_id: str):
    from sqlalchemy import select
    from rider_server.db.models.agent import Job

    async with backend._session_factory() as session:
        row = (
            await session.execute(
                select(Job.status, Job.result_json).where(Job.id == uuid.UUID(job_id))
            )
        ).first()
    return (str(row.status), row.result_json)


async def _count_status(backend, status: str) -> int:
    from sqlalchemy import func, select
    from rider_server.db.models.agent import Job

    async with backend._session_factory() as session:
        n = (
            await session.execute(
                select(func.count()).select_from(Job).where(Job.status == status)
            )
        ).scalar_one()
    return int(n)


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


# ── (4) 다중 Agent scale 경합 — work-order Task 10(20 Agent × 200 jobs) ──────────


def _scale_pg_backend():
    """20대 Agent 를 시드한 fresh PG backend + teardown(scale 경합 테스트용)."""
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
    asyncio.run(_seed_agents(factory, _SCALE_AGENT_IDS))
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
def scale_pg_backend():
    backend, teardown = _scale_pg_backend()
    try:
        yield backend
    finally:
        teardown()


@_pg_gate
def test_many_agents_claim_200_jobs_exactly_once_with_affinity(scale_pg_backend):
    """20 Agent 가 200 PENDING job 을 동시에 claim 해도 (1) 같은 job 이 둘 이상에게
    claim 되지 않고 (2) affinity 가 있는 job 은 지정 Agent 만 가져간다(잘못된 claim 0)."""
    from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

    backend = scale_pg_backend
    total_jobs = 200
    # 매 10번째 job 은 특정 Agent 에 affinity 고정(나머지는 free).
    affinity_by_job: dict[str, str] = {}

    async def _run():
        # ── enqueue 200 ──
        for i in range(total_jobs):
            assigned = _SCALE_AGENT_IDS[i % _SCALE_AGENT_COUNT] if i % 10 == 0 else None
            job_id = await backend.enqueue(
                job_type=JOB_TYPE_CRAWL_BAEMIN,
                assigned_agent_id=assigned,
                now=_T0,
            )
            if assigned is not None:
                affinity_by_job[job_id] = assigned

        # ── 20 Agent 동시 claim(각자 넉넉한 max_jobs 로 경합 유발) ──
        async def _claim(agent_id: str):
            out: list[str] = []
            # 큐가 빌 때까지 반복 claim(한 round 로는 200개를 못 비울 수 있음).
            while True:
                rows = await backend.claim(
                    agent_id=agent_id,
                    capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                    max_jobs=15,
                    lease_seconds=300,
                    now=_T0,
                )
                if not rows:
                    break
                out.extend(r.job_id for r in rows)
            return agent_id, out

        results = await asyncio.gather(*(_claim(a) for a in _SCALE_AGENT_IDS))

        # (1) exactly-once: 어떤 job 도 두 Agent 가 동시에 갖지 않는다.
        claimed_by_agent: dict[str, list[str]] = {a: jobs for a, jobs in results}
        all_claimed = [jid for jobs in claimed_by_agent.values() for jid in jobs]
        assert len(all_claimed) == len(set(all_claimed)), "같은 job 이 둘 이상에게 claim 됐다"

        # (2) affinity 위반 0: 고정된 job 은 지정 Agent 만 claim.
        for agent_id, jobs in claimed_by_agent.items():
            for jid in jobs:
                owner = affinity_by_job.get(jid)
                if owner is not None:
                    assert owner == agent_id, "affinity 가 다른 Agent 가 job 을 claim 했다"

        # free job 은 전부 소진(affinity job 은 그 Agent 가 claim round 에 참여했으니 함께 소진).
        assert len(set(all_claimed)) == total_jobs, "모든 job 이 정확히 한 번 claim 돼야 한다"

    asyncio.run(_run())


@_pg_gate
def test_concurrent_recovery_and_claim_never_double_succeeds(scale_pg_backend):
    """stale recovery 와 동시 claim 이 경합해도 한 job 의 terminal success 는 1회뿐이다
    (옛 소유자 complete 는 LEASE_LOST). work-order 완료기준: terminal 이중 success 0."""
    from rider_server.queue.backend import COMPLETE_ACCEPTED, COMPLETE_LEASE_LOST
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED, JOB_TYPE_CRAWL_BAEMIN

    backend = scale_pg_backend

    async def _run():
        # 50개 job 을 첫 Agent 가 짧은 lease 로 claim → lease 만료 → recovery 와 재claim 경합.
        job_ids: list[str] = []
        for _ in range(50):
            job_ids.append(
                await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
            )
        first = await backend.claim(
            agent_id=_SCALE_AGENT_IDS[0],
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=50,
            lease_seconds=30,
            now=_T0,
        )
        assert len(first) == 50
        later = _T0 + timedelta(seconds=31)

        # recovery 와 두 번째 Agent 의 재claim 을 동시에 — lock 경합 하에서.
        async def _recover():
            return await backend.recover_stale(now=later)

        async def _reclaim():
            return await backend.claim(
                agent_id=_SCALE_AGENT_IDS[1],
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=50,
                lease_seconds=300,
                now=_T0 + timedelta(seconds=62),
            )

        await asyncio.gather(_recover(), _reclaim())

        # 옛 소유자(agent-0)의 뒤늦은 success 는 전부 LEASE_LOST 여야 한다.
        succeeded = 0
        for jid in job_ids:
            outcome = await backend.complete(
                job_id=jid,
                agent_id=_SCALE_AGENT_IDS[0],
                status=JOB_STATUS_SUCCEEDED,
                now=_T0 + timedelta(seconds=70),
            )
            # 재할당됐으면 LEASE_LOST, 아직 agent-0 소유면 ACCEPTED — 둘 다 terminal 1회 의미 유지.
            assert outcome.result in (COMPLETE_LEASE_LOST, COMPLETE_ACCEPTED)
            if outcome.result == COMPLETE_ACCEPTED:
                succeeded += 1
        # 재claim 한 job 은 agent-0 이 success 로 만들 수 없다(이중 success 차단).
        assert succeeded < 50, "재할당된 job 을 옛 소유자가 success 처리하면 안 된다"

    asyncio.run(_run())
