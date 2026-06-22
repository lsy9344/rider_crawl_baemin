"""순수 scheduler 정책 — Story 5.4 (AC1·AC2·AC3·AC4).

2.6 ``subscription_gate`` / 3.1 ``crawl_service`` / 3.6 ``delivery_failure_policy`` 의
**순수 정적 서비스 규약 계승**: FastAPI/SQLAlchemy/async 의존 0, 내부에서
``datetime.now()``/``uuid4()``/``random`` 을 **호출하지 않는다**(시각·seed·임계치는 호출부
주입 — 테스트 결정성). DB/queue I/O 는 :mod:`rider_server.scheduler.service` 의 async tick 소유.

**재사용이 핵심 — 재구현 금지.** 구독 게이트는 :class:`SubscriptionGate` (2.6 정본)를 import 해
``evaluate_status`` 위에 lifecycle 합성만 얹고, error_code별 backoff 는
:class:`DeliveryFailurePolicy` (3.6 정본)의 ``decide``/``backoff_delay_seconds`` 를 호출한다.
job type 상수는 :mod:`rider_server.queue.states` 미러 6종에서 쓴다(``rider_agent`` import 금지).
5.4 는 이들을 **조립(compose)** 하는 스토리다 — 평행한 새 정책 함수를 만들지 않는다.

새 어휘(circuit breaker state 등)는 ``test_domain_states`` 의 count-lock(11/4/7)을 깨지 않게
**plain-string 상수**로 둔다(5.3 ``queue/states`` 선례 — 기존 enum 에 멤버 추가 금지).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from rider_server.domain import (
    CustomerLifecycleState,
    FailureCategory,
    Platform,
    SubscriptionStatus,
)
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG
from rider_server.services.delivery_failure_policy import DeliveryFailurePolicy
from rider_server.services.subscription_gate import SubscriptionGate

# ── lifecycle 활성 집합(AC2) ──────────────────────────────────────────────────
# 구독 게이트 통과 **AND** 이 집합 멤버일 때만 enqueue. epic 초안의 "ACTIVE/PAYMENT_ACTIVE"
# 는 ``CustomerLifecycleState`` 기준이다(``SubscriptionStatus`` 엔 ``ACTIVE`` 없음 — 둘은 다른
# enum). 게이트(구독 상태) AND lifecycle 활성으로 합성한다.
ACTIVE_LIFECYCLE_STATES: frozenset[CustomerLifecycleState] = frozenset(
    {CustomerLifecycleState.ACTIVE, CustomerLifecycleState.PAYMENT_ACTIVE}
)

# ── circuit breaker state(plain-string 상수 — count-lock 회피) ────────────────
BREAKER_OPEN = "OPEN"
BREAKER_CLOSED = "CLOSED"

# circuit breaker 기본 임계치(AC3). 최근 15분 실패율 > 30% & 표본 ≥ min_samples → open.
# min_samples 는 1/1=100% 같은 소표본 오탐을 막는 가드다.
DEFAULT_BREAKER_THRESHOLD = 0.30
DEFAULT_BREAKER_MIN_SAMPLES = 5


# ══════════════════════════════════════════════════════════════════════════
# AC1 — 결정적 jitter · due 판정 · next_run_at 전진
# ══════════════════════════════════════════════════════════════════════════

def compute_jitter(target_id: str, interval_seconds: int) -> int:
    """``target_id`` 안정 해시 파생 **결정적 jitter**(``0..interval_seconds`` 범위, ``random``
    미사용).

    같은 ``target_id`` 는 항상 같은 jitter 를 받고, 서로 다른 ``target_id`` 는 sha256 분포로
    흩어진다 — 같은 ``interval`` 을 가진 N개 대상이 **모두 같은 초에 몰리지 않음**이 결정적으로
    검증 가능하다(AC1, [architecture-contract.md:61 "deterministic jitter in the 0..interval
    range"]). ``interval_seconds <= 0`` 이면 분산할 범위가 없어 0.
    """

    if interval_seconds <= 0:
        return 0
    digest = hashlib.sha256(target_id.encode("utf-8")).digest()
    # 앞 8바이트를 정수로 — 결정적·플랫폼 독립(파이썬 hash() 의 PYTHONHASHSEED 비결정성 회피).
    raw = int.from_bytes(digest[:8], "big")
    return raw % interval_seconds


def next_run_at(now: datetime, interval_seconds: int, jitter_seconds: int) -> datetime:
    """다음 실행 시각 = ``now + interval + jitter``(결정적). 같은 due 가 재진입하지 않게 전진."""

    return now + timedelta(seconds=interval_seconds + jitter_seconds)


def is_due(next_run_at_value: datetime | None, now: datetime) -> bool:
    """due 판정 — ``next_run_at`` 이 ``None``(미초기화=즉시 due)이거나 ``<= now`` 면 due."""

    return next_run_at_value is None or next_run_at_value <= now


# ══════════════════════════════════════════════════════════════════════════
# AC2 — 구독 게이트 + 고객 lifecycle 합성
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class SchedulerDecision:
    """스케줄 게이트 합성 결정(불변). ``reason`` 은 ``UPPER_SNAKE`` 기계가독 코드(평문 secret
    없음). ``warn_admin`` 은 게이트 경고(예: ``PAYMENT_FAILED_GRACE``)를 결정 결과에 보존한다.
    """

    allow_new_crawl_job: bool
    warn_admin: bool
    reason: str


def decide_schedule(
    subscription_status: SubscriptionStatus | None,
    lifecycle_status: CustomerLifecycleState | None,
) -> SchedulerDecision:
    """**구독 게이트(정본 재사용) AND lifecycle 활성** 합성으로 신규 CrawlJob 허용 여부를 판정.

    - 구독 미매핑(``None``) → fail-closed 차단(``NO_SUBSCRIPTION``).
    - :meth:`SubscriptionGate.evaluate_status` 가 ``allow_new_crawl_job=False`` 인 고객
      (``SUSPENDED``/``CANCELLED``/미매핑) → 차단(reason=게이트 사유, **재구현 금지** — import).
    - 게이트는 통과했으나 lifecycle 이 활성 집합(``ACTIVE``/``PAYMENT_ACTIVE``)이 아니거나
      ``None`` → 차단(``LIFECYCLE_INACTIVE``). 게이트 docstring 이 lifecycle 합성을 5.4 책임으로
      명시한다.
    - 둘 다 통과 → 허용(게이트 ``warn_admin`` 경고는 그대로 보존).
    """

    if subscription_status is None:
        return SchedulerDecision(
            allow_new_crawl_job=False, warn_admin=True, reason="NO_SUBSCRIPTION"
        )
    gate = SubscriptionGate.evaluate_status(subscription_status)
    if not gate.allow_new_crawl_job:
        return SchedulerDecision(
            allow_new_crawl_job=False, warn_admin=gate.warn_admin, reason=gate.reason
        )
    if lifecycle_status is None or lifecycle_status not in ACTIVE_LIFECYCLE_STATES:
        return SchedulerDecision(
            allow_new_crawl_job=False,
            warn_admin=gate.warn_admin,
            reason="LIFECYCLE_INACTIVE",
        )
    return SchedulerDecision(
        allow_new_crawl_job=True, warn_admin=gate.warn_admin, reason=gate.reason
    )


# ══════════════════════════════════════════════════════════════════════════
# AC1 — job type 매핑(정본 6종 중 CRAWL_BAEMIN/CRAWL_COUPANG)
# ══════════════════════════════════════════════════════════════════════════

def crawl_job_type_for(platform: Platform | str) -> str:
    """``Platform`` → CrawlJob job type(``CRAWL_BAEMIN``/``CRAWL_COUPANG``).

    문자열(``platform_accounts.platform`` 컬럼값)도 받아 ``Platform`` 으로 강제 변환한다.
    미지 플랫폼은 **fail-closed**(``ValueError``) — 조용히 임의 type 을 쓰면 Agent capability
    매칭이 깨져 claim 0건이 된다(구표기 ``CRAWL``/``RENDER`` 금지). [queue/states.py:24-39]
    """

    if not isinstance(platform, Platform):
        try:
            platform = Platform(platform)
        except ValueError as exc:
            raise ValueError(f"unknown platform for crawl job type: {platform!r}") from exc
    if platform is Platform.BAEMIN:
        return JOB_TYPE_CRAWL_BAEMIN
    if platform is Platform.COUPANG:
        return JOB_TYPE_CRAWL_COUPANG
    raise ValueError(f"unknown platform for crawl job type: {platform!r}")


# ══════════════════════════════════════════════════════════════════════════
# AC3 — 플랫폼 circuit breaker + error_code별 backoff(재사용)
# ══════════════════════════════════════════════════════════════════════════

def evaluate_breaker(
    total: int,
    failures: int,
    *,
    threshold: float = DEFAULT_BREAKER_THRESHOLD,
    min_samples: int = DEFAULT_BREAKER_MIN_SAMPLES,
) -> bool:
    """플랫폼 circuit breaker **open 여부**(True=open).

    최근 15분 윈도 실패율(``failures/total``)이 ``threshold`` **초과**이고 표본
    ``total >= min_samples`` 면 open. 표본 부족(``total < min_samples``)이면 ``1/1=100%`` 오탐을
    막기 위해 **closed**(False) — min_samples 가드. 윈도 집계(total/failures per platform)는
    호출부 주입(DB 집계는 service). [architecture.md:330-331, ops-contract:29 "crawl_error_rate_
    by_platform Over 30% in recent 15 minutes"]
    """

    if total <= 0 or total < min_samples:
        return False
    return (failures / total) > threshold


def breaker_state(
    total: int,
    failures: int,
    *,
    threshold: float = DEFAULT_BREAKER_THRESHOLD,
    min_samples: int = DEFAULT_BREAKER_MIN_SAMPLES,
) -> str:
    """:func:`evaluate_breaker` 를 plain-string state(:data:`BREAKER_OPEN`/:data:`BREAKER_CLOSED`)
    로 매핑 — 결정 결과/로그 가시성용(enum 아님 → count-lock 무관)."""

    return (
        BREAKER_OPEN
        if evaluate_breaker(total, failures, threshold=threshold, min_samples=min_samples)
        else BREAKER_CLOSED
    )


@dataclass(frozen=True)
class RetrySchedule:
    """실패 CrawlJob 재시도 일정(불변). :meth:`DeliveryFailurePolicy.decide` 결정 계승.

    ``run_after`` 는 **재시도(``RETRYING``)일 때만** ``now + backoff`` 값을 갖고, 사람-개입
    보류(``HELD``: ``AUTH_REQUIRED``/``TARGET_VALIDATION_FAILURE``)·소진/결정적(``FAILED``)은
    ``None`` 이다(무한 재시도 금지). ``status``/``error_code`` 는 plain-string 값.
    """

    should_retry: bool
    status: str
    error_code: str
    attempt: int
    run_after: datetime | None
    delay_seconds: int | None


def retry_run_after(
    now: datetime,
    *,
    error_code: FailureCategory,
    attempt: int,
    max_attempts: int,
    base_seconds: int | None = None,
    factor: int | None = None,
    cap_seconds: int | None = None,
) -> RetrySchedule:
    """실패 job 의 다음 실행 시각(``jobs.run_after``)을 **error_code별 결정적 backoff** 로 계산.

    :meth:`DeliveryFailurePolicy.decide` 를 **재사용**(재구현 금지)해 ``run_after = now +
    backoff(attempt, error_code)`` 를 만든다 — 고정 5초·0초·무한 재시도 금지(ADD-15). 사람-개입
    카테고리(``AUTH_REQUIRED`` 등)는 ``decide`` 가 ``HELD``(``should_retry=False``)로 판정하므로
    ``run_after=None``(무한 재시도 차단). backoff 파라미터를 주지 않으면 3.6 기본값을 그대로 쓴다.
    [delivery_failure_policy.py:128-204]
    """

    kwargs: dict[str, int] = {}
    if base_seconds is not None:
        kwargs["base_seconds"] = base_seconds
    if factor is not None:
        kwargs["factor"] = factor
    if cap_seconds is not None:
        kwargs["cap_seconds"] = cap_seconds
    decision = DeliveryFailurePolicy.decide(
        category=error_code, attempt=attempt, max_attempts=max_attempts, **kwargs
    )
    run_after = (
        now + timedelta(seconds=decision.delay_seconds)
        if decision.should_retry and decision.delay_seconds is not None
        else None
    )
    return RetrySchedule(
        should_retry=decision.should_retry,
        status=decision.status.value,
        error_code=decision.error_code.value,
        attempt=decision.attempt,
        run_after=run_after,
        delay_seconds=decision.delay_seconds,
    )


# ══════════════════════════════════════════════════════════════════════════
# AC1 — agent capacity/affinity throttle(정책 입력 고려)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CapacityPolicy:
    """Agent capacity/affinity 정책 입력(불변, AC1).

    MVP 단일 Agent 현실 반영: 처리 가능한 capability 를 가진 Agent 가 없거나 aggregate capacity
    가 가득 차면 신규 enqueue 를 보류(throttle)한다(다중 Agent 라우팅은 forward-looking).

    - ``aggregate_capacity``: 모든 Agent in-flight 한도 합(``agents.capacity_json`` 기반).
    - ``aggregate_in_flight``: 현재 활성(PENDING/CLAIMED/RUNNING) job 수.
    - ``capabilities``: capable Agent 들이 처리 가능한 job type 합집합(빈 집합=처리 가능 Agent 없음).
    """

    aggregate_capacity: int
    aggregate_in_flight: int
    capabilities: frozenset[str]
    capacity_by_job_type: dict[str, int] = field(default_factory=dict)
    in_flight_by_job_type: dict[str, int] = field(default_factory=dict)


def can_admit(capacity: CapacityPolicy, job_type: str) -> bool:
    """신규 ``job_type`` enqueue 를 받아들일 수 있는가.

    (1) ``job_type`` 을 처리 가능한 capable Agent 가 있고(``job_type in capabilities``),
    (2) aggregate capacity 여유(``aggregate_in_flight < aggregate_capacity``)일 때만 True.
    둘 중 하나라도 아니면 throttle(보류, AC1) — 같은 초 job 폭주(storm) 방지.
    """

    if job_type not in capacity.capabilities:
        return False
    if capacity.capacity_by_job_type or capacity.in_flight_by_job_type:
        job_capacity = capacity.capacity_by_job_type.get(job_type, 0)
        job_in_flight = capacity.in_flight_by_job_type.get(job_type, 0)
        return job_in_flight < job_capacity
    return capacity.aggregate_in_flight < capacity.aggregate_capacity
