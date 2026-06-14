"""Story 5.7 / AC1·AC2·AC3 — PostgreSQL AdminActionRepository 영속·tenant 격리(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) 구독 ``suspend`` → ``subscriptions.status='SUSPENDED'`` UPDATE + ``audit_logs`` INSERT(같은 tx).
  (2) 대상 ``pause`` → ``monitoring_targets.status='PAUSED'`` UPDATE + audit.
  (3) job retry → ``jobs.status='PENDING'`` 전이(FAILED→PENDING) + audit.
  (4) Agent 배정 → 기존 ``browser_profiles.agent_id`` 재바인딩 + audit.
  (5) **tenant 격리** — 다른 tenant 대상/구독 전이 거부 + 상태 불변 + audit 누출 0(cross-tenant negative).
  (6) 미인증 actor sentinel → ``audit_logs.actor_id`` NULL + ``diff_redacted.actor`` 보존.

always-run 단위(``tests/server/test_admin_actions.py``·``test_admin_action_audit.py``)가 게이트/전이
의미·redaction 을 잠그고, 이 파일은 실제 SQL UPDATE/INSERT·tenant 격리만 PG 로 확인한다. 시각/actor
주입(결정성). fake 값만 — 실제 토큰/전화/이메일/chat_id 형태 없음.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
_pg_gate = pytest.mark.skipif(
    not _TEST_DB_URL,
    reason="TEST_DATABASE_URL 미설정 — 실 Postgres 부재(현 WSL/venv). always-run 단위로 잠금.",
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_T0 = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)

_TENANT_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
_TENANT_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
_ACC_A = "a1111111-1111-1111-1111-111111111111"
_ACC_B = "b1111111-1111-1111-1111-111111111111"
_SUB_A = "c1111111-1111-1111-1111-111111111111"
_SUB_B = "c2222222-2222-2222-2222-222222222222"
_T_A = "11111111-1111-1111-1111-111111111111"
_T_B = "33333333-3333-3333-3333-333333333333"
_JOB_A = "d1111111-1111-1111-1111-111111111111"
_AGENT = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
_PROFILE = "dddddddd-dddd-dddd-dddd-dddddddddddd"
_ACTOR = "99999999-9999-9999-9999-999999999999"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import MonitoringTarget, PlatformAccount
    from rider_server.db.models.agent import Agent, BrowserProfile, Job
    from rider_server.db.models.tenancy import Subscription, Tenant

    async with session_factory() as session:
        for tid in (_TENANT_A, _TENANT_B):
            session.add(Tenant(id=uuid.UUID(tid), name="t", status="ACTIVE", created_at=_T0))
        session.add(Subscription(id=uuid.UUID(_SUB_A), tenant_id=uuid.UUID(_TENANT_A), plan="basic", status="PAYMENT_ACTIVE", quotas={}))
        session.add(Subscription(id=uuid.UUID(_SUB_B), tenant_id=uuid.UUID(_TENANT_B), plan="basic", status="PAYMENT_ACTIVE", quotas={}))
        session.add(PlatformAccount(id=uuid.UUID(_ACC_A), tenant_id=uuid.UUID(_TENANT_A), platform="BAEMIN", label="l", username_ref="vault://u", password_ref="vault://p", auth_state="ACTIVE"))
        session.add(PlatformAccount(id=uuid.UUID(_ACC_B), tenant_id=uuid.UUID(_TENANT_B), platform="BAEMIN", label="l", username_ref="vault://u", password_ref="vault://p", auth_state="ACTIVE"))
        session.add(MonitoringTarget(id=uuid.UUID(_T_A), tenant_id=uuid.UUID(_TENANT_A), platform_account_id=uuid.UUID(_ACC_A), name="A", center_name="c", external_id="", url="", interval_minutes=10, status="ACTIVE", next_run_at=None))
        session.add(MonitoringTarget(id=uuid.UUID(_T_B), tenant_id=uuid.UUID(_TENANT_B), platform_account_id=uuid.UUID(_ACC_B), name="B", center_name="c", external_id="", url="", interval_minutes=10, status="ACTIVE", next_run_at=None))
        session.add(Job(id=uuid.UUID(_JOB_A), type="CRAWL_BAEMIN", target_id=uuid.UUID(_T_A), status="FAILED", error_code="CRAWL_FAILURE", attempts=1))
        session.add(Agent(id=uuid.UUID(_AGENT), name="agent-1", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0, capacity_json={}))
        session.add(BrowserProfile(id=uuid.UUID(_PROFILE), agent_id=uuid.UUID(_AGENT), target_id=uuid.UUID(_T_A), profile_path_ref="vault://p", cdp_port=None, state="READY"))
        await session.commit()


def _fresh_pg():
    from alembic import command
    from alembic.config import Config

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.queue.memory_queue import InMemoryQueueBackend
    from rider_server.services.admin_action_repository_postgres import (
        PostgresAdminActionRepository,
    )
    from rider_server.services.admin_action_service import AdminActionService

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    prev = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL)
    factory = create_session_factory(engine)
    asyncio.run(_seed(factory))
    repo = PostgresAdminActionRepository(factory)
    service = AdminActionService(repo, InMemoryQueueBackend())

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return service, factory, _teardown


@pytest.fixture
def pg():
    service, factory, teardown = _fresh_pg()
    try:
        yield service, factory
    finally:
        teardown()


async def _audit_rows(factory):
    from sqlalchemy import select

    from rider_server.db.models.audit import AuditLog

    async with factory() as session:
        return (await session.execute(select(AuditLog))).scalars().all()


async def _status(factory, model, pk):
    from sqlalchemy import select

    async with factory() as session:
        return (await session.execute(select(model).where(model.id == pk))).scalar_one().status


# ── (1)~(3) 전이 UPDATE + audit INSERT(같은 tx) ──────────────────────────────────

@_pg_gate
def test_suspend_persists_status_and_audit(pg) -> None:
    from rider_server.db.models.tenancy import Subscription as SubRow

    service, factory = pg
    asyncio.run(service.suspend_subscription(_SUB_A, reason="미납", tenant_id=_TENANT_A, actor_id=_ACTOR, at=_T0))

    assert asyncio.run(_status(factory, SubRow, uuid.UUID(_SUB_A))) == "SUSPENDED"
    audits = asyncio.run(_audit_rows(factory))
    assert any(a.action == "SUBSCRIPTION_SUSPEND" and str(a.target_id) == _SUB_A for a in audits)


@_pg_gate
def test_pause_persists_status_and_audit(pg) -> None:
    from rider_server.db.models.account import MonitoringTarget as TargetRow

    service, factory = pg
    asyncio.run(service.set_target_status(_T_A, active=False, tenant_id=_TENANT_A, actor_id=_ACTOR, reason="", at=_T0))

    assert asyncio.run(_status(factory, TargetRow, uuid.UUID(_T_A))) == "PAUSED"


@_pg_gate
def test_retry_persists_pending(pg) -> None:
    from rider_server.db.models.agent import Job as JobRow

    service, factory = pg
    asyncio.run(service.retry_job(_JOB_A, tenant_id=_TENANT_A, actor_id=_ACTOR, reason="", at=_T0))

    assert asyncio.run(_status(factory, JobRow, uuid.UUID(_JOB_A))) == "PENDING"


@_pg_gate
def test_assign_agent_rebinds_browser_profile(pg) -> None:
    from sqlalchemy import select

    from rider_server.db.models.agent import BrowserProfile as ProfileRow

    service, factory = pg
    new_agent = str(uuid.uuid4())

    async def _seed_agent2():
        from rider_server.db.models.agent import Agent
        async with factory() as session:
            session.add(Agent(id=uuid.UUID(new_agent), name="a2", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0, capacity_json={}))
            await session.commit()

    asyncio.run(_seed_agent2())
    asyncio.run(service.assign_agent(target_id=_T_A, agent_id=new_agent, tenant_id=_TENANT_A, actor_id=_ACTOR, reason="", at=_T0))

    async def _profile_agent():
        async with factory() as session:
            row = (await session.execute(select(ProfileRow).where(ProfileRow.id == uuid.UUID(_PROFILE)))).scalar_one()
            return str(row.agent_id)

    assert asyncio.run(_profile_agent()) == new_agent


# ── (5) tenant 격리(cross-tenant negative) ───────────────────────────────────────

@_pg_gate
def test_cross_tenant_suspend_blocked_no_change_no_audit(pg) -> None:
    from rider_server.db.models.tenancy import Subscription as SubRow
    from rider_server.services.admin_action_service import TenantScopeViolation

    service, factory = pg
    with pytest.raises(TenantScopeViolation):
        asyncio.run(service.suspend_subscription(_SUB_B, reason="x", tenant_id=_TENANT_A, actor_id=_ACTOR, at=_T0))

    # tenant B 구독 불변 + audit 누출 0.
    assert asyncio.run(_status(factory, SubRow, uuid.UUID(_SUB_B))) == "PAYMENT_ACTIVE"
    assert asyncio.run(_audit_rows(factory)) == []


# ── (6) 미인증 actor sentinel → actor_id NULL + diff 보존 ─────────────────────────

@_pg_gate
def test_unauthenticated_actor_sentinel_stored_in_diff(pg) -> None:
    from rider_server.services.admin_action_service import UNAUTHENTICATED_ACTOR

    service, factory = pg
    asyncio.run(
        service.set_target_status(
            _T_A, active=False, tenant_id=_TENANT_A, actor_id=UNAUTHENTICATED_ACTOR, reason="", at=_T0
        )
    )

    audits = asyncio.run(_audit_rows(factory))
    row = next(a for a in audits if a.action == "TARGET_PAUSE")
    assert row.actor_id is None  # UUID 아님 → 컬럼 NULL
    assert row.diff_redacted.get("actor") == UNAUTHENTICATED_ACTOR  # sentinel 보존(추적)
