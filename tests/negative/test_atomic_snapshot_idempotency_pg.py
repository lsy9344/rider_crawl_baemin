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
def test_successful_snapshot_crawl_restores_account_auth_state_active(pg_repo) -> None:
    """성공 crawl(snapshot) atomic 완료가 ``platform_accounts.auth_state`` 를 ACTIVE 로 되돌린다.

    회귀(2026-06-29): atomic 경로(``complete_snapshot_job``)가 auth_state 를 갱신하지 않아,
    AUTH_CHECK 등이 계정을 UNKNOWN 으로 만든 뒤에는 crawl 이 아무리 성공해도 계정이 UNKNOWN 에
    굳어 scheduler 가 ``AUTH_STATE_UNKNOWN`` 으로 영구 차단(수집 정지)했다. 성공 crawl 은 인증
    정상의 강한 신호이므로 ACTIVE 로 복귀해야 한다(``PostgresQueueBackend.complete`` 와 동치).
    """
    from sqlalchemy import select

    from rider_server.db.models.account import PlatformAccount
    from rider_server.domain import BaeminAuthState
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory = pg_repo

    async def _run():
        # 사전조건: 계정을 UNKNOWN 으로 만들어 데드락 상태를 재현한다.
        async with factory() as session:
            await session.execute(
                PlatformAccount.__table__.update()
                .where(PlatformAccount.id == uuid.UUID(_ACCOUNT))
                .values(auth_state=BaeminAuthState.UNKNOWN.value)
            )
            await session.commit()

        outcome = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            result_json={"result_type": "snapshot"}, error_code=None, duration_ms=10,
            result_schema_version="1", completion_id="auth1", completion_payload_hash="h1",
            now=_T0,
        )
        assert outcome.result == COMPLETE_ACCEPTED
        assert outcome.final_status == JOB_STATUS_SUCCEEDED

        async with factory() as session:
            auth_state = (
                await session.execute(
                    select(PlatformAccount.auth_state).where(
                        PlatformAccount.id == uuid.UUID(_ACCOUNT)
                    )
                )
            ).scalar_one()
        assert auth_state == BaeminAuthState.ACTIVE.value

    asyncio.run(_run())


@_pg_gate
def test_unknown_auth_state_result_does_not_clobber_healthy_account(pg_repo) -> None:
    """``UNKNOWN``("판정 불가") 결과는 정상(ACTIVE/AUTH_VERIFIED) 계정을 덮어쓰지 않는다.

    회귀(2026-06-29 2차): AUTH_CHECK 가 쿠팡 계정을 Baemin probe 로 점검하다 분류 실패→
    ``auth_state=UNKNOWN`` 을 보고했고, 서버가 그걸 그대로 써 **정상 계정을 UNKNOWN 으로 떨궈**
    scheduler 가 ``AUTH_STATE_UNKNOWN`` 으로 영구 차단했다. UNKNOWN 은 정보가 없다는 뜻이라
    정상 신호를 파괴해선 안 된다. 단, 비정상(AUTH_REQUIRED 등) 상태는 UNKNOWN 으로 낮출 수
    있어야 한다(stale 해소 — 기존 의도 보존).
    """
    from sqlalchemy import select

    from rider_server.db.models.account import PlatformAccount
    from rider_server.domain import BaeminAuthState
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory = pg_repo

    async def _set_state(value: str) -> None:
        async with factory() as session:
            await session.execute(
                PlatformAccount.__table__.update()
                .where(PlatformAccount.id == uuid.UUID(_ACCOUNT))
                .values(auth_state=value)
            )
            await session.commit()

    async def _read_state() -> str:
        async with factory() as session:
            return (
                await session.execute(
                    select(PlatformAccount.auth_state).where(
                        PlatformAccount.id == uuid.UUID(_ACCOUNT)
                    )
                )
            ).scalar_one()

    async def _complete_unknown(completion_id: str, hash_: str, *, at) -> None:
        outcome = await repo.complete_snapshot_job(
            _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
            # 성공 crawl 이지만 결과가 명시적으로 UNKNOWN 을 싣는 경우(분류 불가 표면화).
            result_json={
                "result_type": "snapshot",
                "auth_state": BaeminAuthState.UNKNOWN.value,
            },
            error_code=None, duration_ms=10, result_schema_version="1",
            completion_id=completion_id, completion_payload_hash=hash_, now=at,
        )
        assert outcome.result == COMPLETE_ACCEPTED

    async def _run():
        # (1) 정상(ACTIVE) 계정 + UNKNOWN 결과 → ACTIVE 유지(덮어쓰기 금지).
        await _set_state(BaeminAuthState.ACTIVE.value)
        await _complete_unknown("u-active", "h1", at=_T0)
        assert await _read_state() == BaeminAuthState.ACTIVE.value

        # (2) 비정상(AUTH_REQUIRED) 계정 + UNKNOWN 결과 → UNKNOWN 으로 낮춤(stale 해소 보존).
        #     같은 job 을 재완료할 수 없으므로(터미널) 상태만 바꿔 statement 의미를 직접 검증한다.
        from rider_server.queue.postgres_queue import _account_auth_state_update

        await _set_state(BaeminAuthState.AUTH_REQUIRED.value)
        async with factory() as session:
            await session.execute(
                _account_auth_state_update(_ACCOUNT, BaeminAuthState.UNKNOWN.value)
            )
            await session.commit()
        assert await _read_state() == BaeminAuthState.UNKNOWN.value

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
