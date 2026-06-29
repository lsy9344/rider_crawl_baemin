"""읽기 전용 대시보드 read-model 조립 + repository 포트 — Story 5.6 (AC1·AC4).

5.3 ``QueueBackend``·5.4 ``SchedulerRepository`` 선례를 **동형**으로 계승한다: 정책↔DB 경계를
:class:`DashboardRepository`(abc) 포트로 분리해 **always-run in-memory fake** 와 **PostgreSQL
구현**(:mod:`rider_server.admin.dashboard_repository_postgres`) 양쪽이 같은 조립 로직을 통과한다.
repository 는 **중립 facts**(원시 타입/datetime/문자열)만 돌려주고(``AsyncSession``/SQL/ORM Row
누출 0), 순수 심각도 합성은 :mod:`rider_server.admin.severity` 가 한다 — DB I/O 만 async,
집계/심각도 합성은 sync(순수).

**읽기 전용 불변식(AC, architecture #Service-Boundaries):** 포트에 write 메서드가 없다 —
``save``/``commit``/``enqueue``/상태 전이 없음. 대시보드는 상태를 바꾸지 않는다(상태 전이는 5.7).

"마지막 성공/실패"는 신규 컬럼이 아니라 기존 테이블에서 **파생 집계**한다(14표 lock·migration
drift 회피): 수집 성공=``snapshots`` (quality_state=OK), 전송 성공=``delivery_logs`` (status=SENT),
실패 사유=``jobs``/``delivery_logs.error_code`` 최신, heartbeat=``agents.last_heartbeat_at``.
"""

from __future__ import annotations

import abc
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from rider_server.domain import FailureCategory

from . import severity

ALL_TENANTS = "all"
_MAX_KAKAO_STATUS_TEXT_LENGTH = 80
_MAX_KAKAO_STATUS_INT = 1_000_000


# ══════════════════════════════════════════════════════════════════════════
# 중립 facts(repository 출력) — ORM Row/SQL 누출 금지
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TargetHealthFacts:
    """대상 한 건의 파생 집계 facts(심각도 미합성 — 순수 정책이 합성). 모두 중립 타입."""

    target_id: str
    tenant_id: str
    name: str
    center_name: str
    platform: str
    interval_minutes: int
    last_success_at: datetime | None  # MAX(snapshots.collected_at WHERE quality_state='OK')
    last_delivery_at: datetime | None  # MAX(delivery_logs.sent_at WHERE status='SENT')
    last_failure_code: str | None  # 최신 non-null FailureCategory(jobs/delivery_logs.error_code)
    account_auth_state: str | None  # platform_accounts.auth_state(BaeminAuthState 값)
    lifecycle_state: str | None  # tenants.status(CustomerLifecycleState 값)
    customer_name: str = ""
    auth_session_pending: bool = False  # auth_sessions 인증대기 행 존재
    last_failure_at: datetime | None = None  # 위 last_failure_code 의 발생 시각(stale 판정용)
    auth_recovery_state: str | None = None  # 쿠팡 자동 인증 복구 세부 상태(AUTH_COUPANG_2FA)
    auth_recovery_reason: str | None = None  # 쿠팡 자동 인증 복구 세부 사유(result_json.reason)
    kakao_delivery_enabled: bool = False  # 이 대상에 ACTIVE Kakao delivery rule 이 연결됨
    kakao_runtime_unavailable: bool = False  # 온라인 Kakao agent 가 세션 없음으로 보고함


@dataclass(frozen=True)
class AgentBrowserProfileRow:
    """heartbeat ``capacity_json.browser_profiles`` 1건의 런타임 상태(배정 정보 아님).

    secret 0: profile path/secret/URL 은 담지 않는다(필드명에 token/secret/_ref 금지).
    진단 필드(auth_state/last_error_code/last_probe_at)는 optional 이며 값이 없으면 ``None``.
    """

    profile_id: str
    target_id: str | None
    state: str | None
    cdp_port: int | None
    source: str = "heartbeat"
    auth_state: str | None = None
    last_error_code: str | None = None
    last_probe_at: str | None = None


@dataclass(frozen=True)
class AgentHealthFacts:
    """Agent 한 건의 facts(online 미판정 — 순수 정책이 판정). agents 는 tenant 소유 아님(fleet)."""

    agent_id: str
    name: str
    version: str
    last_heartbeat_at: datetime | None
    current_job_type: str | None  # 활성(CLAIMED/RUNNING) job 의 type
    capabilities: tuple[str, ...]  # capacity_json 의 capability 목록
    kakao_status: dict[str, Any] | None = None
    browser_profiles: tuple[AgentBrowserProfileRow, ...] = ()


# ══════════════════════════════════════════════════════════════════════════
# read-model 중립 DTO(서비스 출력 — 심각도/online 합성 포함)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class TargetRow:
    """대상 read-model 행(심각도 합성 포함). 템플릿이 한글 라벨/CSS class 로 매핑한다."""

    target_id: str
    tenant_id: str
    name: str
    center_name: str
    platform: str
    interval_minutes: int
    last_success_at: datetime | None
    last_delivery_at: datetime | None
    last_failure_code: str | None
    severity: str
    customer_name: str = ""
    auth_session_pending: bool = False
    auth_recovery_detail: str | None = None


@dataclass(frozen=True)
class AgentRow:
    """Agent read-model 행(online 판정 포함)."""

    agent_id: str
    name: str
    version: str
    last_heartbeat_at: datetime | None
    online: bool
    current_job_type: str | None
    capabilities: tuple[str, ...]
    kakao_state: str | None = None
    kakao_enabled: bool | None = None
    kakao_queue_depth: int | None = None
    kakao_queue_lag_seconds: int | None = None
    kakao_sent: int | None = None
    kakao_failed: int | None = None
    kakao_last_success_at: str | None = None
    kakao_last_error_code: str | None = None
    kakao_interactive_session_available: bool | None = None
    browser_profiles: tuple[AgentBrowserProfileRow, ...] = ()


@dataclass(frozen=True)
class ChannelHealthRow:
    """채널 운영 상태(KakaoTalk queue lag 와 Telegram 전송 오류를 **별도 필드**로 구분, AC1).

    혼합 금지: ``kakao_queue_lag_seconds`` 는 대기 ``KAKAO_SEND`` job 지연(초),
    ``telegram_error_count`` 는 최근 윈도 ``TELEGRAM_FAILURE`` 분류 카운트로 의미가 다르다.
    """

    kakao_queue_lag_seconds: int
    telegram_error_count: int


@dataclass(frozen=True)
class AuthRequiredRow:
    """인증 필요 대상 한 건(AC4 필터). ``reason`` 은 기계가독 코드(secret 아님)."""

    tenant_id: str
    target_id: str | None
    profile_id: str | None
    reason: str
    target_name: str | None = None
    platform: str | None = None
    auth_recovery_detail: str | None = None


@dataclass(frozen=True)
class JobQueueRow:
    """실시간 큐 뷰의 active(처리 전/처리 중) job 한 건. secret 0(분류 코드/이름/시각만).

    운영자가 "왜 '이미 진행 중인 수집 작업이 있습니다'가 뜨는지" 큐를 직접 보고 판단하게 한다.
    ``stuck`` 은 claim 됐는데 lease 가 만료됐거나(처리 중 멈춤), 배정 Agent 가 오프라인이라 아무도
    집어가지 못하는 상태로, 운영자가 회수/재시도를 판단하는 신호다(stale recovery 가 곧 닫는다).
    """

    job_id: str
    job_type: str
    status: str
    target_id: str | None
    target_name: str | None
    center_name: str | None
    tenant_id: str | None
    attempts: int
    agent_name: str | None
    agent_online: bool | None  # 배정 Agent heartbeat 신선도(미배정이면 None)
    run_after: datetime | None  # 이 시각 이후에야 claim 가능(retry 대기 등)
    claimed_at: datetime | None  # claim 된 시각(PENDING 은 None) — 처리 경과 표시용
    stuck: bool
    error_code: str | None = None  # 최근 실패 job 의 실패 코드(active job 은 None)
    recently_failed: bool = False  # 짧은 윈도 안에 terminal FAILED 된 job(사라지기 전 사유 표시)
    auth_recovery_detail: str | None = None  # AUTH_COUPANG_2FA result detail label


# ══════════════════════════════════════════════════════════════════════════
# repository 포트(읽기 전용) — in-memory fake / PostgreSQL 공용
# ══════════════════════════════════════════════════════════════════════════

class DashboardRepository(abc.ABC):
    """대시보드 read-model 의 DB 접근 포트(backend 중립, **읽기 전용**).

    customer-owned 질의(:meth:`target_health`/:meth:`critical_target_health`/
    :meth:`channel_health`/:meth:`auth_required`)는
    ``tenant_id`` 로 scope 된다(architecture #Data-Boundaries). :meth:`agent_health` 는 agents
    가 tenant 소유가 아닌 fleet 전역 자원이라 tenant scope 가 없다(명시적 예외).

    write 메서드는 **존재하지 않는다** — 대시보드가 상태를 바꿀 수 없음을 타입으로 보장한다.
    """

    @abc.abstractmethod
    async def target_health(
        self,
        *,
        tenant_id: str,
        now: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TargetHealthFacts]:
        """tenant 의 대상별 파생 집계 facts(AC1·AC2·AC3 입력)."""

    async def critical_target_health(
        self,
        *,
        tenant_id: str,
        now: datetime,
        limit: int,
    ) -> list[TargetHealthFacts]:
        """첫 화면 우선 노출용 critical 후보 facts.

        기본 구현은 전체 target facts 위에서 계산한다. PostgreSQL 구현은 별도 bounded query 로
        가장 오래된 성공 수집 후보만 가져와 page 밖 critical target 을 놓치지 않게 한다.
        """

        facts = await self.target_health(tenant_id=tenant_id, now=now)
        rows = [
            row
            for row in facts
            if severity.severity_rank(DashboardService.target_row(row, now).severity)
            >= severity.severity_rank(severity.SEVERITY_CRITICAL)
        ]
        rows.sort(key=lambda row: row.last_success_at or datetime.max)
        return rows[: max(0, limit)]

    @abc.abstractmethod
    async def agent_health(self, *, now: datetime) -> list[AgentHealthFacts]:
        """fleet 전역 Agent facts(heartbeat/버전/현재 job/capability, AC1)."""

    @abc.abstractmethod
    async def channel_health(
        self, *, tenant_id: str, now: datetime
    ) -> ChannelHealthRow:
        """tenant 의 Kakao queue lag · Telegram 전송 오류(구분, AC1)."""

    @abc.abstractmethod
    async def auth_required(self, *, tenant_id: str) -> list[AuthRequiredRow]:
        """tenant 의 인증 필요 고객/대상/프로필 목록(AC4)."""

    @abc.abstractmethod
    async def active_jobs(
        self, *, tenant_id: str, now: datetime, limit: int = 100
    ) -> list[JobQueueRow]:
        """tenant 의 active(PENDING/CLAIMED/RUNNING/RETRY) job + 최근 실패 job 목록(실시간 큐 뷰).

        최근 실패(terminal FAILED, 짧은 윈도 내)도 포함해 ``error_code`` 와 함께 보여준다 —
        그러지 않으면 재검증 crawl 이 실패하면 큐에서 그냥 사라져 운영자가 사유를 못 본다.
        ``tenant_id == ALL_TENANTS`` 면 전 tenant 를 합친다. target 미연결 job(fleet job)은
        ``tenant_id`` scope 밖이라 ALL_TENANTS 일 때만 보인다. 읽기 전용(상태 변경 0).
        """


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= _MAX_KAKAO_STATUS_INT else None
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        integer = int(value)
        return integer if 0 <= integer <= _MAX_KAKAO_STATUS_INT else None
    return None


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _optional_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > _MAX_KAKAO_STATUS_TEXT_LENGTH:
        return None
    if any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in text):
        return None
    return text


# ══════════════════════════════════════════════════════════════════════════
# read-model 조립 서비스(순수 심각도 합성 + async repository I/O)
# ══════════════════════════════════════════════════════════════════════════

class DashboardService:
    """repository facts 를 심각도 합성된 read-model 로 조립한다(상태 변경 0).

    행 매핑(:meth:`target_row`/:meth:`agent_row`)은 sync 순수 함수다 — 시각 ``now`` 주입으로
    결정적·always-run 테스트가 가능하다(PG 없이 의미 잠금).
    """

    @staticmethod
    def target_row(facts: TargetHealthFacts, now: datetime) -> TargetRow:
        failure_code = facts.last_failure_code
        failure_at = facts.last_failure_at
        failure_is_stale = (
            facts.last_success_at is not None
            and failure_at is not None
            and failure_at <= facts.last_success_at
        )
        if facts.kakao_delivery_enabled and facts.kakao_runtime_unavailable:
            failure_code = FailureCategory.KAKAO_FAILURE.value
            failure_at = now
        elif failure_is_stale:
            failure_code = None
            failure_at = None

        freshness = severity.classify_freshness(
            facts.last_success_at, facts.interval_minutes, now
        )
        signals = severity.failclosed_signals_from(
            account_auth_state=facts.account_auth_state,
            lifecycle_state=facts.lifecycle_state,
            latest_failure_code=failure_code,
            auth_session_pending=facts.auth_session_pending,
            last_success_at=facts.last_success_at,
            latest_failure_at=failure_at,
        )
        overall = severity.overall_severity(
            freshness, severity.classify_failclosed(signals)
        )
        if signals.kakao_misdelivery_risk and overall != severity.SEVERITY_STOPPED:
            overall = severity.SEVERITY_CRITICAL
        return TargetRow(
            target_id=facts.target_id,
            tenant_id=facts.tenant_id,
            name=facts.name,
            center_name=facts.center_name,
            platform=facts.platform,
            interval_minutes=facts.interval_minutes,
            last_success_at=facts.last_success_at,
            last_delivery_at=facts.last_delivery_at,
            last_failure_code=failure_code,
            severity=overall,
            customer_name=facts.customer_name,
            auth_session_pending=facts.auth_session_pending,
            auth_recovery_detail=severity.coupang_recovery_detail_label(
                auth_recovery_state=facts.auth_recovery_state,
                reason=facts.auth_recovery_reason,
            ),
        )

    @staticmethod
    def agent_row(facts: AgentHealthFacts, now: datetime) -> AgentRow:
        kakao = facts.kakao_status if isinstance(facts.kakao_status, dict) else {}
        state = _optional_str(kakao.get("current_state") or kakao.get("state"))
        enabled = (
            kakao.get("enabled") if "enabled" in kakao else kakao.get("worker_enabled")
        )
        return AgentRow(
            agent_id=facts.agent_id,
            name=facts.name,
            version=facts.version,
            last_heartbeat_at=facts.last_heartbeat_at,
            online=severity.is_agent_online(facts.last_heartbeat_at, now),
            current_job_type=facts.current_job_type,
            capabilities=facts.capabilities,
            kakao_state=state,
            kakao_enabled=_optional_bool(enabled),
            kakao_queue_depth=_optional_int(kakao.get("queue_depth")),
            kakao_queue_lag_seconds=_optional_int(kakao.get("queue_lag_seconds")),
            kakao_sent=_optional_int(kakao.get("sent")),
            kakao_failed=_optional_int(kakao.get("failed")),
            kakao_last_success_at=_optional_str(kakao.get("last_success_at")),
            kakao_last_error_code=_optional_str(kakao.get("last_error_code")),
            kakao_interactive_session_available=_optional_bool(
                kakao.get("interactive_session_available")
            ),
            # browser_profiles 는 repository 에서 이미 정제된 AgentBrowserProfileRow 다.
            # service 는 정책 합성만 하고 런타임 상태는 그대로 통과시킨다(raw capacity 미접근).
            browser_profiles=facts.browser_profiles,
        )

    async def target_rows(
        self, repo: DashboardRepository, *, tenant_id: str, now: datetime
    ) -> list[TargetRow]:
        facts = await repo.target_health(tenant_id=tenant_id, now=now)
        rows = [self.target_row(f, now) for f in facts]
        # 위험도 높은 순으로 정렬해 운영자가 막힌 곳을 먼저 본다(fail-closed 우선 표시, AC3).
        rows.sort(key=lambda r: severity.severity_rank(r.severity), reverse=True)
        return rows

    async def agent_rows(
        self, repo: DashboardRepository, *, now: datetime
    ) -> list[AgentRow]:
        facts = await repo.agent_health(now=now)
        return [self.agent_row(f, now) for f in facts]

    async def channel_health(
        self, repo: DashboardRepository, *, tenant_id: str, now: datetime
    ) -> ChannelHealthRow:
        return await repo.channel_health(tenant_id=tenant_id, now=now)

    async def auth_required_rows(
        self, repo: DashboardRepository, *, tenant_id: str, now: datetime | None = None
    ) -> list[AuthRequiredRow]:
        rows = self._collapse_auth_required_rows(await repo.auth_required(tenant_id=tenant_id))
        missing_names = [row for row in rows if row.target_id and not row.target_name]
        if not missing_names or now is None:
            return rows
        targets = {
            facts.target_id: facts.name
            for facts in await repo.target_health(tenant_id=tenant_id, now=now)
        }
        return [
            AuthRequiredRow(
                tenant_id=row.tenant_id,
                target_id=row.target_id,
                profile_id=row.profile_id,
                reason=row.reason,
                target_name=row.target_name or targets.get(row.target_id or ""),
                platform=row.platform,
                auth_recovery_detail=row.auth_recovery_detail,
            )
            for row in rows
        ]

    @staticmethod
    def _collapse_auth_required_rows(rows: list[AuthRequiredRow]) -> list[AuthRequiredRow]:
        """Keep one visible auth action per target to avoid duplicate dashboard controls."""

        def priority(row: AuthRequiredRow) -> int:
            if row.reason == "AUTH_SESSION_PENDING":
                return 0
            if row.reason == "ACCOUNT_AUTH_REQUIRED":
                return 1
            return 2

        def combine_rows(
            existing: AuthRequiredRow, candidate: AuthRequiredRow
        ) -> AuthRequiredRow:
            winner, fallback = (
                (candidate, existing)
                if priority(candidate) < priority(existing)
                else (existing, candidate)
            )
            return AuthRequiredRow(
                tenant_id=winner.tenant_id,
                target_id=winner.target_id,
                profile_id=winner.profile_id or fallback.profile_id,
                reason=winner.reason,
                target_name=winner.target_name or fallback.target_name,
                platform=winner.platform or fallback.platform,
                auth_recovery_detail=winner.auth_recovery_detail or fallback.auth_recovery_detail,
            )

        collapsed: dict[tuple[str, str], AuthRequiredRow] = {}
        order: list[tuple[str, str]] = []
        targetless: list[AuthRequiredRow] = []
        for row in rows:
            if not row.target_id:
                targetless.append(row)
                continue
            key = (row.tenant_id, row.target_id)
            if key not in collapsed:
                collapsed[key] = row
                order.append(key)
                continue
            collapsed[key] = combine_rows(collapsed[key], row)
        return [collapsed[key] for key in order] + targetless

    async def job_queue_rows(
        self, repo: DashboardRepository, *, tenant_id: str, now: datetime, limit: int = 100
    ) -> list[JobQueueRow]:
        """실시간 큐 뷰 행. stuck/online 판정은 lease·heartbeat 가 있는 repository 가 한다.

        run_after(대기) → created_at(오래된 순)으로 정렬해 가장 막혀 있는 job 을 위로 올린다.
        """

        rows = await repo.active_jobs(tenant_id=tenant_id, now=now, limit=limit)
        # 주의가 필요한 것(stuck=멈춤, recently_failed=방금 실패)을 가장 위로, 그다음 run_after
        # (대기) → claimed_at(오래된 순). None 은 항상 뒤로 보낸다(naive datetime.max 와 tz-aware
        # 비교 회피 — 키를 (has_value 불리언, 값)으로 구성해 None 끼리는 값 비교 자체를 안 한다).
        return sorted(
            rows,
            key=lambda r: (
                not (r.stuck or r.recently_failed),
                r.run_after is None,
                r.run_after or now,
                r.claimed_at is None,
                r.claimed_at or now,
            ),
        )


# ══════════════════════════════════════════════════════════════════════════
# in-memory 구현(무-DB 기본값 + 테스트 fake — InMemoryQueueBackend 선례)
# ══════════════════════════════════════════════════════════════════════════

class InMemoryDashboardRepository(DashboardRepository):
    """프로세스-내 읽기 전용 대시보드 repository(무-DB 기본값 + always-run 테스트 fake).

    ``seed_*`` 헬퍼는 **테스트/데모용 주입**일 뿐 앱 런타임 경로(라우트/서비스)는 호출하지
    않는다(읽기 전용 — read 메서드만 사용). tenant scope 는 dict 키로 격리한다.
    """

    def __init__(self) -> None:
        self._targets: dict[str, list[TargetHealthFacts]] = {}
        self._agents: list[AgentHealthFacts] = []
        self._channels: dict[str, ChannelHealthRow] = {}
        self._auth_required: dict[str, list[AuthRequiredRow]] = {}
        self._active_jobs: list[JobQueueRow] = []

    # ── seed(테스트 전용 — 런타임 read 경로 아님) ──────────────────────────────
    def seed_target(self, facts: TargetHealthFacts) -> None:
        self._targets.setdefault(facts.tenant_id, []).append(facts)

    def seed_agent(self, facts: AgentHealthFacts) -> None:
        self._agents.append(facts)

    def seed_channel_health(self, tenant_id: str, row: ChannelHealthRow) -> None:
        self._channels[tenant_id] = row

    def seed_auth_required(self, row: AuthRequiredRow) -> None:
        self._auth_required.setdefault(row.tenant_id, []).append(row)

    def seed_active_job(self, row: JobQueueRow) -> None:
        self._active_jobs.append(row)

    # ── read 포트(런타임 경로) ────────────────────────────────────────────────
    async def target_health(
        self,
        *,
        tenant_id: str,
        now: datetime,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[TargetHealthFacts]:
        if tenant_id == ALL_TENANTS:
            rows = [row for values in self._targets.values() for row in values]
        else:
            rows = list(self._targets.get(tenant_id, []))
        rows = rows[max(0, offset):]
        if limit is not None:
            rows = rows[: max(0, limit)]
        return rows

    async def critical_target_health(
        self,
        *,
        tenant_id: str,
        now: datetime,
        limit: int,
    ) -> list[TargetHealthFacts]:
        if tenant_id == ALL_TENANTS:
            facts = [row for values in self._targets.values() for row in values]
        else:
            facts = list(self._targets.get(tenant_id, []))
        rows = [
            row
            for row in facts
            if severity.severity_rank(DashboardService.target_row(row, now).severity)
            >= severity.severity_rank(severity.SEVERITY_CRITICAL)
        ]
        rows.sort(key=lambda row: row.last_success_at or datetime.max)
        return rows[: max(0, limit)]

    async def agent_health(self, *, now: datetime) -> list[AgentHealthFacts]:
        return list(self._agents)

    async def channel_health(
        self, *, tenant_id: str, now: datetime
    ) -> ChannelHealthRow:
        if tenant_id == ALL_TENANTS:
            return ChannelHealthRow(
                kakao_queue_lag_seconds=max(
                    (row.kakao_queue_lag_seconds for row in self._channels.values()),
                    default=0,
                ),
                telegram_error_count=sum(
                    row.telegram_error_count for row in self._channels.values()
                ),
            )
        return self._channels.get(
            tenant_id, ChannelHealthRow(kakao_queue_lag_seconds=0, telegram_error_count=0)
        )

    async def auth_required(self, *, tenant_id: str) -> list[AuthRequiredRow]:
        if tenant_id == ALL_TENANTS:
            return [row for rows in self._auth_required.values() for row in rows]
        return list(self._auth_required.get(tenant_id, []))

    async def active_jobs(
        self, *, tenant_id: str, now: datetime, limit: int = 100
    ) -> list[JobQueueRow]:
        # fake 는 seed 된 행을 그대로 돌려준다(active + 최근 실패 모두 seed 로 표현). tenant scope 만 건다.
        if tenant_id == ALL_TENANTS:
            rows = list(self._active_jobs)
        else:
            rows = [row for row in self._active_jobs if row.tenant_id == tenant_id]
        return rows[: max(0, limit)]
