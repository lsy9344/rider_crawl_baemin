"""PostgreSQL ``DashboardRepository`` 구현 — Story 5.6 (AC1·AC4).

:class:`rider_server.admin.dashboard_service.DashboardRepository` 포트의 실 DB 구현. 5.2
``db/base.py`` 의 ``async_sessionmaker`` 를 주입받아 쓰고 새 엔진을 만들지 않는다
(``PostgresChannelRepository``/``PostgresSchedulerRepository`` 선례). async 본문은 **읽기 전용**
DB I/O 만 한다 — ``select`` 만 쓰고 ``commit``/``update``/``insert``/``delete`` 0(대시보드는
상태를 바꾸지 않음, AC). blocking sync 직접 호출 0(async 경계 가드 준수).

"마지막 성공/실패"는 신규 컬럼이 아니라 **파생 집계**다(14표 lock·migration drift 회피):
  - 수집 성공 = ``MAX(snapshots.collected_at) WHERE quality_state='OK'``
  - 전송 성공 = ``MAX(delivery_logs.sent_at) WHERE status='SENT'``(messages→snapshots→target 조인)
  - 실패 사유 = ``jobs.error_code``/``delivery_logs.error_code`` 중 최신 non-null
모든 customer-owned 질의는 ``tenant_id`` 로 scope 한다(cross-tenant 누출 0 — PG-gated 테스트가
seed 후 검증). agents 는 tenant 소유가 아닌 fleet 전역 자원이라 scope 가 없다.

윈도/근사(정밀화는 Story 5.9): Telegram 오류는 최근 :data:`_TELEGRAM_ERROR_WINDOW` 윈도
``TELEGRAM_FAILURE`` 카운트, Kakao lag 은 대기 ``KAKAO_SEND`` job 의 ``now - MIN(run_after)``.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.account import (
    AuthSession,
    MonitoringTarget,
    PlatformAccount,
)
from rider_server.db.models.agent import Agent, BrowserProfile, Job
from rider_server.db.models.messaging import (
    DeliveryLog,
    Message,
    MessengerChannel,
    Snapshot,
)
from rider_server.db.models.tenancy import Tenant
from rider_server.domain import (
    BaeminAuthState,
    DeliveryStatus,
    FailureCategory,
    MonitoringTargetStatus,
    SnapshotQualityState,
)
from rider_server.queue.states import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_TYPE_KAKAO_SEND,
)

from .dashboard_service import (
    AgentHealthFacts,
    AuthRequiredRow,
    ChannelHealthRow,
    DashboardRepository,
    TargetHealthFacts,
)

#: Telegram 전송 오류 집계 윈도(최근 10분 — ops-contract 정합, 정밀화는 5.9).
_TELEGRAM_ERROR_WINDOW = timedelta(minutes=10)

#: 활성(현재 처리 중) job status — Agent 현재 job 판정 스코프.
_ACTIVE_JOB_STATUSES = (JOB_STATUS_CLAIMED, JOB_STATUS_RUNNING)

#: auth_sessions 인증대기로 보는 상태(BaeminAuthState 값).
_AUTH_SESSION_PENDING_STATES = (
    BaeminAuthState.AUTH_REQUIRED.value,
    BaeminAuthState.USER_ACTION_PENDING.value,
)


class PostgresDashboardRepository(DashboardRepository):
    """async SQLAlchemy 기반 읽기 전용 ``DashboardRepository``."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def target_health(
        self, *, tenant_id: str, now: datetime
    ) -> list[TargetHealthFacts]:
        if not tenant_id.strip():
            return []
        # 대상 + 소유 계정(auth_state) + tenant lifecycle 을 tenant scope 로 조인(INACTIVE 제외).
        base_stmt = (
            select(
                MonitoringTarget.id,
                MonitoringTarget.tenant_id,
                MonitoringTarget.name,
                MonitoringTarget.center_name,
                PlatformAccount.platform,
                MonitoringTarget.interval_minutes,
                PlatformAccount.id.label("account_id"),
                PlatformAccount.auth_state,
                Tenant.status.label("lifecycle_state"),
            )
            .join(
                PlatformAccount,
                MonitoringTarget.platform_account_id == PlatformAccount.id,
            )
            .join(Tenant, MonitoringTarget.tenant_id == Tenant.id)
            .where(
                MonitoringTarget.tenant_id == tenant_id,
                PlatformAccount.tenant_id == MonitoringTarget.tenant_id,
                MonitoringTarget.status != MonitoringTargetStatus.INACTIVE.value,
            )
        )
        facts: list[TargetHealthFacts] = []
        async with self._session_factory() as session:
            rows = (await session.execute(base_stmt)).all()
            for row in rows:
                target_id = str(row.id)
                last_success_at = await self._last_collect_success(session, target_id)
                last_delivery_at = await self._last_delivery_success(session, target_id)
                last_failure_code = await self._latest_failure_code(session, target_id)
                auth_pending = await self._auth_session_pending(
                    session, str(row.account_id)
                )
                facts.append(
                    TargetHealthFacts(
                        target_id=target_id,
                        tenant_id=str(row.tenant_id),
                        name=row.name,
                        center_name=row.center_name,
                        platform=row.platform,
                        interval_minutes=row.interval_minutes,
                        last_success_at=last_success_at,
                        last_delivery_at=last_delivery_at,
                        last_failure_code=last_failure_code,
                        account_auth_state=row.auth_state,
                        lifecycle_state=row.lifecycle_state,
                        auth_session_pending=auth_pending,
                    )
                )
        return facts

    async def _last_collect_success(
        self, session: AsyncSession, target_id: str
    ) -> datetime | None:
        stmt = select(func.max(Snapshot.collected_at)).where(
            Snapshot.target_id == target_id,
            Snapshot.quality_state == SnapshotQualityState.OK.value,
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _last_delivery_success(
        self, session: AsyncSession, target_id: str
    ) -> datetime | None:
        # delivery_logs → messages → snapshots → target 조인으로 target 별 전송 성공.
        stmt = (
            select(func.max(DeliveryLog.sent_at))
            .join(Message, DeliveryLog.message_id == Message.id)
            .join(Snapshot, Message.snapshot_id == Snapshot.id)
            .where(
                Snapshot.target_id == target_id,
                DeliveryLog.status == DeliveryStatus.SENT.value,
            )
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def _latest_failure_code(
        self, session: AsyncSession, target_id: str
    ) -> str | None:
        # jobs(활동시각=claimed_at/run_after 근사)과 delivery_logs(sent_at) 중 더 최신 non-null
        # error_code 를 고른다. 종료시각 컬럼 부재(14표) — 근사, 정밀화는 5.9.
        job_stmt = (
            select(
                Job.error_code,
                func.coalesce(Job.claimed_at, Job.run_after).label("ts"),
            )
            .where(Job.target_id == target_id, Job.error_code.is_not(None))
            .order_by(func.coalesce(Job.claimed_at, Job.run_after).desc().nullslast())
            .limit(1)
        )
        delivery_stmt = (
            select(DeliveryLog.error_code, DeliveryLog.sent_at.label("ts"))
            .join(Message, DeliveryLog.message_id == Message.id)
            .join(Snapshot, Message.snapshot_id == Snapshot.id)
            .where(
                Snapshot.target_id == target_id,
                DeliveryLog.error_code.is_not(None),
            )
            .order_by(DeliveryLog.sent_at.desc().nullslast())
            .limit(1)
        )
        job_row = (await session.execute(job_stmt)).first()
        delivery_row = (await session.execute(delivery_stmt)).first()
        return _pick_latest_code(job_row, delivery_row)

    async def _auth_session_pending(
        self, session: AsyncSession, account_id: str
    ) -> bool:
        stmt = (
            select(func.count())
            .select_from(AuthSession)
            .where(
                AuthSession.account_id == account_id,
                AuthSession.state.in_(_AUTH_SESSION_PENDING_STATES),
                AuthSession.resolved_at.is_(None),
            )
        )
        return int((await session.execute(stmt)).scalar_one()) > 0

    async def agent_health(self, *, now: datetime) -> list[AgentHealthFacts]:
        # agents 는 fleet 전역(tenant 소유 아님) — scope 없음.
        agents_stmt = select(
            Agent.id,
            Agent.name,
            Agent.version,
            Agent.last_heartbeat_at,
            Agent.capacity_json,
        )
        facts: list[AgentHealthFacts] = []
        async with self._session_factory() as session:
            rows = (await session.execute(agents_stmt)).all()
            for row in rows:
                current_job_type = (
                    await session.execute(
                        select(Job.type)
                        .where(
                            Job.agent_id == row.id,
                            Job.status.in_(_ACTIVE_JOB_STATUSES),
                        )
                        .order_by(Job.claimed_at.desc().nullslast())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                data = row.capacity_json or {}
                capabilities = tuple(data.get("capabilities", []) or [])
                facts.append(
                    AgentHealthFacts(
                        agent_id=str(row.id),
                        name=row.name,
                        version=row.version,
                        last_heartbeat_at=row.last_heartbeat_at,
                        current_job_type=current_job_type,
                        capabilities=capabilities,
                    )
                )
        return facts

    async def channel_health(
        self, *, tenant_id: str, now: datetime
    ) -> ChannelHealthRow:
        if not tenant_id.strip():
            return ChannelHealthRow(kakao_queue_lag_seconds=0, telegram_error_count=0)
        kakao_stmt = (
            select(func.min(Job.run_after))
            .join(MonitoringTarget, Job.target_id == MonitoringTarget.id)
            .where(
                MonitoringTarget.tenant_id == tenant_id,
                Job.type == JOB_TYPE_KAKAO_SEND,
                Job.status == JOB_STATUS_PENDING,
            )
        )
        since = now - _TELEGRAM_ERROR_WINDOW
        telegram_stmt = (
            select(func.count())
            .select_from(DeliveryLog)
            .join(MessengerChannel, DeliveryLog.channel_id == MessengerChannel.id)
            .where(
                MessengerChannel.tenant_id == tenant_id,
                DeliveryLog.error_code == FailureCategory.TELEGRAM_FAILURE.value,
                # sent_at 미기록(전송 실패) 도 최근 오류로 포함, 그 외엔 윈도 내만.
                (DeliveryLog.sent_at.is_(None)) | (DeliveryLog.sent_at >= since),
            )
        )
        async with self._session_factory() as session:
            oldest_run_after = (await session.execute(kakao_stmt)).scalar_one_or_none()
            telegram_errors = int((await session.execute(telegram_stmt)).scalar_one())
        kakao_lag = 0
        if oldest_run_after is not None:
            kakao_lag = max(0, int((now - oldest_run_after).total_seconds()))
        return ChannelHealthRow(
            kakao_queue_lag_seconds=kakao_lag,
            telegram_error_count=telegram_errors,
        )

    async def auth_required(self, *, tenant_id: str) -> list[AuthRequiredRow]:
        if not tenant_id.strip():
            return []
        # 계정 인증 필요(AUTH_REQUIRED) 대상/프로필을 tenant scope 로 조인(AC4).
        account_stmt = (
            select(
                MonitoringTarget.tenant_id,
                MonitoringTarget.id.label("target_id"),
                MonitoringTarget.name.label("target_name"),
                BrowserProfile.id.label("profile_id"),
            )
            .select_from(PlatformAccount)
            .join(
                MonitoringTarget,
                MonitoringTarget.platform_account_id == PlatformAccount.id,
            )
            .join(
                BrowserProfile,
                BrowserProfile.target_id == MonitoringTarget.id,
                isouter=True,
            )
            .where(
                PlatformAccount.tenant_id == tenant_id,
                MonitoringTarget.tenant_id == PlatformAccount.tenant_id,
                PlatformAccount.auth_state == BaeminAuthState.AUTH_REQUIRED.value,
            )
        )
        rows: list[AuthRequiredRow] = []
        async with self._session_factory() as session:
            for row in (await session.execute(account_stmt)).all():
                rows.append(
                    AuthRequiredRow(
                        tenant_id=str(row.tenant_id),
                        target_id=str(row.target_id) if row.target_id else None,
                        profile_id=str(row.profile_id) if row.profile_id else None,
                        reason="ACCOUNT_AUTH_REQUIRED",
                        target_name=str(row.target_name) if row.target_name else None,
                    )
                )
            # auth_sessions 인증대기(미해소) — 계정을 tenant scope 로 묶어 도출.
            session_stmt = (
                select(
                    PlatformAccount.tenant_id,
                    AuthSession.account_id,
                )
                .select_from(AuthSession)
                .join(PlatformAccount, AuthSession.account_id == PlatformAccount.id)
                .where(
                    PlatformAccount.tenant_id == tenant_id,
                    AuthSession.state.in_(_AUTH_SESSION_PENDING_STATES),
                    AuthSession.resolved_at.is_(None),
                )
            )
            for row in (await session.execute(session_stmt)).all():
                rows.append(
                    AuthRequiredRow(
                        tenant_id=str(row.tenant_id),
                        target_id=None,
                        profile_id=None,
                        reason="AUTH_SESSION_PENDING",
                    )
                )
        return rows


def _pick_latest_code(job_row, delivery_row) -> str | None:
    """jobs/delivery_logs 후보 중 더 최신 ts 의 ``error_code`` 를 고른다(둘 다 없으면 None).

    ts(``None``)는 가장 오래된 것으로 취급하고, 동률/모두 None 이면 job 을 우선한다(결정적).
    """

    if job_row is None and delivery_row is None:
        return None
    if delivery_row is None:
        return job_row.error_code
    if job_row is None:
        return delivery_row.error_code
    job_ts = job_row.ts
    delivery_ts = delivery_row.ts
    if delivery_ts is not None and (job_ts is None or delivery_ts > job_ts):
        return delivery_row.error_code
    return job_row.error_code
