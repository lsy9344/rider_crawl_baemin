"""지표 repository 포트 + 조립 서비스 + in-memory fake — Story 5.9 (AC1).

5.6 ``DashboardRepository``/``InMemoryDashboardRepository`` 선례를 **동형**으로 계승한다:
정책↔DB 경계를 :class:`MetricsRepository`(abc) 포트로 분리해 **always-run in-memory fake** 와
**PostgreSQL 구현**(:mod:`rider_server.metrics.repository_postgres`) 양쪽이 같은 조립 로직을
통과한다. 포트는 **중립 facts**(원시 타입/datetime)만 돌려주고(``AsyncSession``/SQL/ORM Row
누출 0), 식별 텍스트(이름/target_id)는 facts 단계에서도 담지 않는다(비식별 1차 방어선).

**읽기 전용 불변식:** 포트에 write 메서드가 없다 — ``save``/``commit``/``enqueue``/상태 전이
없음. 지표 레이어는 상태를 바꾸지 않는다.

**async/sync 경계:** DB I/O 만 async(:meth:`MetricsService.snapshot`), 스냅샷 조립
(:meth:`MetricsService.assemble`)은 sync 순수다 — 시각 ``now`` 주입으로 결정적·always-run
테스트가 가능하다(PG 없이 의미 잠금). async 본문에서 blocking sync 직접 호출 0.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime

from rider_server.admin import severity

from .policy import (
    DEFAULT_BREAKER_WINDOW,
    TELEGRAM_ERROR_WINDOW,
    MetricsSnapshot,
)


# ══════════════════════════════════════════════════════════════════════════
# 중립 facts(repository 출력) — 식별 텍스트/ORM Row 누출 금지
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class HeartbeatFact:
    """Agent 한 건의 heartbeat fact(online 미판정 — 순수 조립이 판정). 이름/id 미포함."""

    last_heartbeat_at: datetime | None


@dataclass(frozen=True)
class FreshnessFact:
    """대상 한 건의 수집 신선도 fact(심각도 미합성). 이름/target_id 미포함(비식별)."""

    interval_minutes: int
    last_success_at: datetime | None


@dataclass(frozen=True)
class CrawlWindowFact:
    """플랫폼별 최근 윈도 crawl ``(total, failures)`` 집계(breaker 입력). ``platform`` 은
    ``Platform`` 값(BAEMIN/COUPANG) — 플랫폼명은 식별정보가 아니다(fleet 집계)."""

    platform: str
    total: int
    failures: int


# ══════════════════════════════════════════════════════════════════════════
# repository 포트(읽기 전용) — in-memory fake / PostgreSQL 공용
# ══════════════════════════════════════════════════════════════════════════

class MetricsRepository(abc.ABC):
    """운영 지표 facts 의 DB 접근 포트(backend 중립, **읽기 전용**, fleet scope).

    모든 메서드는 **비식별 fleet 집계**(전 tenant 합/최댓값/카운트)를 돌려준다 — 대시보드
    (5.6)는 tenant scope 지만 지표 엔드포인트는 unauthenticated scrape 라 tenant 격리·redaction
    을 위해 식별 facts 를 노출하지 않는다. write 메서드는 **존재하지 않는다**(상태 변경 불가).
    """

    @abc.abstractmethod
    async def agent_heartbeats(self, *, now: datetime) -> list[HeartbeatFact]:
        """fleet 전역 Agent heartbeat facts(offline 판정 입력, 지표 1)."""

    @abc.abstractmethod
    async def target_freshness(self, *, now: datetime) -> list[FreshnessFact]:
        """fleet 전역 대상 수집 신선도 facts(warning/critical 집계 입력, 지표 2)."""

    @abc.abstractmethod
    async def crawl_windows(
        self, *, since: datetime, now: datetime
    ) -> list[CrawlWindowFact]:
        """플랫폼별 최근 윈도 crawl ``(total, failures)``(breaker 입력, 지표 5)."""

    @abc.abstractmethod
    async def kakao_queue_lag_seconds(self, *, now: datetime) -> int:
        """대기 KAKAO_SEND fleet 최대 지연(초, 지표 4)."""

    @abc.abstractmethod
    async def telegram_error_count(self, *, since: datetime, now: datetime) -> int:
        """최근 10분 윈도 TELEGRAM_FAILURE fleet 카운트(지표 6)."""

    @abc.abstractmethod
    async def auth_required_count(self) -> int:
        """인증 필요(AUTH_REQUIRED) 계정 fleet 카운트(지표 3)."""

    @abc.abstractmethod
    async def gmail_reauth_required_count(self) -> int:
        """쿠팡 Gmail reauth 근사(미해결 auth_session ⨝ COUPANG) fleet 카운트(지표 7)."""


# ══════════════════════════════════════════════════════════════════════════
# 조립 서비스(순수 집계 + async repository I/O)
# ══════════════════════════════════════════════════════════════════════════

class MetricsService:
    """repository facts 를 비식별 :class:`MetricsSnapshot` 으로 조립한다(상태 변경 0).

    :meth:`assemble` 은 sync 순수 함수다 — 시각 ``now`` 주입으로 결정적·always-run 테스트가
    가능하다(PG 없이 offline/freshness 의미 잠금). :meth:`snapshot` 만 async(DB I/O).
    """

    @staticmethod
    def assemble(
        *,
        heartbeats: list[HeartbeatFact],
        freshness: list[FreshnessFact],
        crawl_windows: list[CrawlWindowFact],
        kakao_queue_lag_seconds: int,
        telegram_error_count: int,
        auth_required_count: int,
        gmail_reauth_required_count: int,
        now: datetime,
    ) -> MetricsSnapshot:
        """중립 facts → :class:`MetricsSnapshot`(sync 순수, severity 정본 재사용)."""

        # 지표 1: agent offline 판정·최고령 heartbeat age(severity 정본 재사용).
        agents_total = len(heartbeats)
        agents_offline = sum(
            0 if severity.is_agent_online(h.last_heartbeat_at, now) else 1
            for h in heartbeats
        )
        ages = [
            int((now - h.last_heartbeat_at).total_seconds())
            for h in heartbeats
            if h.last_heartbeat_at is not None
        ]
        oldest_heartbeat_age_seconds = max(ages) if ages else None

        # 지표 2: freshness warning/critical **대상 수**만(개별 timestamp/이름 노출 금지).
        targets_total = len(freshness)
        targets_warning = 0
        targets_critical = 0
        for f in freshness:
            sev = severity.classify_freshness(f.last_success_at, f.interval_minutes, now)
            if sev == severity.SEVERITY_CRITICAL:
                targets_critical += 1
            elif sev == severity.SEVERITY_WARNING:
                targets_warning += 1

        # 지표 5: 플랫폼별 실패율 + 표본수(rate 는 표시, 알림 판정은 표본 가드와 함께).
        crawl_error_rate_by_platform: dict[str, float] = {}
        crawl_samples_by_platform: dict[str, int] = {}
        for w in crawl_windows:
            crawl_samples_by_platform[w.platform] = w.total
            crawl_error_rate_by_platform[w.platform] = (
                (w.failures / w.total) if w.total > 0 else 0.0
            )

        return MetricsSnapshot(
            agents_total=agents_total,
            agents_offline=agents_offline,
            oldest_heartbeat_age_seconds=oldest_heartbeat_age_seconds,
            targets_total=targets_total,
            targets_warning=targets_warning,
            targets_critical=targets_critical,
            auth_required_count=auth_required_count,
            kakao_queue_lag_seconds=kakao_queue_lag_seconds,
            crawl_error_rate_by_platform=crawl_error_rate_by_platform,
            crawl_samples_by_platform=crawl_samples_by_platform,
            telegram_error_count=telegram_error_count,
            gmail_reauth_required_count=gmail_reauth_required_count,
        )

    async def snapshot(
        self, repo: MetricsRepository, *, now: datetime
    ) -> MetricsSnapshot:
        """repository 에서 facts 를 읽어(async) 비식별 스냅샷으로 조립한다.

        crawl 은 최근 15분(``DEFAULT_BREAKER_WINDOW``), telegram 은 최근 10분
        (``TELEGRAM_ERROR_WINDOW``) 윈도를 ``now`` 기준으로 잡는다(임계 정본 재사용).
        """

        crawl_since = now - DEFAULT_BREAKER_WINDOW
        telegram_since = now - TELEGRAM_ERROR_WINDOW
        return self.assemble(
            heartbeats=await repo.agent_heartbeats(now=now),
            freshness=await repo.target_freshness(now=now),
            crawl_windows=await repo.crawl_windows(since=crawl_since, now=now),
            kakao_queue_lag_seconds=await repo.kakao_queue_lag_seconds(now=now),
            telegram_error_count=await repo.telegram_error_count(
                since=telegram_since, now=now
            ),
            auth_required_count=await repo.auth_required_count(),
            gmail_reauth_required_count=await repo.gmail_reauth_required_count(),
            now=now,
        )


# ══════════════════════════════════════════════════════════════════════════
# in-memory 구현(무-DB 기본값 + 테스트 fake — InMemoryDashboardRepository 선례)
# ══════════════════════════════════════════════════════════════════════════

class InMemoryMetricsRepository(MetricsRepository):
    """프로세스-내 읽기 전용 지표 repository(무-DB 기본값 + always-run 테스트 fake).

    ``seed_*`` 헬퍼는 **테스트/데모용 주입**일 뿐 앱 런타임 경로(라우트/서비스)는 호출하지
    않는다(읽기 전용 — read 메서드만 사용).
    """

    def __init__(self) -> None:
        self._heartbeats: list[HeartbeatFact] = []
        self._freshness: list[FreshnessFact] = []
        self._crawl_windows: list[CrawlWindowFact] = []
        self._kakao_queue_lag_seconds = 0
        self._telegram_error_count = 0
        self._auth_required_count = 0
        self._gmail_reauth_required_count = 0

    # ── seed(테스트 전용 — 런타임 read 경로 아님) ──────────────────────────────
    def seed_agent_heartbeat(self, last_heartbeat_at: datetime | None) -> None:
        self._heartbeats.append(HeartbeatFact(last_heartbeat_at=last_heartbeat_at))

    def seed_target_freshness(
        self, interval_minutes: int, last_success_at: datetime | None
    ) -> None:
        self._freshness.append(
            FreshnessFact(
                interval_minutes=interval_minutes, last_success_at=last_success_at
            )
        )

    def seed_crawl_window(self, platform: str, total: int, failures: int) -> None:
        self._crawl_windows.append(
            CrawlWindowFact(platform=platform, total=total, failures=failures)
        )

    def seed_kakao_queue_lag_seconds(self, seconds: int) -> None:
        self._kakao_queue_lag_seconds = seconds

    def seed_telegram_error_count(self, count: int) -> None:
        self._telegram_error_count = count

    def seed_auth_required_count(self, count: int) -> None:
        self._auth_required_count = count

    def seed_gmail_reauth_required_count(self, count: int) -> None:
        self._gmail_reauth_required_count = count

    # ── read 포트(런타임 경로) ────────────────────────────────────────────────
    async def agent_heartbeats(self, *, now: datetime) -> list[HeartbeatFact]:
        return list(self._heartbeats)

    async def target_freshness(self, *, now: datetime) -> list[FreshnessFact]:
        return list(self._freshness)

    async def crawl_windows(
        self, *, since: datetime, now: datetime
    ) -> list[CrawlWindowFact]:
        return list(self._crawl_windows)

    async def kakao_queue_lag_seconds(self, *, now: datetime) -> int:
        return self._kakao_queue_lag_seconds

    async def telegram_error_count(self, *, since: datetime, now: datetime) -> int:
        return self._telegram_error_count

    async def auth_required_count(self) -> int:
        return self._auth_required_count

    async def gmail_reauth_required_count(self) -> int:
        return self._gmail_reauth_required_count
