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

from rider_server.admin.severity import is_agent_online
from rider_server.db.base import (
    AUTH_ENQUEUE_LOCK_NAMESPACE,
    acquire_xact_advisory_lock,
    advisory_lock_key_for_uuid,
)
from rider_server.db.models.account import MonitoringTarget as MonitoringTargetRow
from rider_server.db.models.account import PlatformAccount as PlatformAccountRow
from rider_server.db.models.agent import Agent as AgentRow
from rider_server.db.models.agent import BrowserProfile as BrowserProfileRow
from rider_server.db.models.agent import Job as JobRow
from rider_server.db.models.audit import AuditLog as AuditLogRow
from rider_server.db.models.tenancy import Subscription as SubscriptionRow
from rider_server.domain import (
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Subscription,
    SubscriptionStatus,
)

from .admin_action_service import AuditEntry, HeldDispatchRef, JobRef
from .admin_action_service import AdminActionNotFound
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RETRY,
    JOB_STATUS_RUNNING,
    JOB_TYPE_AUTH_COUPANG_2FA,
    JOB_TYPE_OPEN_AUTH_BROWSER,
)

#: 인증 시작 계열 job type — 같은 대상에 둘 중 하나라도 진행 중이면 중복 인증 작업을 막는다
#: (auto AUTH_COUPANG_2FA 와 manual OPEN_AUTH_BROWSER 가 동시에 돌지 않게 — Task 5 Step 3).
_AUTH_JOB_TYPES = (JOB_TYPE_AUTH_COUPANG_2FA, JOB_TYPE_OPEN_AUTH_BROWSER)
_INACTIVE_BROWSER_PROFILE_STATE = "INACTIVE"


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _assigned_agent_id_from_agent_capacity(
    agent_rows,
    *,
    target_id: uuid.UUID,
    job_type: str,
    now,
) -> uuid.UUID | None:
    """Pick the agent for a manual job from recent heartbeat capacity data."""

    online_rows = [
        row for row in agent_rows if is_agent_online(row.last_heartbeat_at, now)
    ]
    target_text = str(target_id)
    for row in online_rows:
        data = row.capacity_json or {}
        profiles = data.get("browser_profiles") if isinstance(data, dict) else None
        if not isinstance(profiles, list):
            continue
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            if str(profile.get("target_id") or "") != target_text:
                continue
            if str(profile.get("state") or "").upper() == _INACTIVE_BROWSER_PROFILE_STATE:
                continue
            return _uuid_or_none(profile.get("agent_id")) or _uuid_or_none(row.id)

    capable_rows = []
    for row in online_rows:
        data = row.capacity_json or {}
        capabilities = data.get("capabilities") if isinstance(data, dict) else None
        if isinstance(capabilities, list) and job_type in capabilities:
            capable_rows.append(row)
    if len(capable_rows) == 1:
        return _uuid_or_none(capable_rows[0].id)
    return None


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
        schedule_enabled=row.schedule_enabled,
        start_time=row.start_time,
        stop_time=row.stop_time,
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

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None:
        stmt = select(PlatformAccountRow).where(PlatformAccountRow.id == account_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        if row is None:
            return None
        return PlatformAccount(
            id=str(row.id),
            tenant_id=str(row.tenant_id),
            platform=Platform(str(row.platform)),
            label=row.label,
            username=row.username,
            password=row.password,
            verification_email_address=row.verification_email_address,
            verification_email_app_password=row.verification_email_app_password,
            verification_email_subject_keyword=row.verification_email_subject_keyword,
            verification_email_sender_keyword=row.verification_email_sender_keyword,
        )

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

    async def list_tenant_active_targets(
        self, tenant_id: str
    ) -> list[MonitoringTarget]:
        stmt = select(MonitoringTargetRow).where(
            MonitoringTargetRow.tenant_id == tenant_id,
            MonitoringTargetRow.status == MonitoringTargetStatus.ACTIVE.value,
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_target_to_domain(row) for row in rows]

    # ── write + audit(같은 트랜잭션) ────────────────────────────────────────────
    async def transition_subscription(
        self,
        subscription: Subscription,
        audit: AuditEntry,
        *,
        schedule_resets=None,
    ) -> None:
        # no-catchup: 구독 복구 시 subscription UPDATE + tenant 의 ACTIVE targets next_run_at reset
        # + audit 를 **한 세션·한 commit** 으로 묶는다(부분 반영 없음).
        async with self._session_factory() as session:
            result = await session.execute(
                update(SubscriptionRow)
                .where(SubscriptionRow.id == subscription.id)
                .values(status=subscription.status.value)
            )
            if result.rowcount == 0:
                raise AdminActionNotFound("subscription", subscription.id)
            for target_id, next_run_at in (schedule_resets or {}).items():
                await session.execute(
                    update(MonitoringTargetRow)
                    .where(MonitoringTargetRow.id == target_id)
                    .values(next_run_at=next_run_at)
                )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def transition_target(
        self,
        target: MonitoringTarget,
        audit: AuditEntry,
        *,
        schedule_reset_to=None,
    ) -> None:
        # no-catchup: schedule_reset_to 가 있으면 status 전이와 같은 UPDATE 로 next_run_at 을 민다
        # (같은 트랜잭션 — 전이만 되고 schedule 이 안 밀리는 부분 반영 없음). last_enqueued_at/
        # last_success_at 은 건드리지 않는다(실제 enqueue 아님, 성공 이력은 snapshots 파생).
        values = {"status": target.status.value}
        if schedule_reset_to is not None:
            values["next_run_at"] = schedule_reset_to
        async with self._session_factory() as session:
            result = await session.execute(
                update(MonitoringTargetRow)
                .where(MonitoringTargetRow.id == target.id)
                .values(**values)
            )
            if result.rowcount == 0:
                raise AdminActionNotFound("monitoring_target", target.id)
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def transition_job(
        self, job_id: str, *, status: str, audit: AuditEntry
    ) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                update(JobRow).where(JobRow.id == job_id).values(status=status)
            )
            if result.rowcount == 0:
                raise AdminActionNotFound("job", job_id)
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
            result = await session.execute(
                update(BrowserProfileRow)
                .where(BrowserProfileRow.target_id == target_id)
                .values(agent_id=agent_id)
            )
            if result.rowcount == 0:
                raise AdminActionNotFound("browser_profile", target_id)
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def record_audit(self, audit: AuditEntry) -> None:
        async with self._session_factory() as session:
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()

    async def enqueue_manual_job(
        self,
        *,
        job_id: str,
        job_type: str,
        target_id: str,
        payload_json: dict,
        audit: AuditEntry,
        now,
    ) -> str:
        active_statuses = (
            JOB_STATUS_PENDING,
            JOB_STATUS_CLAIMED,
            JOB_STATUS_RUNNING,
            JOB_STATUS_RETRY,
        )
        job_uuid = uuid.UUID(str(job_id))
        target_uuid = uuid.UUID(str(target_id))
        async with self._session_factory() as session:
            locked_target = (
                await session.execute(
                    select(
                        MonitoringTargetRow.id,
                        MonitoringTargetRow.platform_account_id,
                    )
                    .where(MonitoringTargetRow.id == target_uuid)
                    .with_for_update()
                )
            ).first()
            if locked_target is None:
                raise AdminActionNotFound("monitoring_target", target_id)
            platform_account_id = locked_target.platform_account_id
            # 인증 시작 job 은 계정 단위 advisory lock 으로 동시 admin action 을 직렬화한다 — 같은
            # 계정의 **다른 target** 에서 동시에 인증 시작이 들어오면 각자 자기 target row 만 잠가
            # sibling 을 못 보고 중복 auth job 을 만들 수 있다(검토 High). scheduler 자동 복구와
            # 같은 네임스페이스/키라 둘이 동시에 들어와도 직렬화된다(중복 OTP 요청 0).
            if job_type in _AUTH_JOB_TYPES and platform_account_id is not None:
                await acquire_xact_advisory_lock(
                    session,
                    namespace=AUTH_ENQUEUE_LOCK_NAMESPACE,
                    key=advisory_lock_key_for_uuid(platform_account_id),
                )
            assigned_agent_id = (
                await session.execute(
                    select(BrowserProfileRow.agent_id)
                    .where(BrowserProfileRow.target_id == target_uuid)
                    .order_by(BrowserProfileRow.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if assigned_agent_id is None:
                agent_rows = (
                    await session.execute(
                        select(
                            AgentRow.id,
                            AgentRow.last_heartbeat_at,
                            AgentRow.capacity_json,
                        )
                    )
                ).all()
                assigned_agent_id = _assigned_agent_id_from_agent_capacity(
                    agent_rows,
                    target_id=target_uuid,
                    job_type=job_type,
                    now=now,
                )
            # 인증 시작 job(AUTH_COUPANG_2FA/OPEN_AUTH_BROWSER)은 둘을 한 묶음으로 중복 차단한다.
            # 그 외 job(test crawl 등)은 기존처럼 같은 type 만 본다.
            duplicate_types = (
                _AUTH_JOB_TYPES if job_type in _AUTH_JOB_TYPES else (job_type,)
            )
            # 인증 job 의 중복 검사는 **계정 단위**다 — 여러 target 이 같은 쿠팡 계정/메일함을
            # 공유할 수 있어 target 만 보면 같은 계정에 2FA job 이 둘 생긴다(검토 High). 같은
            # platform_account_id 의 어느 target 에든 active auth job 이 있으면 차단한다. 그 외
            # job 은 기존처럼 이 target 만 본다.
            if job_type in _AUTH_JOB_TYPES and platform_account_id is not None:
                target_scope = JobRow.target_id.in_(
                    select(MonitoringTargetRow.id).where(
                        MonitoringTargetRow.platform_account_id == platform_account_id
                    )
                )
            else:
                target_scope = JobRow.target_id == target_uuid
            existing = (
                await session.execute(
                    select(JobRow.id)
                    .where(
                        target_scope,
                        JobRow.type.in_(duplicate_types),
                        JobRow.status.in_(active_statuses),
                    )
                    .limit(1)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if existing is not None:
                raise ValueError("active manual job already exists")
            session.add(
                JobRow(
                    id=job_uuid,
                    type=job_type,
                    target_id=target_uuid,
                    assigned_agent_id=assigned_agent_id,
                    payload_json=payload_json,
                    agent_id=None,
                    status=JOB_STATUS_PENDING,
                    run_after=None,
                    attempts=0,
                )
            )
            await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
            await session.commit()
        return str(job_uuid)
