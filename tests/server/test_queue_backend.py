"""Story 5.3 / AC1·AC2·AC3 — backend-중립 QueueBackend 계약 suite.

같은 테스트를 in-memory 구현(항상 실행, DB-less)과 PostgreSQL 구현(``TEST_DATABASE_URL``
있을 때만 추가)에 **동일하게** 통과시켜 "구현을 Redis/SQS 로 옮길 수 있음"(P4-05)을 잠근다 —
인터페이스가 PG 세부에 새지 않음을 보장하는 1차 가드.

``pytest-asyncio`` 미도입(dep 동결)이라 ``asyncio.run`` 으로 async backend 를 구동한다(5.1
``test_server_async_e2e.py`` 패턴). PG 구현은 실제 lock 의미(SKIP LOCKED)를 ``tests/negative/``
에서 동시성으로 검증하고, 여기서는 단일-claim "의미"·lease·상태 전이의 계약을 잠근다.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from rider_server.db.models.audit import AuditLog
from rider_server.domain import AuditResult, BaeminAuthState, FailureCategory
from rider_server.queue import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_SUCCEEDED,
    InMemoryQueueBackend,
    InvalidJobTransition,
    PostgresQueueBackend,
)
from rider_server.queue.backend import COMPLETE_ACCEPTED, COMPLETE_LEASE_LOST
from rider_server.queue.retry import default_retry_decider
from rider_server.queue.states import (
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPE_KAKAO_SEND,
)
from rider_server.queue.postgres_queue import (
    COUPANG_AUTO_RECOVERY_COOLDOWN,
    _platform_account_auth_update,
    coupang_recovery_state_values,
    kakao_delivery_log_values,
)

_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

# 유효 UUID agent_id — PG ``jobs.agent_id`` 는 ``agents.id`` FK + ``Uuid`` 타입이라
# 비-UUID 문자열은 ``_as_uuid`` ValueError + FK 위반을 일으킨다. in-memory 는 임의 문자열도
# 수용하므로 UUID 형으로 통일해 backend-중립(양쪽 동일 통과)을 실제로 보장한다.
_AGENT_1 = "11111111-1111-1111-1111-111111111111"
_AGENT_2 = "22222222-2222-2222-2222-222222222222"


# ── backend factory parametrize: in-memory 항상, PostgreSQL 은 env 있을 때만 ──────


def _memory_factory():
    return InMemoryQueueBackend(), (lambda: None)


_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_param = pytest.param(
    "postgres",
    marks=pytest.mark.skipif(
        not _TEST_DB_URL,
        reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). in-memory 로 계약 잠금.",
    ),
)


def _make_backend(kind: str):
    """(backend, teardown) 을 돌려준다. postgres 는 빈 DB 에 0001+0002 적용 후 정리."""
    if kind == "memory":
        return _memory_factory()
    return _make_pg_backend()


async def _seed_agents(session_factory, agent_ids) -> None:
    """PG ``agents`` 행을 시드한다(jobs.agent_id FK 충족). in-memory 경로는 호출하지 않는다."""
    from rider_server.db.models.agent import Agent

    async with session_factory() as session:
        for aid in agent_ids:
            session.add(
                Agent(
                    id=uuid.UUID(aid),
                    name="contract-test-agent",
                    machine_id="test-machine",
                    version="0.0.0",
                    os="linux",
                    status="active",
                    capacity_json={},
                )
            )
        await session.commit()


def _make_pg_backend():
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.queue import PostgresQueueBackend

    repo_root = Path(__file__).resolve().parents[2]
    cfg = Config()
    cfg.set_main_option("script_location", str(repo_root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)
    factory = create_session_factory(engine)
    # jobs.agent_id 는 agents.id FK — claim/complete 가 쓰는 agent UUID 를 미리 시드해
    # FK 위반 없이 계약 suite 가 실 PG 에서도 통과하게 한다.
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


class _FakeAuditSession:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.committed = True


class _FakeAuditSessionFactory:
    def __init__(self) -> None:
        self.session = _FakeAuditSession()

    def __call__(self):
        return self.session


_BACKENDS = ["memory", _pg_param]


@pytest.fixture(params=_BACKENDS)
def backend(request):
    be, teardown = _make_backend(request.param)
    try:
        yield be
    finally:
        teardown()


# ── (a) 계약: enqueue→claim→complete 해피패스 ────────────────────────────────────


def test_enqueue_claim_complete_happy_path(backend):
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        records = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert len(records) == 1
        r = records[0]
        assert r.job_id == job_id
        assert r.type == JOB_TYPE_CRAWL_BAEMIN
        assert r.status == JOB_STATUS_CLAIMED
        assert r.lease_expires_at == _T0 + timedelta(seconds=120)

        outcome = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            result_json={"ok": True},
            now=_T0 + timedelta(seconds=1),
        )
        assert outcome.result == COMPLETE_ACCEPTED
        assert outcome.final_status == JOB_STATUS_SUCCEEDED

    asyncio.run(_run())


def test_claim_empty_queue_returns_empty(backend):
    async def _run():
        records = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert records == []

    asyncio.run(_run())


def test_claim_skips_capability_mismatch(backend):
    async def _run():
        await backend.enqueue(job_type=JOB_TYPE_KAKAO_SEND, now=_T0)
        records = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],  # KAKAO_SEND 매칭 안 됨
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert records == []

    asyncio.run(_run())


def test_claim_respects_run_after(backend):
    async def _run():
        await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_BAEMIN,
            run_after=_T0 + timedelta(seconds=60),
            now=_T0,
        )
        # run_after 미도래 → claim 안 됨
        early = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert early == []
        # run_after 도래 → claim 됨
        late = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=5,
            lease_seconds=120,
            now=_T0 + timedelta(seconds=61),
        )
        assert len(late) == 1

    asyncio.run(_run())


def test_kakao_delivery_log_values_map_complete_status() -> None:
    sent = kakao_delivery_log_values(
        job_status=JOB_STATUS_SUCCEEDED,
        error_code=None,
        now=_T0,
    )
    failed = kakao_delivery_log_values(
        job_status=JOB_STATUS_FAILED,
        error_code=None,
        now=_T0,
    )

    assert sent == {"status": "SENT", "error_code": None, "sent_at": _T0}
    # 실패는 sent_at 없이 last_failed_at 에 실패 시각을 남긴다 — 대시보드 최신-실패 집계가 이
    # 실패를 "시각 없음"으로 보고 무시하지 않게(오래된 실패 코드가 카드에 굳는 회귀 차단).
    assert failed == {
        "status": "FAILED",
        "error_code": "KAKAO_FAILURE",
        "sent_at": None,
        "last_failed_at": _T0,
    }


def test_postgres_emit_event_records_agent_audit_log() -> None:
    factory = _FakeAuditSessionFactory()
    backend = PostgresQueueBackend(factory)  # type: ignore[arg-type]
    job_id = "33333333-3333-3333-3333-333333333333"

    asyncio.run(
        backend.emit_event(
            job_id=job_id,
            agent_id=_AGENT_1,
            event_type="JOB_STARTED",
            severity="info",
            message_redacted="started token=raw-secret-123456",
            artifact_refs=["artifact:agent-event-1"],
            now=_T0,
        )
    )

    assert factory.session.committed is True
    assert len(factory.session.added) == 1
    audit = factory.session.added[0]
    assert isinstance(audit, AuditLog)
    assert audit.actor_id == uuid.UUID(_AGENT_1)
    assert audit.action == "JOB_STARTED"
    assert audit.source == "AGENT"
    assert audit.target_type == "JOB"
    assert audit.target_id == uuid.UUID(job_id)
    assert audit.result == AuditResult.SUCCESS.value
    assert audit.created_at == _T0
    blob = json.dumps(audit.diff_redacted, ensure_ascii=False)
    assert "raw-secret-123456" not in blob
    assert audit.diff_redacted["severity"] == "info"
    assert audit.diff_redacted["artifact_refs"] == ["artifact:agent-event-1"]


# ── (a) 계약: lease 만료 → recover_stale → 재claim 가능 ───────────────────────────


def test_lease_expiry_recover_and_reclaim(backend):
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        first = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        assert len(first) == 1

        # lease 만료 전: 재claim 안 됨(다른 Agent)
        none_yet = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=10),
        )
        assert none_yet == []

        # lease 만료 후 recover_stale → PENDING 재진입하되 backoff 전에는 재claim 안 됨.
        recovered = await backend.recover_stale(now=_T0 + timedelta(seconds=31))
        assert recovered == 1

        early = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=32),
        )
        assert early == []

        # backoff 가 지난 뒤 다른 Agent 가 재claim 가능
        again = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=62),
        )
        assert len(again) == 1
        assert again[0].job_id == job_id

    asyncio.run(_run())


def test_stale_owner_complete_is_lease_lost(backend):
    # 재할당된 job 의 옛 소유자 complete 는 LEASE_LOST(409 매핑) — 이중 success 차단(AC2).
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        await backend.recover_stale(now=_T0 + timedelta(seconds=31))
        await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=62),
        )
        # 옛 소유자(agent-1)가 뒤늦게 success 보고 → 거부
        outcome = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            now=_T0 + timedelta(seconds=63),
        )
        assert outcome.result == COMPLETE_LEASE_LOST

    asyncio.run(_run())


def test_transient_crawl_failure_reenters_pending_with_backoff(backend):
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        outcome = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_FAILED,
            error_code=FailureCategory.CRAWL_FAILURE.value,
            now=_T0 + timedelta(seconds=5),
        )

        assert outcome.accepted is True
        assert outcome.final_status == JOB_STATUS_PENDING
        early = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0 + timedelta(seconds=30),
        )
        assert early == []
        [again] = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0 + timedelta(seconds=36),
        )
        assert again.job_id == job_id
        assert again.attempts == 1

    asyncio.run(_run())


@pytest.mark.parametrize(
    "error_code",
    ["CRAWL_TIMEOUT", "CDP_UNREACHABLE", "PROFILE_UNAVAILABLE", "PARSER_MISSING_DATA"],
)
def test_worker_transient_crawl_error_codes_retry_with_backoff(error_code: str):
    decision = default_retry_decider(error_code, 1, _T0)

    assert decision is not None
    assert decision.run_after == _T0 + timedelta(seconds=30)


@pytest.mark.parametrize(
    "error_code",
    [
        FailureCategory.AUTH_REQUIRED.value,
        FailureCategory.TARGET_VALIDATION_FAILURE.value,
        "SECRET_REF_UNRESOLVED",
        "PLAINTEXT_SECRET_NOT_ALLOWED",
    ],
)
def test_human_intervention_failures_do_not_retry(backend, error_code: str):
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        outcome = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_FAILED,
            error_code=error_code,
            now=_T0 + timedelta(seconds=5),
        )

        assert outcome.accepted is True
        assert outcome.final_status == JOB_STATUS_FAILED
        retry = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0 + timedelta(hours=1),
        )
        assert retry == []

    asyncio.run(_run())


def test_stale_recovery_records_attempt_and_backoff_in_memory():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )

        recovered = await backend.recover_stale(now=_T0 + timedelta(seconds=31))

        assert recovered == 1
        snapshot = backend.job_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.status == JOB_STATUS_PENDING
        assert snapshot.attempts == 1
        assert snapshot.error_code == "CRAWL_TIMEOUT"
        assert snapshot.last_failed_at == _T0 + timedelta(seconds=31)
        assert snapshot.run_after == _T0 + timedelta(seconds=61)

        early = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=32),
        )
        assert early == []

    asyncio.run(_run())


def test_stale_recovery_stops_after_max_attempts_in_memory():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)

        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        await backend.recover_stale(now=_T0 + timedelta(seconds=31))

        await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=62),
        )
        await backend.recover_stale(now=_T0 + timedelta(seconds=93))

        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(seconds=154),
        )
        recovered = await backend.recover_stale(now=_T0 + timedelta(seconds=185))

        assert recovered == 1
        snapshot = backend.job_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.status == JOB_STATUS_FAILED
        assert snapshot.attempts == 3
        assert snapshot.error_code == "CRAWL_TIMEOUT"
        assert snapshot.last_failed_at == _T0 + timedelta(seconds=185)
        assert snapshot.completed_at == _T0 + timedelta(seconds=185)
        retry = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0 + timedelta(minutes=30),
        )
        assert retry == []

    asyncio.run(_run())


def test_complete_persists_completion_metadata_in_memory():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            result_json={"ok": True},
            duration_ms=1234,
            result_schema_version="crawl-result.v1",
            now=_T0 + timedelta(seconds=2),
        )

        snapshot = backend.job_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.completed_at == _T0 + timedelta(seconds=2)
        assert snapshot.duration_ms == 1234
        assert snapshot.result_schema_version == "crawl-result.v1"

    asyncio.run(_run())


def test_retry_failure_records_last_failed_at_in_memory():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_FAILED,
            error_code=FailureCategory.CRAWL_FAILURE.value,
            now=_T0 + timedelta(seconds=5),
        )

        snapshot = backend.job_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.status == JOB_STATUS_PENDING
        assert snapshot.run_after == _T0 + timedelta(seconds=35)
        assert snapshot.last_failed_at == _T0 + timedelta(seconds=5)
        assert snapshot.last_failed_at != snapshot.run_after

    asyncio.run(_run())


def test_terminal_failure_records_last_failed_at_in_memory():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_FAILED,
            error_code=FailureCategory.AUTH_REQUIRED.value,
            now=_T0 + timedelta(seconds=7),
        )

        snapshot = backend.job_snapshot(job_id)
        assert snapshot is not None
        assert snapshot.status == JOB_STATUS_FAILED
        assert snapshot.last_failed_at == _T0 + timedelta(seconds=7)

    asyncio.run(_run())


# ── (a) 계약: 미정의 상태 전이 거부 ──────────────────────────────────────────────


def test_undefined_transition_rejected(backend):
    # SUCCEEDED 는 터미널 — 완료된 job 을 다시 complete 하면 LEASE_LOST(소유 끝) 또는 전이 거부.
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )
        first = await backend.complete(
            job_id=job_id, agent_id=_AGENT_1, status=JOB_STATUS_SUCCEEDED, now=_T0
        )
        assert first.result == COMPLETE_ACCEPTED
        # 같은 job 두 번째 complete → 더는 진행 중이 아니므로 LEASE_LOST(이중 기록 차단)
        second = await backend.complete(
            job_id=job_id, agent_id=_AGENT_1, status=JOB_STATUS_FAILED, now=_T0
        )
        assert second.result == COMPLETE_LEASE_LOST

    asyncio.run(_run())


def test_transition_table_rejects_pending_to_succeeded():
    # 상태머신 단위 — PENDING→SUCCEEDED 같은 미정의 전이는 예외(backend 무관, 순수 함수).
    from rider_server.queue.states import assert_transition

    assert_transition(JOB_STATUS_PENDING, JOB_STATUS_CLAIMED)  # 정의됨 → 통과
    with pytest.raises(InvalidJobTransition):
        assert_transition(JOB_STATUS_PENDING, JOB_STATUS_SUCCEEDED)


def test_postgres_account_auth_update_maps_auth_state_and_center_mismatch():
    job = SimpleNamespace(
        payload_json={"platform_account_id": "33333333-3333-3333-3333-333333333333"},
        result_json={"auth_state": BaeminAuthState.USER_ACTION_PENDING.value},
    )
    assert _platform_account_auth_update(job, None) == (
        "33333333-3333-3333-3333-333333333333",
        BaeminAuthState.USER_ACTION_PENDING.value,
    )

    mismatch_job = SimpleNamespace(
        payload_json={"platform_account_id": "33333333-3333-3333-3333-333333333333"},
        result_json={"mismatch": BaeminAuthState.CENTER_MISMATCH.value},
    )
    assert _platform_account_auth_update(
        mismatch_job, FailureCategory.TARGET_VALIDATION_FAILURE.value
    ) == (
        "33333333-3333-3333-3333-333333333333",
        BaeminAuthState.CENTER_MISMATCH.value,
    )

    active_job = SimpleNamespace(
        payload_json={"platform_account_id": "33333333-3333-3333-3333-333333333333"},
        result_json={"auth_state": BaeminAuthState.ACTIVE.value},
    )
    assert _platform_account_auth_update(active_job, None) == (
        "33333333-3333-3333-3333-333333333333",
        BaeminAuthState.ACTIVE.value,
    )

    unknown_job = SimpleNamespace(
        payload_json={"platform_account_id": "33333333-3333-3333-3333-333333333333"},
        result_json={"auth_state": BaeminAuthState.UNKNOWN.value},
    )
    assert _platform_account_auth_update(unknown_job, None) == (
        "33333333-3333-3333-3333-333333333333",
        BaeminAuthState.UNKNOWN.value,
    )

    verified_job = SimpleNamespace(
        payload_json={"platform_account_id": "33333333-3333-3333-3333-333333333333"},
        result_json={"auth_state": BaeminAuthState.AUTH_VERIFIED.value},
    )
    assert _platform_account_auth_update(verified_job, None) == (
        "33333333-3333-3333-3333-333333333333",
        BaeminAuthState.AUTH_VERIFIED.value,
    )


# ── Task 4: Coupang 자동 복구 cooldown 영속(순수 함수, 항상 실행) ─────────────────


_PA_ID = "33333333-3333-3333-3333-333333333333"


def test_coupang_auto_recovery_failure_sets_cooldown_on_account() -> None:
    """Failed auto recovery suppresses future scheduler attempts."""

    job = SimpleNamespace(
        type=JOB_TYPE_CRAWL_COUPANG,
        payload_json={
            "platform_account_id": _PA_ID,
            "recovery_mode": "coupang_auto_email_2fa",
        },
        result_json={"target_id": "mt-1", "auth_state": BaeminAuthState.AUTH_REQUIRED.value},
    )

    update = coupang_recovery_state_values(job=job, status=JOB_STATUS_FAILED, now=_T0)

    assert update is not None
    account_id, values = update
    assert account_id == _PA_ID
    assert values["auto_recovery_failed_at"] == _T0
    assert values["auto_recovery_cooldown_until"] == _T0 + COUPANG_AUTO_RECOVERY_COOLDOWN
    assert values["auto_recovery_attempted_at"] == _T0


def test_coupang_auto_recovery_success_clears_cooldown_on_account() -> None:
    """Successful recovery returns account to normal crawl scheduling."""

    job = SimpleNamespace(
        type=JOB_TYPE_CRAWL_COUPANG,
        payload_json={
            "platform_account_id": _PA_ID,
            "recovery_mode": "coupang_auto_email_2fa",
        },
        result_json={"target_id": "mt-1", "auth_state": BaeminAuthState.AUTH_VERIFIED.value},
    )

    update = coupang_recovery_state_values(job=job, status=JOB_STATUS_SUCCEEDED, now=_T0)

    assert update is not None
    account_id, values = update
    assert account_id == _PA_ID
    assert values["auto_recovery_cooldown_until"] is None
    assert values["auto_recovery_failed_at"] is None


def test_non_recovery_job_does_not_touch_cooldown() -> None:
    """A normal (non-recovery) crawl completion leaves cooldown columns alone."""

    job = SimpleNamespace(
        type=JOB_TYPE_CRAWL_COUPANG,
        payload_json={"platform_account_id": _PA_ID},
        result_json={"target_id": "mt-1"},
    )
    assert coupang_recovery_state_values(job=job, status=JOB_STATUS_FAILED, now=_T0) is None
    assert coupang_recovery_state_values(job=job, status=JOB_STATUS_SUCCEEDED, now=_T0) is None


# ── Task 6: AUTH_COUPANG_2FA result → 계정 coarse gate + cooldown(순수 함수, 항상 실행) ──


def _auth_2fa_completed_job(*, auth_state, auth_recovery_state, reason=None):
    result = {
        "target_id": "mt-1",
        "platform": "coupang",
        "platform_account_id": _PA_ID,
        "auth_state": auth_state,
        "auth_recovery_state": auth_recovery_state,
        "recovery_mode": "coupang_auto_email_2fa",
    }
    if reason is not None:
        result["reason"] = reason
    return SimpleNamespace(
        type="AUTH_COUPANG_2FA",
        payload_json={
            "platform_account_id": _PA_ID,
            "recovery_mode": "coupang_auto_email_2fa",
        },
        result_json=result,
    )


def test_auth_coupang_2fa_success_marks_account_active() -> None:
    """AUTH_COUPANG_2FA success moves account to ACTIVE and clears cooldown."""
    job = _auth_2fa_completed_job(
        auth_state=BaeminAuthState.ACTIVE.value, auth_recovery_state="ACTIVE"
    )

    assert _platform_account_auth_update(job, None) == (_PA_ID, BaeminAuthState.ACTIVE.value)

    cooldown = coupang_recovery_state_values(job=job, status=JOB_STATUS_SUCCEEDED, now=_T0)
    assert cooldown is not None
    _account, values = cooldown
    assert values["auto_recovery_cooldown_until"] is None
    assert values["auto_recovery_failed_at"] is None


def test_auth_coupang_2fa_email_auth_required_keeps_account_auth_required_with_detail() -> None:
    """Detailed Coupang recovery state is preserved without inventing retry."""
    job = _auth_2fa_completed_job(
        auth_state=BaeminAuthState.AUTH_REQUIRED.value,
        auth_recovery_state="EMAIL_AUTH_REQUIRED",
        reason="email_auth_required",
    )

    # 계정 coarse gate 는 AUTH_REQUIRED, 세부 상태는 result_json 에 보존된다(드롭 금지).
    assert _platform_account_auth_update(job, "AUTH_REQUIRED") == (
        _PA_ID,
        BaeminAuthState.AUTH_REQUIRED.value,
    )
    assert job.result_json["auth_recovery_state"] == "EMAIL_AUTH_REQUIRED"
    assert job.result_json["reason"] == "email_auth_required"

    # 실패는 cooldown 을 켠다(즉시 재시도 폭주 방지).
    cooldown = coupang_recovery_state_values(job=job, status=JOB_STATUS_FAILED, now=_T0)
    assert cooldown is not None
    _account, values = cooldown
    assert values["auto_recovery_cooldown_until"] == _T0 + COUPANG_AUTO_RECOVERY_COOLDOWN


def test_auth_coupang_2fa_user_action_required_marks_account_user_action_pending() -> None:
    """Manual intervention states are visible to scheduler/dashboard."""
    job = _auth_2fa_completed_job(
        auth_state=BaeminAuthState.USER_ACTION_PENDING.value,
        auth_recovery_state="USER_ACTION_REQUIRED",
        reason="captcha_or_abnormal_login",
    )

    assert _platform_account_auth_update(job, "AUTH_REQUIRED") == (
        _PA_ID,
        BaeminAuthState.USER_ACTION_PENDING.value,
    )
    assert job.result_json["auth_recovery_state"] == "USER_ACTION_REQUIRED"


def test_auth_coupang_2fa_detail_only_falls_back_to_gate_mapping() -> None:
    """If only auth_recovery_state is present, gate state is derived deterministically."""
    # coarse auth_state 가 없고 세부 상태만 온 경우의 결정적 fallback.
    job = SimpleNamespace(
        payload_json={"platform_account_id": _PA_ID, "recovery_mode": "coupang_auto_email_2fa"},
        result_json={
            "platform_account_id": _PA_ID,
            "auth_recovery_state": "RECOVERY_FAILED",
            "reason": "verification_mail_delayed",
        },
    )
    assert _platform_account_auth_update(job, "AUTH_REQUIRED") == (
        _PA_ID,
        BaeminAuthState.AUTH_REQUIRED.value,
    )


# ── (b) 단일-claim / exactly-once (in-memory, 항상 실행) ──────────────────────────


def test_exactly_one_claim_in_memory():
    # 같은 PENDING job 에 두 claim 요청 → 정확히 하나만 받고 다른 하나는 빈 응답.
    async def _run():
        be = InMemoryQueueBackend()
        job_id = await be.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        results = await asyncio.gather(
            be.claim(
                agent_id=_AGENT_1,
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=1,
                lease_seconds=120,
                now=_T0,
            ),
            be.claim(
                agent_id=_AGENT_2,
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=1,
                lease_seconds=120,
                now=_T0,
            ),
        )
        winners = [r for r in results if r]
        losers = [r for r in results if not r]
        assert len(winners) == 1
        assert len(losers) == 1
        assert winners[0][0].job_id == job_id

    asyncio.run(_run())


def test_extend_lease_in_memory():
    async def _run():
        be = InMemoryQueueBackend()
        job_id = await be.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await be.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        # 연장 → 만료 시각이 뒤로 밀려 recover_stale 가 회수하지 않음
        ok = await be.extend_lease(
            job_id=job_id, agent_id=_AGENT_1, lease_seconds=120, now=_T0 + timedelta(seconds=10)
        )
        assert ok is True
        recovered = await be.recover_stale(now=_T0 + timedelta(seconds=31))
        assert recovered == 0  # 연장됐으니 아직 안 만료
        assert be.job_status(job_id) == JOB_STATUS_CLAIMED

    asyncio.run(_run())


# ── (QA gap C) 계약: claim 은 max_jobs 한도를 지킨다 ──────────────────────────────


def test_claim_respects_max_jobs_limit(backend):
    # enqueue 3건(매칭) → max_jobs=2 claim 시 정확히 2건만, 나머지 1건은 PENDING 으로 남아 재claim 가능.
    async def _run():
        for _ in range(3):
            await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        first = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=2,
            lease_seconds=120,
            now=_T0,
        )
        assert len(first) == 2
        # 남은 1건은 여전히 다른 Agent 가 claim 가능(누락/중복 없음)
        rest = await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert len(rest) == 1
        claimed_ids = {r.job_id for r in first} | {r.job_id for r in rest}
        assert len(claimed_ids) == 3  # 세 job 이 서로 겹치지 않게 분배됨

    asyncio.run(_run())


def test_claim_returns_enqueued_payload(backend):
    async def _run():
        target_id = "33333333-3333-3333-3333-333333333333"
        job_id = await backend.enqueue(
            job_type=JOB_TYPE_CRAWL_BAEMIN,
            payload_json={
                "target_id": target_id,
                "platform": "baemin",
                "primary_url": "https://example.invalid/performance",
            },
            now=_T0,
        )
        [record] = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )
        assert record.job_id == job_id
        assert record.payload_json == {
            "target_id": target_id,
            "platform": "baemin",
            "primary_url": "https://example.invalid/performance",
        }

    asyncio.run(_run())


def test_duplicate_complete_same_id_returns_accepted(backend):
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        first = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            result_json={"ok": True},
            completion_id="completion-1",
            completion_payload_hash="same-payload",
            now=_T0 + timedelta(seconds=1),
        )
        second = await backend.complete(
            job_id=job_id,
            agent_id=_AGENT_1,
            status=JOB_STATUS_SUCCEEDED,
            result_json={"ok": True},
            completion_id="completion-1",
            completion_payload_hash="same-payload",
            now=_T0 + timedelta(seconds=2),
        )

        assert first.result == COMPLETE_ACCEPTED
        assert second.result == COMPLETE_ACCEPTED
        assert second.final_status == JOB_STATUS_SUCCEEDED

    asyncio.run(_run())


def test_extend_leases_bulk_extends_only_owned_active_jobs(backend):
    async def _run():
        owned_ids: list[str] = []
        for _ in range(2):
            job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
            await backend.claim(
                agent_id=_AGENT_1,
                capabilities=[JOB_TYPE_CRAWL_BAEMIN],
                max_jobs=1,
                lease_seconds=30,
                now=_T0,
            )
            owned_ids.append(job_id)
        other_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_2,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )

        extended = await backend.extend_leases(
            job_ids=[owned_ids[0], owned_ids[1], owned_ids[1], other_id],
            agent_id=_AGENT_1,
            lease_seconds=120,
            now=_T0 + timedelta(seconds=10),
        )

        assert extended == set(owned_ids)
        recovered = await backend.recover_stale(now=_T0 + timedelta(seconds=31))
        assert recovered == 1

    asyncio.run(_run())


# ── (QA gap D) 계약: claim 엣지 케이스(max_jobs<=0 / 빈 capabilities → []) ─────────


def test_claim_zero_max_jobs_returns_empty(backend):
    async def _run():
        await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        records = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=0,
            lease_seconds=120,
            now=_T0,
        )
        assert records == []

    asyncio.run(_run())


def test_claim_empty_capabilities_returns_empty(backend):
    async def _run():
        await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        records = await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[],  # capability 없음 → 어떤 job 도 매칭 안 됨
            max_jobs=5,
            lease_seconds=120,
            now=_T0,
        )
        assert records == []

    asyncio.run(_run())


# ── (QA gap B) 계약: extend_lease 의미론(연장/비소유/만료/미존재) ─────────────────


def test_extend_lease_prevents_stale_recovery(backend):
    # 연장하면 원래 만료 시점에 recover_stale 가 회수하지 않는다(heartbeat lease 연장, AC2).
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        ok = await backend.extend_lease(
            job_id=job_id, agent_id=_AGENT_1, lease_seconds=120, now=_T0 + timedelta(seconds=10)
        )
        assert ok is True
        recovered = await backend.recover_stale(now=_T0 + timedelta(seconds=31))
        assert recovered == 0  # 연장됐으니 아직 안 만료

    asyncio.run(_run())


def test_extend_lease_rejects_non_owner(backend):
    # 다른 Agent 의 연장 시도는 거부(False) — 소유 검증.
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )
        ok = await backend.extend_lease(
            job_id=job_id, agent_id=_AGENT_2, lease_seconds=120, now=_T0 + timedelta(seconds=1)
        )
        assert ok is False

    asyncio.run(_run())


def test_extend_lease_rejects_expired(backend):
    # 이미 만료된 lease 는 연장 불가(False) — 회수 대상이지 연장 대상이 아님.
    async def _run():
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )
        ok = await backend.extend_lease(
            job_id=job_id, agent_id=_AGENT_1, lease_seconds=30, now=_T0 + timedelta(seconds=31)
        )
        assert ok is False

    asyncio.run(_run())


def test_extend_lease_unknown_job_returns_false(backend):
    # 존재하지 않는 job(유효 UUID 형식 — PG 파싱 가능) → False.
    async def _run():
        ok = await backend.extend_lease(
            job_id="00000000-0000-0000-0000-000000000000",
            agent_id=_AGENT_1,
            lease_seconds=120,
            now=_T0,
        )
        assert ok is False

    asyncio.run(_run())


# ── (QA gap H) 계약: claim 은 owner+lease+claimed_at 를 한 번에 부여(AC2, in-memory) ─


def test_claim_assigns_owner_lease_and_claimed_at_in_memory():
    # AC2 "claim 시 lease_expires_at + claimed_at + agent_id 가 한 트랜잭션에서 부여"를
    # in-memory 스냅샷으로 잠근다(PG 는 negative suite 가 실DB 로 검증).
    async def _run():
        be = InMemoryQueueBackend()
        job_id = await be.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await be.claim(
            agent_id=_AGENT_1,
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )
        snap = be.job_snapshot(job_id)
        assert snap is not None
        assert snap.status == JOB_STATUS_CLAIMED
        assert snap.agent_id == _AGENT_1
        assert snap.claimed_at == _T0
        assert snap.lease_expires_at == _T0 + timedelta(seconds=120)

    asyncio.run(_run())
