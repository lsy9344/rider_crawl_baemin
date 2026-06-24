"""전역 전송 kill switch 가 snapshot dispatch enqueue 까지 막는지(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) ``sending_enabled=False`` 면 crawl 성공 ingest 시 snapshot/message 는 저장되지만
      ``delivery_logs`` 예약행과 ``KAKAO_SEND`` job 은 만들지 않는다(오발송·dedup 오염 방지).
  (2) ``sending_enabled=True`` 면 기존 fan-out 그대로 — delivery_log 1건 + KAKAO_SEND job 1건.

차단 시 delivery log 를 만들지 않아야 dedup key 가 소비되지 않고, 운영자가 전송을 다시 켠 뒤
같은 snapshot 흐름이 정상적으로 fan-out 된다(작업지시서 리스크표 참조).
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
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재. enqueue kill switch 의미는 PG 로만 잠근다.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)

_TENANT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_ACCOUNT = "a1111111-1111-1111-1111-111111111111"
_TARGET = "11111111-1111-1111-1111-111111111111"
_AGENT = "cccccccc-cccc-cccc-cccc-cccccccccccc"
_JOB = "d0000000-0000-0000-0000-000000000001"
_CHANNEL = "e0000000-0000-0000-0000-000000000001"
_RULE = "f0000000-0000-0000-0000-000000000001"


async def _seed(session_factory, *, tenant_sending_enabled: bool) -> None:
    from rider_server.db.models.account import MonitoringTarget, PlatformAccount
    from rider_server.db.models.agent import Agent, Job
    from rider_server.db.models.messaging import DeliveryRule, MessengerChannel
    from rider_server.db.models.tenancy import Subscription, Tenant

    async with session_factory() as session:
        session.add(
            Tenant(
                id=uuid.UUID(_TENANT), name="t", status="ACTIVE", created_at=_T0,
                sending_enabled=tenant_sending_enabled,
            )
        )
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
        # delivery rule(enabled) + ACTIVE KAKAO channel — fan-out 대상이 존재함을 보장한다.
        session.add(
            MessengerChannel(
                id=uuid.UUID(_CHANNEL), tenant_id=uuid.UUID(_TENANT), messenger="KAKAO",
                kakao_room_name="이수열", state="ACTIVE",
            )
        )
        await session.flush()
        session.add(
            DeliveryRule(
                id=uuid.UUID(_RULE), tenant_id=uuid.UUID(_TENANT),
                target_id=uuid.UUID(_TARGET), channel_id=uuid.UUID(_CHANNEL),
                template_id="", enabled=True, send_only_on_change=False,
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


def _fresh_pg(*, sending_enabled: bool, tenant_sending_enabled: bool = True):
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
    asyncio.run(_seed(factory, tenant_sending_enabled=tenant_sending_enabled))
    repo = PostgresSnapshotIngestRepository(factory, sending_enabled=sending_enabled)

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


async def _count(factory, model_cls) -> int:
    from sqlalchemy import func, select

    async with factory() as session:
        return int((await session.execute(select(func.count()).select_from(model_cls))).scalar_one())


async def _kakao_send_job_count(factory) -> int:
    from sqlalchemy import func, select

    from rider_server.db.models.agent import Job
    from rider_server.queue.states import JOB_TYPE_KAKAO_SEND

    async with factory() as session:
        return int(
            (
                await session.execute(
                    select(func.count()).select_from(Job).where(Job.type == JOB_TYPE_KAKAO_SEND)
                )
            ).scalar_one()
        )


@_pg_gate
def test_snapshot_ingest_sending_disabled_does_not_create_delivery_log_or_kakao_job() -> None:
    """Global sending OFF stores snapshot/message but does not reserve dispatch work."""
    from rider_server.db.models.messaging import (
        DeliveryLog as DeliveryLogRow,
        Message as MessageRow,
        Snapshot as SnapshotRow,
    )
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    # tenant 전송은 ON 이지만 환경 전역 kill switch 가 OFF → fan-out 차단(전역 게이트 단독 효과 확인).
    repo, factory, teardown = _fresh_pg(sending_enabled=False, tenant_sending_enabled=True)
    try:

        async def _run():
            outcome = await repo.complete_snapshot_job(
                _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
                result_json={"result_type": "snapshot", "auth_state": "ACTIVE"},
                error_code=None, duration_ms=10, result_schema_version="1",
                completion_id="c1", completion_payload_hash="h1", now=_T0,
            )
            assert outcome.result == COMPLETE_ACCEPTED
            assert outcome.final_status == JOB_STATUS_SUCCEEDED

            snapshot_count = await _count(factory, SnapshotRow)
            message_count = await _count(factory, MessageRow)
            delivery_log_count = await _count(factory, DeliveryLogRow)
            kakao_send_job_count = await _kakao_send_job_count(factory)

            assert snapshot_count == 1
            assert message_count == 1
            assert delivery_log_count == 0
            assert kakao_send_job_count == 0

        asyncio.run(_run())
    finally:
        teardown()


@_pg_gate
def test_snapshot_ingest_sending_enabled_creates_kakao_job() -> None:
    """Global sending ON + tenant sending ON keeps the existing Kakao fan-out behavior."""
    from rider_server.db.models.messaging import (
        DeliveryLog as DeliveryLogRow,
        Message as MessageRow,
        Snapshot as SnapshotRow,
    )
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory, teardown = _fresh_pg(sending_enabled=True, tenant_sending_enabled=True)
    try:

        async def _run():
            outcome = await repo.complete_snapshot_job(
                _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
                result_json={"result_type": "snapshot", "auth_state": "ACTIVE"},
                error_code=None, duration_ms=10, result_schema_version="1",
                completion_id="c1", completion_payload_hash="h1", now=_T0,
            )
            assert outcome.result == COMPLETE_ACCEPTED
            assert outcome.final_status == JOB_STATUS_SUCCEEDED

            snapshot_count = await _count(factory, SnapshotRow)
            message_count = await _count(factory, MessageRow)
            delivery_log_count = await _count(factory, DeliveryLogRow)
            kakao_send_job_count = await _kakao_send_job_count(factory)

            assert snapshot_count == 1
            assert message_count == 1
            assert delivery_log_count == 1
            assert kakao_send_job_count == 1

        asyncio.run(_run())
    finally:
        teardown()


@_pg_gate
def test_snapshot_ingest_tenant_sending_disabled_global_on_does_not_create_dispatch_work() -> None:
    """Tenant 'send OFF' blocks fan-out even when the env-global switch is ON.

    Admin 대시보드의 ``send_enabled`` 는 ``tenant.sending_enabled AND …`` 라 고객별 OFF 면 전송
    OFF 로 표시된다. 그 의미를 enqueue 경로가 존중해야 한다 — 전역 ON 만으로 enqueue 되면 안 된다.
    """
    from rider_server.db.models.messaging import (
        DeliveryLog as DeliveryLogRow,
        Message as MessageRow,
        Snapshot as SnapshotRow,
    )
    from rider_server.queue import COMPLETE_ACCEPTED
    from rider_server.queue.states import JOB_STATUS_SUCCEEDED

    repo, factory, teardown = _fresh_pg(sending_enabled=True, tenant_sending_enabled=False)
    try:

        async def _run():
            outcome = await repo.complete_snapshot_job(
                _record(), agent_id=_AGENT, status=JOB_STATUS_SUCCEEDED,
                result_json={"result_type": "snapshot", "auth_state": "ACTIVE"},
                error_code=None, duration_ms=10, result_schema_version="1",
                completion_id="c1", completion_payload_hash="h1", now=_T0,
            )
            assert outcome.result == COMPLETE_ACCEPTED
            assert outcome.final_status == JOB_STATUS_SUCCEEDED

            snapshot_count = await _count(factory, SnapshotRow)
            message_count = await _count(factory, MessageRow)
            delivery_log_count = await _count(factory, DeliveryLogRow)
            kakao_send_job_count = await _kakao_send_job_count(factory)

            assert snapshot_count == 1
            assert message_count == 1
            assert delivery_log_count == 0
            assert kakao_send_job_count == 0

        asyncio.run(_run())
    finally:
        teardown()
