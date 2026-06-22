"""PostgreSQL ``MetricsRepository`` 구현 — Story 5.9 (AC1).

:class:`rider_server.metrics.service.MetricsRepository` 포트의 실 DB 구현. 5.2 ``db/base.py``
의 ``async_sessionmaker`` 를 주입받아 쓰고 새 엔진을 만들지 않는다(``PostgresDashboardRepository``
선례). async 본문은 **읽기 전용** DB I/O 만 한다 — ``select`` 만 쓰고 ``commit``/``update``/
``insert``/``delete`` 0(지표 레이어는 상태를 바꾸지 않음). blocking sync 직접 호출 0.

**기존 집계 compose(신규 쿼리 최소화):**
  - crawl 실패율: 5.4 ``SchedulerRepository.platform_failure_window`` 를 **재사용**(동일 윈도/
    임계 정본과 일치 — 평행 쿼리 작성 금지).
  - kakao lag·telegram error: 5.6 channel_health 집계 패턴을 **fleet 집계**(전 tenant 합/
    최댓값)로 올린다 — 대시보드는 tenant scope 지만 지표 엔드포인트는 비식별 fleet 수치.
  - heartbeat/freshness/auth_required: 5.6 파생 집계를 fleet **카운트/수치**로만 도출(개별
    timestamp·이름·target_id 노출 금지).

신규 DB 컬럼/테이블 **추가 0**(14표 lock) — 모든 지표는 기존 테이블 파생 집계다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.account import AuthSession, MonitoringTarget, PlatformAccount
from rider_server.db.models.agent import Agent, Job
from rider_server.db.models.messaging import DeliveryLog, Snapshot
from rider_server.domain import (
    BaeminAuthState,
    FailureCategory,
    MonitoringTargetStatus,
    Platform,
    SnapshotQualityState,
)
from rider_server.queue.states import JOB_STATUS_PENDING, JOB_TYPE_KAKAO_SEND
from rider_server.scheduler.postgres_repository import PostgresSchedulerRepository

from .service import (
    CrawlWindowFact,
    FreshnessFact,
    HeartbeatFact,
    MetricsRepository,
)

#: crawl 실패율 집계 대상 플랫폼(정본 2종). scheduler 윈도 집계와 동일 스코프.
_CRAWL_PLATFORMS = (Platform.BAEMIN.value, Platform.COUPANG.value)


class PostgresMetricsRepository(MetricsRepository):
    """async SQLAlchemy 기반 읽기 전용 ``MetricsRepository``(fleet 비식별 집계)."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        # crawl 실패율 윈도 집계는 scheduler 정본을 그대로 재사용(재구현 금지).
        self._scheduler_repo = PostgresSchedulerRepository(session_factory)

    async def agent_heartbeats(self, *, now: datetime) -> list[HeartbeatFact]:
        # agents 는 fleet 전역(tenant 소유 아님) — scope 없음. 이름/id 미수집(비식별).
        stmt = select(Agent.last_heartbeat_at)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
        return [HeartbeatFact(last_heartbeat_at=row.last_heartbeat_at) for row in rows]

    async def target_freshness(self, *, now: datetime) -> list[FreshnessFact]:
        # 활성 대상별 interval + MAX(snapshots.collected_at WHERE quality_state='OK').
        # OK 가 없으면 last_success_at=None → classify_freshness 가 최소 WARNING 으로 본다.
        # 이름/target_id 는 SELECT 하지 않는다(비식별 — warning/critical 카운트만 쓴다).
        stmt = (
            select(
                MonitoringTarget.interval_minutes,
                func.max(Snapshot.collected_at).label("last_success_at"),
            )
            .select_from(MonitoringTarget)
            .join(
                Snapshot,
                (Snapshot.target_id == MonitoringTarget.id)
                & (Snapshot.quality_state == SnapshotQualityState.OK.value),
                isouter=True,
            )
            .where(MonitoringTarget.status != MonitoringTargetStatus.INACTIVE.value)
            .group_by(MonitoringTarget.id, MonitoringTarget.interval_minutes)
        )
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).all()
        return [
            FreshnessFact(
                interval_minutes=row.interval_minutes,
                last_success_at=row.last_success_at,
            )
            for row in rows
        ]

    async def crawl_windows(
        self, *, since: datetime, now: datetime
    ) -> list[CrawlWindowFact]:
        # 5.4 정본 재사용: platform_failure_window 가 (total, failures) 를 같은 15분/claimed_at
        # 윈도로 집계한다 — 신규 쿼리 작성 금지(evaluate_breaker 정본과 일치).
        facts: list[CrawlWindowFact] = []
        for platform in _CRAWL_PLATFORMS:
            total, failures = await self._scheduler_repo.platform_failure_window(
                platform, since=since, now=now
            )
            facts.append(
                CrawlWindowFact(platform=platform, total=total, failures=failures)
            )
        return facts

    async def kakao_queue_lag_seconds(self, *, now: datetime) -> int:
        # fleet 전체 대기 KAKAO_SEND 의 가장 오래된 run_after 기준 lag(전 tenant — scope 없음).
        stmt = (
            select(func.min(Job.run_after))
            .select_from(Job)
            .where(
                Job.type == JOB_TYPE_KAKAO_SEND,
                Job.status == JOB_STATUS_PENDING,
            )
        )
        async with self._session_factory() as session:
            oldest_run_after = (await session.execute(stmt)).scalar_one_or_none()
        if oldest_run_after is None:
            return 0
        return max(0, int((now - oldest_run_after).total_seconds()))

    async def telegram_error_count(self, *, since: datetime, now: datetime) -> int:
        # fleet 전체 최근 윈도 TELEGRAM_FAILURE 카운트(채널/tenant scope 없음 — fleet 수치).
        # 실패 발생 시각 기준. 오래된 실패 row(sent_at=NULL)가 계속 최근 오류로 보이지 않게 한다.
        stmt = (
            select(func.count())
            .select_from(DeliveryLog)
            .where(
                DeliveryLog.error_code == FailureCategory.TELEGRAM_FAILURE.value,
                DeliveryLog.last_failed_at >= since,
            )
        )
        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def auth_required_count(self) -> int:
        # 인증 필요 계정(auth_state == AUTH_REQUIRED) fleet 카운트(5.6 auth_required 집계 의미).
        stmt = (
            select(func.count())
            .select_from(PlatformAccount)
            .where(PlatformAccount.auth_state == BaeminAuthState.AUTH_REQUIRED.value)
        )
        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one())

    async def gmail_reauth_required_count(self) -> int:
        # 쿠팡 Gmail reauth 근사: 미해결(resolved_at IS NULL) auth_session ⨝ COUPANG 계정.
        # 서버에 Gmail 전용 상태가 없어(Platform=BAEMIN/COUPANG뿐) Coupang 미해결 auth_session
        # 으로 근사한다 — 한계는 auth_required.md 에 명시(임의 enum/컬럼 신설 금지).
        stmt = (
            select(func.count())
            .select_from(AuthSession)
            .join(PlatformAccount, AuthSession.account_id == PlatformAccount.id)
            .where(
                PlatformAccount.platform == Platform.COUPANG.value,
                AuthSession.resolved_at.is_(None),
            )
        )
        async with self._session_factory() as session:
            return int((await session.execute(stmt)).scalar_one())
