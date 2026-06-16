"""Story 5.8 / AC1·AC3 — PostgreSQL 보안 영속·token revoke·audit 7필드(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) audit 7필드 영속 — 위험 액션이 ``source``/``reason``/``result`` 컬럼까지 INSERT(0005 컬럼).
  (2) Agent token revoke — ``agents.token_revoked_at`` UPDATE + audit(같은 tx) → ``is_revoked`` True.
  (3) 0005 round-trip — head(0005) upgrade → ``agents.token_revoked_at``/``audit_logs.result`` 존재,
      downgrade(0004) → 컬럼 제거, 재 upgrade.
  (4) cross-tenant audit 누출 0 — 거부된 cross-tenant 액션은 상태 전이도 audit 도 남기지 않는다.

always-run 단위(``tests/server/test_agent_token_revoke.py``·``test_admin_security.py``·
``test_audit_log_schema.py``)가 게이트/revoke/redaction 의미를 잠그고, 이 파일은 실제 SQL
UPDATE/INSERT·컬럼 영속만 PG 로 확인한다. 시각/actor 주입(결정성). fake 값만.
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
_T_A = "d1111111-1111-1111-1111-111111111111"
_T_B = "d2222222-2222-2222-2222-222222222222"
_AGENT = "e1111111-1111-1111-1111-111111111111"
_ACTOR = "f1111111-1111-1111-1111-111111111111"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import MonitoringTarget, PlatformAccount
    from rider_server.db.models.agent import Agent
    from rider_server.db.models.tenancy import Tenant

    async with session_factory() as session:
        for tid in (_TENANT_A, _TENANT_B):
            session.add(Tenant(id=uuid.UUID(tid), name="t", status="ACTIVE", created_at=_T0))
        await session.flush()
        session.add(PlatformAccount(id=uuid.UUID(_ACC_A), tenant_id=uuid.UUID(_TENANT_A), platform="BAEMIN", label="l", username_ref="vault://u", password_ref="vault://p", auth_state="ACTIVE"))
        session.add(Agent(id=uuid.UUID(_AGENT), name="agent-1", machine_id="m", version="1.0.0", os="windows", status="active", last_heartbeat_at=_T0, capacity_json={}))
        await session.flush()
        session.add(MonitoringTarget(id=uuid.UUID(_T_A), tenant_id=uuid.UUID(_TENANT_A), platform_account_id=uuid.UUID(_ACC_A), name="A", center_name="c", external_id="", url="", interval_minutes=10, status="ACTIVE", next_run_at=None))
        await session.commit()


def _fresh_pg():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.queue.memory_queue import InMemoryQueueBackend
    from rider_server.services.admin_action_repository_postgres import (
        PostgresAdminActionRepository,
    )
    from rider_server.services.admin_action_service import AdminActionService
    from rider_server.services.agent_token_repository_postgres import (
        PostgresAgentTokenRepository,
    )
    from rider_server.services.agent_token_service import AgentTokenService

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
    admin = AdminActionService(PostgresAdminActionRepository(factory), InMemoryQueueBackend())
    token = AgentTokenService(PostgresAgentTokenRepository(factory))

    def _teardown() -> None:
        try:
            command.downgrade(cfg, "base")
        finally:
            asyncio.run(engine.dispose())
            if prev is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev

    return admin, token, factory, cfg, _teardown


@pytest.fixture
def pg():
    admin, token, factory, cfg, teardown = _fresh_pg()
    try:
        yield admin, token, factory, cfg
    finally:
        teardown()


async def _audit_rows(factory):
    from sqlalchemy import select

    from rider_server.db.models.audit import AuditLog

    async with factory() as session:
        return (await session.execute(select(AuditLog))).scalars().all()


# ── (1) audit 7필드 영속(source/reason/result 컬럼) ──────────────────────────────

@_pg_gate
def test_pause_persists_source_reason_result_columns(pg) -> None:
    admin, _token, factory, _cfg = pg
    asyncio.run(
        admin.set_target_status(
            _T_A, active=False, tenant_id=_TENANT_A, actor_id=_ACTOR,
            reason="점검", at=_T0, source="ADMIN_UI/operator",
        )
    )
    rows = asyncio.run(_audit_rows(factory))
    pause = [a for a in rows if a.action == "TARGET_PAUSE"]
    assert pause, "TARGET_PAUSE audit 가 영속돼야 한다"
    a = pause[-1]
    assert a.result == "SUCCESS"
    assert a.source == "ADMIN_UI/operator"
    assert a.reason == "점검"
    assert str(a.target_id) == _T_A


# ── (2) Agent token revoke — token_revoked_at UPDATE + is_revoked ────────────────

@_pg_gate
def test_revoke_sets_token_revoked_at_and_audit(pg) -> None:
    from sqlalchemy import select

    from rider_server.db.models.agent import Agent as AgentRow

    _admin, token, factory, _cfg = pg
    assert asyncio.run(token._repo.is_revoked(_AGENT)) is False  # 초기 미revoke

    asyncio.run(token.revoke(_AGENT, at=_T0, actor_id=_ACTOR, source="ADMIN_UI/secret-admin", reason="유출"))

    async def _revoked_at():
        async with factory() as session:
            return (await session.execute(
                select(AgentRow.token_revoked_at).where(AgentRow.id == uuid.UUID(_AGENT))
            )).scalar_one()

    assert asyncio.run(_revoked_at()) is not None
    assert asyncio.run(token._repo.is_revoked(_AGENT)) is True
    rows = asyncio.run(_audit_rows(factory))
    assert any(a.action == "AGENT_TOKEN_REVOKE" and a.result == "SUCCESS" for a in rows)


@_pg_gate
def test_external_token_rotate_audit_persists_ref_no_plaintext(pg) -> None:
    _admin, token, factory, _cfg = pg
    asyncio.run(
        token.rotate_external_token(
            channel_id=_T_A, new_secret_ref="vault://telegram/bot2",
            at=_T0, actor_id=_ACTOR, source="s", reason="회전",
        )
    )
    rows = asyncio.run(_audit_rows(factory))
    rot = [a for a in rows if a.action == "EXTERNAL_TOKEN_ROTATE"]
    assert rot and rot[-1].diff_redacted["new_secret_ref"] == "vault://telegram/bot2"


# ── (3) 0005 round-trip — 컬럼 추가/제거 ──────────────────────────────────────────

@_pg_gate
def test_0005_round_trip_adds_and_drops_columns(pg) -> None:
    from alembic import command
    from sqlalchemy import inspect
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine

    _admin, _token, _factory, cfg = pg
    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)

    async def _cols(table):
        async with engine.connect() as conn:
            return await conn.run_sync(lambda sc: {c["name"] for c in inspect(sc).get_columns(table)})

    try:
        # head(0005): 신규 컬럼 존재.
        audit_cols = asyncio.run(_cols("audit_logs"))
        agent_cols = asyncio.run(_cols("agents"))
        assert {"source", "reason", "result"} <= audit_cols
        assert {"token_revoked_at", "token_rotated_at"} <= agent_cols

        # downgrade 0004: 신규 컬럼 제거(테이블은 유지 — 14표 불변).
        command.downgrade(cfg, "0004_channel_reg")
        audit_cols_4 = asyncio.run(_cols("audit_logs"))
        agent_cols_4 = asyncio.run(_cols("agents"))
        assert {"source", "reason", "result"}.isdisjoint(audit_cols_4)
        assert {"token_revoked_at", "token_rotated_at"}.isdisjoint(agent_cols_4)

        # 재 upgrade head: 다시 추가(round-trip).
        command.upgrade(cfg, "head")
        assert {"source", "reason", "result"} <= asyncio.run(_cols("audit_logs"))
    finally:
        asyncio.run(engine.dispose())


# ── (4) cross-tenant — 거부된 액션은 전이·audit 0 ────────────────────────────────

@_pg_gate
def test_cross_tenant_pause_leaves_no_audit(pg) -> None:
    from rider_server.services.admin_action_service import TenantScopeViolation

    admin, _token, factory, _cfg = pg
    # _T_A 는 tenant A 소유 — tenant B 로 pause 시도 → 거부(누출 0).
    with pytest.raises(TenantScopeViolation):
        asyncio.run(
            admin.set_target_status(_T_A, active=False, tenant_id=_TENANT_B, actor_id=_ACTOR, reason="", at=_T0)
        )
    rows = asyncio.run(_audit_rows(factory))
    assert not any(a.action == "TARGET_PAUSE" for a in rows)  # 거부 → audit 0
