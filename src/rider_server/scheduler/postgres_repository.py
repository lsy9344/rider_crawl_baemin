"""PostgreSQL ``SchedulerRepository`` 구현 — Story 5.4 (AC1·AC2·AC3·AC4).

:class:`SchedulerRepository` 포트의 실 DB 구현. 단일 PostgreSQL(queue 도 같은 ``jobs`` 테이블,
architecture.md:140,496)에서 due 질의·게이트 입력 로드·플랫폼 실패 집계·활성 job 존재·capacity
스냅샷·**conditional advance 멱등 선점**을 async SQLAlchemy 로 수행한다. async 본문은 DB I/O 만
하고 blocking sync 직접 호출은 하지 않는다(async 경계 가드 준수).

멱등성(AC4): :meth:`claim_due_target` 는 ``UPDATE … WHERE next_run_at <= now`` 한 문장으로
대상을 선점해(``rowcount==1`` 이 win), 동시 tick/두 worker 가 같은 due 윈도를 돌아도 한 tick 만
``next_run_at`` 을 전진시키고 enqueue 한다 — 중복 due 작업이 생기지 않는다(5.3 ``FOR UPDATE
SKIP LOCKED`` 와 동형 사고: 경합을 DB 한 곳에서 차단).

breaker 윈도 집계는 ``jobs.claimed_at`` 을 활동 시각으로 근사한다(14테이블 계약에 job 종료
시각 컬럼이 없음 — 정밀 윈도/metric emission 은 Story 5.9, 본 구현은 자체 집계로 동작). 이 근사는
PG-gated 테스트에서만 실행되고, always-run in-memory 테스트가 breaker 임계 의미를 결정적으로
잠근다.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.account import MonitoringTarget, PlatformAccount
from rider_server.admin.severity import is_agent_online
from rider_server.db.models.agent import Agent, BrowserProfile, Job
from rider_server.db.models.tenancy import Subscription, Tenant
from rider_server.domain import (
    CustomerLifecycleState,
    MonitoringTargetStatus,
    SubscriptionStatus,
)
from rider_server.queue.backend import QueueBackend
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_FAILED,
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
)

from . import policy
from .service import DueTarget, SchedulerRepository, TenantGate

# scheduler 가 생성/평가하는 CrawlJob type(정본 6종 중 2종). 활성 job/실패 집계 스코프.
_CRAWL_JOB_TYPES = (JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG)
_ACTIVE_JOB_STATUSES = (JOB_STATUS_PENDING, JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)


def _to_subscription_status(value: str | None) -> SubscriptionStatus | None:
    """DB 문자열 → ``SubscriptionStatus``(미매핑/None 은 None → 게이트 fail-closed)."""
    if value is None:
        return None
    try:
        return SubscriptionStatus(value)
    except ValueError:
        return None


def _to_lifecycle_status(value: str | None) -> CustomerLifecycleState | None:
    """DB 문자열 → ``CustomerLifecycleState``(미매핑/None 은 None → 비활성 차단)."""
    if value is None:
        return None
    try:
        return CustomerLifecycleState(value)
    except ValueError:
        return None


def _capacity_from_agent_rows(
    rows,
    *,
    aggregate_in_flight: int,
    in_flight_by_job_type: dict[str, int] | None = None,
    now: datetime,
) -> policy.CapacityPolicy:
    """Online Agent rows 만 capacity 로 집계한다."""

    aggregate_capacity = 0
    capacity_by_job_type: dict[str, int] = {}
    capabilities: set[str] = set()
    for row in rows:
        if not is_agent_online(row.last_heartbeat_at, now):
            continue
        data = row.capacity_json or {}
        max_in_flight = int(data.get("max_in_flight", 0))
        aggregate_capacity += max_in_flight
        row_capabilities = data.get("capabilities", []) or []
        capabilities.update(row_capabilities)
        for job_type in row_capabilities:
            capacity_by_job_type[str(job_type)] = (
                capacity_by_job_type.get(str(job_type), 0) + max_in_flight
            )
    return policy.CapacityPolicy(
        aggregate_capacity=aggregate_capacity,
        aggregate_in_flight=aggregate_in_flight,
        capabilities=frozenset(capabilities),
        capacity_by_job_type=capacity_by_job_type,
        in_flight_by_job_type=dict(in_flight_by_job_type or {}),
    )


class PostgresSchedulerRepository(SchedulerRepository):
    """async SQLAlchemy 기반 ``SchedulerRepository``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def due_targets(self, *, now: datetime, limit: int) -> list[DueTarget]:
        if limit <= 0:
            return []
        assigned_agent_id = (
            select(BrowserProfile.agent_id)
            .where(BrowserProfile.target_id == MonitoringTarget.id)
            .order_by(BrowserProfile.id.asc())
            .limit(1)
            .scalar_subquery()
        )
        stmt = (
            select(
                MonitoringTarget.id,
                MonitoringTarget.tenant_id,
                MonitoringTarget.platform_account_id,
                MonitoringTarget.url,
                MonitoringTarget.center_name,
                PlatformAccount.platform,
                PlatformAccount.username,
                PlatformAccount.password,
                PlatformAccount.verification_email_address,
                PlatformAccount.verification_email_app_password,
                PlatformAccount.verification_email_subject_keyword,
                PlatformAccount.verification_email_sender_keyword,
                MonitoringTarget.interval_minutes,
                MonitoringTarget.next_run_at,
                assigned_agent_id.label("assigned_agent_id"),
            )
            .join(
                PlatformAccount,
                MonitoringTarget.platform_account_id == PlatformAccount.id,
            )
            .where(
                PlatformAccount.tenant_id == MonitoringTarget.tenant_id,
                MonitoringTarget.status == MonitoringTargetStatus.ACTIVE.value,
                (MonitoringTarget.next_run_at.is_(None))
                | (MonitoringTarget.next_run_at <= now),
            )
            .order_by(
                MonitoringTarget.next_run_at.asc().nullsfirst(),
                MonitoringTarget.id.asc(),
            )
            .limit(limit)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
        return [
            DueTarget(
                target_id=str(row.id),
                tenant_id=str(row.tenant_id),
                platform=row.platform,
                interval_minutes=row.interval_minutes,
                next_run_at=row.next_run_at,
                platform_account_id=str(row.platform_account_id),
                primary_url=row.url,
                expected_display_name=row.center_name,
                username=row.username,
                password=row.password,
                verification_email_address=row.verification_email_address,
                verification_email_app_password=row.verification_email_app_password,
                verification_email_subject_keyword=row.verification_email_subject_keyword,
                verification_email_sender_keyword=row.verification_email_sender_keyword,
                assigned_agent_id=str(row.assigned_agent_id)
                if row.assigned_agent_id is not None
                else "",
            )
            for row in rows
        ]

    async def tenant_gate(self, tenant_id: str) -> TenantGate:
        async with self._session_factory() as session:
            lifecycle_value = (
                await session.execute(
                    select(Tenant.status).where(Tenant.id == tenant_id)
                )
            ).scalar_one_or_none()
            subscription_value = (
                await session.execute(
                    select(Subscription.status).where(
                        Subscription.tenant_id == tenant_id
                    )
                )
            ).scalars().first()
        return TenantGate(
            subscription_status=_to_subscription_status(subscription_value),
            lifecycle_status=_to_lifecycle_status(lifecycle_value),
        )

    async def tenant_gates(self, tenant_ids: list[str]) -> dict[str, TenantGate]:
        unique_ids = list(dict.fromkeys(tid for tid in tenant_ids if tid))
        if not unique_ids:
            return {}

        async with self._session_factory() as session:
            tenant_rows = (
                await session.execute(
                    select(Tenant.id, Tenant.status).where(Tenant.id.in_(unique_ids))
                )
            ).all()
            subscription_rows = (
                await session.execute(
                    select(Subscription.tenant_id, Subscription.status).where(
                        Subscription.tenant_id.in_(unique_ids)
                    )
                )
            ).all()

        lifecycle_by_tenant = {
            str(row.id): _to_lifecycle_status(row.status) for row in tenant_rows
        }
        subscription_by_tenant: dict[str, SubscriptionStatus | None] = {}
        for row in subscription_rows:
            subscription_by_tenant.setdefault(
                str(row.tenant_id), _to_subscription_status(row.status)
            )
        return {
            tenant_id: TenantGate(
                subscription_status=subscription_by_tenant.get(tenant_id),
                lifecycle_status=lifecycle_by_tenant.get(tenant_id),
            )
            for tenant_id in unique_ids
        }

    async def platform_failure_window(
        self, platform: str, *, since: datetime, now: datetime
    ) -> tuple[int, int]:
        try:
            job_type = policy.crawl_job_type_for(platform)
        except ValueError:
            return (0, 0)
        # claimed_at 을 활동 시각으로 근사(종료 시각 컬럼 부재 — 5.9 정밀화).
        window = (Job.type == job_type) & (Job.claimed_at.is_not(None)) & (
            Job.claimed_at >= since
        )
        total_stmt = select(func.count()).select_from(Job).where(window)
        fail_stmt = (
            select(func.count())
            .select_from(Job)
            .where(window & (Job.status == JOB_STATUS_FAILED))
        )
        async with self._session_factory() as session:
            total = (await session.execute(total_stmt)).scalar_one()
            failures = (await session.execute(fail_stmt)).scalar_one()
        return (int(total), int(failures))

    async def has_active_crawl_job(self, target_id: str) -> bool:
        stmt = (
            select(func.count())
            .select_from(Job)
            .where(
                Job.target_id == target_id,
                Job.type.in_(_CRAWL_JOB_TYPES),
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
        )
        async with self._session_factory() as session:
            count = (await session.execute(stmt)).scalar_one()
        return int(count) > 0

    async def active_crawl_job_target_ids(self, target_ids: list[str]) -> set[str]:
        unique_ids = list(dict.fromkeys(tid for tid in target_ids if tid))
        if not unique_ids:
            return set()
        stmt = (
            select(Job.target_id)
            .where(
                Job.target_id.in_(unique_ids),
                Job.type.in_(_CRAWL_JOB_TYPES),
                Job.status.in_(_ACTIVE_JOB_STATUSES),
            )
            .distinct()
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return {str(target_id) for target_id in rows}

    async def capacity_snapshot(self, *, now: datetime) -> policy.CapacityPolicy:
        """``agents.capacity_json`` 집계.

        해석(문서화된 계약): ``capacity_json = {"max_in_flight": int, "capabilities": [job_type]}``.
        ``aggregate_capacity`` = online Agent ``max_in_flight`` 합, ``capabilities`` = 합집합.
        ``aggregate_in_flight`` = 현재 활성 CrawlJob 수(scheduler 가 만드는 type 스코프).
        """
        async with self._session_factory() as session:
            agents = (
                await session.execute(select(Agent.capacity_json, Agent.last_heartbeat_at))
            ).all()
            in_flight = (
                await session.execute(
                    select(func.count())
                    .select_from(Job)
                    .where(
                        Job.type.in_(_CRAWL_JOB_TYPES),
                        Job.status.in_(_ACTIVE_JOB_STATUSES),
                    )
                )
            ).scalar_one()
            in_flight_rows = (
                await session.execute(
                    select(Job.type, func.count())
                    .select_from(Job)
                    .where(
                        Job.type.in_(_CRAWL_JOB_TYPES),
                        Job.status.in_(_ACTIVE_JOB_STATUSES),
                    )
                    .group_by(Job.type)
                )
            ).all()
        return _capacity_from_agent_rows(
            agents,
            aggregate_in_flight=int(in_flight),
            in_flight_by_job_type={str(row[0]): int(row[1]) for row in in_flight_rows},
            now=now,
        )

    async def claim_due_target(
        self, target_id: str, *, now: datetime, next_run_at: datetime
    ) -> bool:
        stmt = (
            update(MonitoringTarget)
            .where(
                MonitoringTarget.id == target_id,
                MonitoringTarget.status == MonitoringTargetStatus.ACTIVE.value,
                (MonitoringTarget.next_run_at.is_(None))
                | (MonitoringTarget.next_run_at <= now),
            )
            .values(next_run_at=next_run_at, last_enqueued_at=now)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
        return (result.rowcount or 0) == 1

    async def claim_due_target_and_enqueue(
        self,
        queue_backend: QueueBackend,
        target: DueTarget,
        *,
        job_type: str,
        payload_json: dict[str, object],
        now: datetime,
        next_run_at: datetime,
    ) -> str | None:
        del queue_backend
        job_id = uuid.uuid4()
        stmt = (
            update(MonitoringTarget)
            .where(
                MonitoringTarget.id == target.target_id,
                MonitoringTarget.status == MonitoringTargetStatus.ACTIVE.value,
                (MonitoringTarget.next_run_at.is_(None))
                | (MonitoringTarget.next_run_at <= now),
            )
            .values(next_run_at=next_run_at, last_enqueued_at=now)
        )
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(stmt)
                if (result.rowcount or 0) != 1:
                    return None
                session.add(
                    Job(
                        id=job_id,
                        type=job_type,
                        target_id=uuid.UUID(target.target_id),
                        assigned_agent_id=uuid.UUID(target.assigned_agent_id)
                        if target.assigned_agent_id
                        else None,
                        agent_id=None,
                        status=JOB_STATUS_PENDING,
                        run_after=now,
                        attempts=0,
                        error_code=None,
                        payload_json=payload_json,
                    )
                )
        return str(job_id)

    async def release_due_target(
        self,
        target_id: str,
        *,
        claimed_next_run_at: datetime,
        restore_next_run_at: datetime | None,
    ) -> bool:
        stmt = (
            update(MonitoringTarget)
            .where(
                MonitoringTarget.id == target_id,
                MonitoringTarget.status == MonitoringTargetStatus.ACTIVE.value,
                MonitoringTarget.next_run_at == claimed_next_run_at,
            )
            .values(next_run_at=restore_next_run_at)
        )
        async with self._session_factory() as session:
            result = await session.execute(stmt)
            await session.commit()
        return (result.rowcount or 0) == 1
