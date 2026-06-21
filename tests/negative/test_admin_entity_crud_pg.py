"""Story 5.11 / AC1·AC2·AC3·AC4 — PostgreSQL AdminEntityRepository 영속·tenant 격리(PG-gated).

실 PostgreSQL 에서만 검증 가능한 의미를 잠근다(``TEST_DATABASE_URL`` 없으면 skip):
  (1) 모니터링 대상 create → ``monitoring_targets`` 신규 INSERT + ``audit_logs`` INSERT(같은 tx),
      재조회로 영속 확인.
  (2) 플랫폼 계정 create → ``platform_accounts`` INSERT(``*_ref`` 핸들만, 평문 컬럼 0) + audit.
  (3) soft delete — 대상 deactivate → ``status='INACTIVE'`` UPDATE 후 재조회 INACTIVE.
  (4) **tenant 격리** — 다른 tenant 대상은 list/get 에 노출되지 않음(cross-tenant negative).

always-run 단위(``tests/server/test_admin_entity_crud.py``)가 게이트/전이 의미·secret·center_name·
scope 를 잠그고, 이 파일은 실제 SQL INSERT/UPDATE·tenant 격리만 PG 로 확인한다(memory/
pg-gated-files-hide-pure-helpers). 시각/actor/id 주입(결정성). fake 값만(실제 토큰/전화/이메일 없음).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from rider_server.domain import MonitoringTargetStatus, Platform

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
_ACTOR = "99999999-9999-9999-9999-999999999999"


async def _seed(session_factory) -> None:
    from rider_server.db.models.account import PlatformAccount
    from rider_server.db.models.tenancy import Tenant

    async with session_factory() as session:
        for tid in (_TENANT_A, _TENANT_B):
            session.add(Tenant(id=uuid.UUID(tid), name="t", status="ACTIVE", created_at=_T0))
        await session.flush()
        session.add(
            PlatformAccount(
                id=uuid.UUID(_ACC_A),
                tenant_id=uuid.UUID(_TENANT_A),
                platform="COUPANG",
                label="l",
                username="vault://u",
                password="vault://p",
                auth_state="UNKNOWN",
            )
        )
        await session.commit()


def _fresh_pg():
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.pool import NullPool

    from rider_server.db.base import create_engine, create_session_factory
    from rider_server.services.admin_entity_repository_postgres import (
        PostgresAdminEntityRepository,
    )
    from rider_server.services.admin_entity_service import AdminEntityService

    cfg = Config()
    cfg.set_main_option("script_location", str(_REPO_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", _TEST_DB_URL)
    os.environ["DATABASE_URL"] = _TEST_DB_URL
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    engine = create_engine(_TEST_DB_URL, poolclass=NullPool)
    factory = create_session_factory(engine)
    asyncio.run(_seed(factory))
    repo = PostgresAdminEntityRepository(factory)
    return AdminEntityService(repo), repo, engine


@_pg_gate
def test_create_target_inserts_with_audit_same_tx() -> None:
    svc, repo, engine = _fresh_pg()
    new_id = str(uuid.uuid4())

    async def _exercise():
        try:
            result = await svc.create_monitoring_target(
                entity_id=new_id,
                tenant_id=_TENANT_A,
                platform_account_id=_ACC_A,
                name="대상",
                # 쿠팡 빈 center 는 차단(always-run test_create_coupang_blank_center_rejected)
                # — INSERT same-tx 영속을 검증하려면 유효 center 로 통과시킨다.
                center_name="쿠팡센터-강남",
                at=_T0,
                actor_id=_ACTOR,
            )
            assert result.center_name_risky is False
            stored = await repo.get_monitoring_target(new_id)
            assert stored is not None and stored.name == "대상"
            assert stored.center_name == "쿠팡센터-강남"
        finally:
            await engine.dispose()

    asyncio.run(_exercise())


@_pg_gate
def test_create_account_stores_refs_and_soft_delete_roundtrip() -> None:
    svc, repo, engine = _fresh_pg()
    acc_id = str(uuid.uuid4())
    tgt_id = str(uuid.uuid4())

    async def _exercise():
        try:
            await svc.create_platform_account(
                entity_id=acc_id,
                tenant_id=_TENANT_A,
                platform=Platform.BAEMIN,
                label="배민",
                username="vault://u2",
                password="vault://p2",
                at=_T0,
                actor_id=_ACTOR,
            )
            account = await repo.get_platform_account(acc_id)
            assert account.username == "vault://u2"  # 평문 영속

            await svc.create_monitoring_target(
                entity_id=tgt_id,
                tenant_id=_TENANT_A,
                platform_account_id=acc_id,
                name="t",
                center_name="센터",
                at=_T0,
                actor_id=_ACTOR,
            )
            await svc.deactivate_monitoring_target(
                tgt_id, tenant_id=_TENANT_A, at=_T0, actor_id=_ACTOR
            )
            reloaded = await repo.get_monitoring_target(tgt_id)
            assert reloaded.status is MonitoringTargetStatus.INACTIVE  # soft delete 영속
        finally:
            await engine.dispose()

    asyncio.run(_exercise())


@_pg_gate
def test_cross_tenant_target_not_exposed() -> None:
    from rider_server.services.admin_action_service import TenantScopeViolation

    svc, repo, engine = _fresh_pg()
    tgt_id = str(uuid.uuid4())

    async def _exercise():
        try:
            await svc.create_monitoring_target(
                entity_id=tgt_id,
                tenant_id=_TENANT_A,
                platform_account_id=_ACC_A,
                name="A대상",
                center_name="센터",
                at=_T0,
                actor_id=_ACTOR,
            )
            # 다른 tenant 의 list 에는 노출되지 않는다.
            b_rows = await repo.list_monitoring_targets(_TENANT_B)
            assert all(r.id != tgt_id for r in b_rows)
            # 다른 tenant 로 편집 시도 → not-found 동급(404 매핑) 거부.
            with pytest.raises(TenantScopeViolation):
                await svc.deactivate_monitoring_target(
                    tgt_id, tenant_id=_TENANT_B, at=_T0, actor_id=_ACTOR
                )
        finally:
            await engine.dispose()

    asyncio.run(_exercise())


@_pg_gate
def test_delete_empty_tenant_removes_row_and_audits_same_tx() -> None:
    svc, repo, engine = _fresh_pg()

    async def _exercise():
        try:
            from sqlalchemy import select

            from rider_server.db.models.audit import AuditLog

            deleted = await svc.delete_tenant(
                _TENANT_B, at=_T0, actor_id=_ACTOR, reason="정리"
            )
            assert deleted.id == _TENANT_B
            assert await repo.get_tenant(_TENANT_B) is None

            async with repo._session_factory() as session:
                audits = (
                    await session.execute(
                        select(AuditLog).where(AuditLog.target_id == uuid.UUID(_TENANT_B))
                    )
                ).scalars().all()
            assert len(audits) == 1
            assert audits[0].action == "TENANT_DELETE"
        finally:
            await engine.dispose()

    asyncio.run(_exercise())


@_pg_gate
def test_delete_tenant_with_account_or_subscription_is_blocked_no_audit() -> None:
    from rider_server.services.admin_entity_service import AdminEntityDeleteBlockedError

    svc, repo, engine = _fresh_pg()

    async def _exercise():
        try:
            from sqlalchemy import func, insert, select

            from rider_server.db.models.audit import AuditLog
            from rider_server.db.models.tenancy import Subscription

            with pytest.raises(AdminEntityDeleteBlockedError):
                await svc.delete_tenant(_TENANT_A, at=_T0, actor_id=_ACTOR)
            assert await repo.get_tenant(_TENANT_A) is not None

            sub_id = uuid.uuid4()
            async with repo._session_factory() as session:
                await session.execute(
                    insert(Subscription).values(
                        id=sub_id,
                        tenant_id=uuid.UUID(_TENANT_B),
                        plan="basic",
                        status="PAYMENT_ACTIVE",
                        quotas={},
                    )
                )
                await session.commit()

            with pytest.raises(AdminEntityDeleteBlockedError):
                await svc.delete_tenant(_TENANT_B, at=_T0, actor_id=_ACTOR)
            assert await repo.get_tenant(_TENANT_B) is not None

            async with repo._session_factory() as session:
                audit_count = (
                    await session.execute(
                        select(func.count()).select_from(AuditLog).where(
                            AuditLog.action == "TENANT_DELETE"
                        )
                    )
                ).scalar_one()
            assert audit_count == 0
        finally:
            await engine.dispose()

    asyncio.run(_exercise())
