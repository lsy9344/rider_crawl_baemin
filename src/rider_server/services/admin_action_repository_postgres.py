"""PostgreSQL ``AdminActionRepository`` 구현 — Story 5.7 (AC1·AC2·AC3).

:class:`rider_server.services.admin_action_service.AdminActionRepository` 포트의 실 DB 구현.
5.2 ``db/base.py`` 의 ``async_sessionmaker`` 를 주입받아 쓰고 새 엔진을 만들지 않는다
(``PostgresChannelRepository``/``PostgresQueueBackend`` 선례). async 본문은 DB I/O 만 한다.

**같은 트랜잭션(AC3):** 위험 액션의 상태 전이 UPDATE 와 ``audit_logs`` INSERT 를 **한 세션·한
commit** 으로 묶는다 — 액션만 성공하고 audit 가 누락되는 경우가 없다(둘 다 commit 또는 둘 다
rollback). 신규 컬럼/테이블/마이그레이션 0 — 기존 14표를 UPDATE/INSERT 만 한다.

**actor_id 매핑:** seam 이 준 actor 가 UUID 면 ``audit_logs.actor_id`` 컬럼에, 미인증 sentinel
(UUID 아님)이면 컬럼은 NULL 로 두고 ``diff_redacted.actor`` 에 보존한다(미인증도 추적, AC3).

**열린 질문 #1(HELD Dispatch 영속):** ``jobs.status``/``delivery_logs.status`` 어휘에 게이트의
``DISCARDED``/``PENDING`` 4값을 신규 멤버 없이 매핑할 수 없다(14표·enum lock). 따라서 HELD
Dispatch 의 영속 표현은 **Epic 3/5 reconcile** 로 두고, 본 PG 구현은 보수적으로 ``get_held_dispatch``
→ ``None``(미노출), ``transition_dispatch`` → audit 만 기록한다(fail-closed — 자동 발송 0). 게이트
폐기/재개 **의미** 는 순수 service + in-memory 테스트로 잠근다(memory pg-gated-files-hide-pure-helpers).
"""

from __future__ import annotations

import uuid

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.account import MonitoringTarget as MonitoringTargetRow
from rider_server.db.models.account import PlatformAccount as PlatformAccountRow
from rider_server.db.models.agent import BrowserProfile as BrowserProfileRow
from rider_server.db.models.agent import Job as JobRow
from rider_server.db.models.audit import AuditLog as AuditLogRow
from rider_server.db.models.tenancy import Subscription as SubscriptionRow
from rider_server.domain import (
    MonitoringTarget,
    MonitoringTargetStatus,
    Subscription,
    SubscriptionStatus,
)

from .admin_action_service import AuditEntry, HeldDispatchRef, JobRef


def _sub_to_domain(row: SubscriptionRow) -> Subscription:
    return Subscription(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        plan=row.plan,
        status=SubscriptionStatus(row.status),
        current_period_end=row.current_period_end,
        quotas=dict(row.quotas or {}),
    )


def _target_to_domain(row: MonitoringTargetRow) -> MonitoringTarget:
    return MonitoringTarget(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        platform_account_id=str(row.platform_account_id),
        name=row.name,
        center_name=row.center_name,
        external_id=row.external_id,
        url=row.url,
        interval_minutes=row.interval_minutes,
        status=MonitoringTargetStatus(row.status),
    )


def _audit_values(audit: AuditEntry) -> dict:
    """``AuditEntry`` → ``audit_logs`` INSERT values(actor/target UUID 파싱·sentinel 보존)."""

    diff = dict(audit.diff_redacted)
    try:
        actor_uuid = uuid.UUID(audit.actor_id) if audit.actor_id else None
    except (ValueError, AttributeError, TypeError):
        actor_uuid = None
        diff = {**diff, "actor": audit.actor_id}  # 미인증 sentinel 보존(추적)
    try:
        target_uuid = uuid.UUID(audit.target_id) if audit.target_id else None
    except (ValueError, AttributeError, TypeError):
        target_uuid = None
    return {
        "actor_id": actor_uuid,
        "action": audit.action,
        "target_type": audit.target_type,
        "target_id": target_uuid,
        "diff_redacted": diff,
        "created_at": audit.created_at,
        # Story 5.8: readiness gate 7필드 — source/reason/result(redaction 통과값·기계가독 result).
        "source": audit.source,
        "reason": audit.reason,
        "result": audit.result,
    }


class PostgresAdminActionRepository:
    """async SQLAlchemy 기반 ``AdminActionRepository`` — 전이 UPDATE + audit INSERT 동일 트랜잭션."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ── read ───────────────────────────────────────────────────────────────────
    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        stmt = select(SubscriptionRow).where(SubscriptionRow.id == subscription_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _sub_to_domain(row)

    async def get_target(self, target_id: str) -> MonitoringTarget | None:
        stmt = select(MonitoringTargetRow).where(MonitoringTargetRow.id == target_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _target_to_domain(row)

    async def get_target_platform(self, target_id: str) -> str | None:
        stmt = (
            select(PlatformAccountRow.platform)
            .select_from(MonitoringTargetRow)
            .join(
                PlatformAccountRow,
                (MonitoringTargetRow.platform_account_id == PlatformAccountRow.id)
                & (MonitoringTargetRow.tenant_id == PlatformAccountRow.tenant_id),
            )
            .where(MonitoringTargetRow.id == target_id)
        )
        async with self._session_factory() as session:
            platform = (await session.execute(stmt)).scalar_one_or_none()
        return None if platform is None else str(platform)

    async def get_job(self, job_id: str) -> JobRef | None:
        # tenant scope 는 job→target→tenant 조인으로 도출(jobs 엔 tenant_id 컬럼 없음).
        stmt = (
            select(JobRow, MonitoringTargetRow.tenant_id)
            .outerjoin(MonitoringTargetRow, JobRow.target_id == MonitoringTargetRow.id)
            .where(JobRow.id == job_id)
        )
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).first()
        if row is None:
            return None
        job, tenant_id = row
        return JobRef(
            job_id=str(job.id),
            type=job.type,
            target_id=None if job.target_id is None else str(job.target_id),
            status=job.status,
            tenant_id=None if tenant_id is None else str(tenant_id),
        )

    async def get_held_dispatch(self, dispatch_id: str) -> HeldDispatchRef | None:
        # 열린 질문 #1: HELD Dispatch 영속 표현 미정 → 보수적 미노출(Epic 3/5 reconcile).
        return None

    # ── write + audit(같은 트랜잭션) ────────────────────────────────────────────
    async def transition_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.id == subscription.id)
                .values(status=subscription.status.value)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def transition_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(MonitoringTargetRow)
                .where(MonitoringTargetRow.id == target.id)
                .values(status=target.status.value)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def transition_job(
        self, job_id: str, *, status: str, audit: AuditEntry
    ) -> None:
        async with self._session_factory() as session:
            await session.execute(
                update(JobRow).where(JobRow.id == job_id).values(status=status)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def transition_dispatch(
        self, dispatch_id: str, *, status: str, audit: AuditEntry
    ) -> None:
        # 열린 질문 #1: HELD Dispatch 상태 영속 미정 → 의사결정만 audit(Epic 3/5 reconcile, fail-closed).
        async with self._session_factory() as session:
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def assign_agent(
        self, *, target_id: str, agent_id: str, audit: AuditEntry
    ) -> None:
        # 보수적 affinity: 기존 browser_profile 의 agent_id 를 재바인딩(신규 행/ref 생성 0).
        async with self._session_factory() as session:
            await session.execute(
                update(BrowserProfileRow)
                .where(BrowserProfileRow.target_id == target_id)
                .values(agent_id=agent_id)
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def record_audit(self, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()
