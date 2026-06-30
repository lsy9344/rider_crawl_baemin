"""Story 5.4 / AC1·AC2·AC3 — 순수 scheduler 정책(항상 실행, DB-less).

jitter 결정성·분산, due/next_run_at 경계, 구독 게이트 + lifecycle 합성, job type 매핑,
플랫폼 circuit breaker(30% 경계·min_samples 오탐 방지), error_code별 backoff 가
``DeliveryFailurePolicy`` 와 일치(고정 5초 아님), capacity throttle 을 결정적으로 잠근다.
fake fixture 만 — 실제 토큰/전화/이메일/chat_id 형태 없음. ``random`` 미사용(전부 결정적).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from rider_server.domain import (
    CustomerLifecycleState,
    FailureCategory,
    Platform,
    SubscriptionStatus,
)
from rider_server.queue.states import (
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPES,
)
from rider_server.scheduler import policy
from rider_server.services.delivery_failure_policy import DeliveryFailurePolicy

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_INTERVAL_S = 600  # 10분


# ── AC1: 결정적 jitter ────────────────────────────────────────────────────────

def test_jitter_is_deterministic_per_target() -> None:
    # 같은 target_id 는 항상 같은 jitter(random 미사용).
    assert policy.compute_jitter("target-abc", _INTERVAL_S) == policy.compute_jitter(
        "target-abc", _INTERVAL_S
    )


def test_jitter_within_zero_to_interval_range() -> None:
    for i in range(200):
        j = policy.compute_jitter(f"target-{i}", _INTERVAL_S)
        assert 0 <= j < _INTERVAL_S


def test_jitter_spreads_same_interval_targets_across_seconds() -> None:
    # AC1: 같은 interval 의 다수 대상이 같은 초에 몰리지 않는다(분산 단언).
    jitters = [policy.compute_jitter(f"target-{i}", _INTERVAL_S) for i in range(100)]
    distinct = set(jitters)
    # 100 대상이 600초 범위에 흩어짐(소수 해시 충돌은 정상 — storm 아님). 결정적으로 ≥85 distinct.
    assert len(distinct) >= 85, f"jitter 분산 부족(같은 초 집중): {len(distinct)} distinct"
    # 단일 초 버킷에 과반이 몰리지 않음.
    most_common = max(jitters.count(j) for j in distinct)
    assert most_common <= 3, f"한 초에 {most_common}개 집중 — storm 위험"


def test_jitter_zero_when_interval_non_positive() -> None:
    assert policy.compute_jitter("t", 0) == 0
    assert policy.compute_jitter("t", -5) == 0


# ── AC1: due 판정 · next_run_at 전진 ──────────────────────────────────────────

def test_is_due_none_is_immediately_due() -> None:
    assert policy.is_due(None, _NOW) is True


def test_is_due_boundary_inclusive_now() -> None:
    assert policy.is_due(_NOW, _NOW) is True  # <= now
    assert policy.is_due(_NOW - timedelta(seconds=1), _NOW) is True
    assert policy.is_due(_NOW + timedelta(seconds=1), _NOW) is False


def test_next_run_at_advances_by_configured_interval_not_jitter() -> None:
    assert policy.next_run_at(_NOW, _INTERVAL_S, 17) == _NOW + timedelta(
        seconds=_INTERVAL_S
    )
    assert policy.next_run_at(_NOW, _INTERVAL_S, _INTERVAL_S - 1) == _NOW + timedelta(
        seconds=_INTERVAL_S
    )


# ── AC2: 구독 게이트 + lifecycle 합성 ─────────────────────────────────────────

def test_decide_allows_payment_active_sub_and_active_lifecycle() -> None:
    d = policy.decide_schedule(
        SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.ACTIVE
    )
    assert d.allow_new_crawl_job is True
    assert d.reason == "PAYMENT_ACTIVE"


def test_decide_allows_payment_active_lifecycle_too() -> None:
    # lifecycle 활성 집합 = {ACTIVE, PAYMENT_ACTIVE}.
    d = policy.decide_schedule(
        SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.PAYMENT_ACTIVE
    )
    assert d.allow_new_crawl_job is True


@pytest.mark.parametrize("sub", [SubscriptionStatus.SUSPENDED, SubscriptionStatus.CANCELLED])
def test_decide_blocks_suspended_cancelled_subscription(sub) -> None:
    d = policy.decide_schedule(sub, CustomerLifecycleState.ACTIVE)
    assert d.allow_new_crawl_job is False
    assert d.reason == sub.value


def test_decide_blocks_unmapped_subscription_fail_closed() -> None:
    d = policy.decide_schedule(None, CustomerLifecycleState.ACTIVE)
    assert d.allow_new_crawl_job is False
    assert d.reason == "NO_SUBSCRIPTION"


@pytest.mark.parametrize(
    "lifecycle",
    [CustomerLifecycleState.SETUP_PENDING, CustomerLifecycleState.SUSPENDED, None],
)
def test_decide_blocks_inactive_lifecycle_even_if_gate_passes(lifecycle) -> None:
    d = policy.decide_schedule(SubscriptionStatus.PAYMENT_ACTIVE, lifecycle)
    assert d.allow_new_crawl_job is False
    assert d.reason == "LIFECYCLE_INACTIVE"


def test_decide_preserves_warn_admin_for_grace_period() -> None:
    # PAYMENT_FAILED_GRACE 는 게이트가 allow=True·warn_admin=True — 차단 안 하되 경고 보존(AC2).
    d = policy.decide_schedule(
        SubscriptionStatus.PAYMENT_FAILED_GRACE, CustomerLifecycleState.ACTIVE
    )
    assert d.allow_new_crawl_job is True
    assert d.warn_admin is True


def test_decide_matches_subscription_gate_for_all_statuses() -> None:
    # 재사용 증명: lifecycle 활성일 때 decide 의 allow 는 SubscriptionGate 와 동일(재구현 아님).
    from rider_server.services.subscription_gate import SubscriptionGate

    for sub in SubscriptionStatus:
        gate = SubscriptionGate.evaluate_status(sub)
        d = policy.decide_schedule(sub, CustomerLifecycleState.ACTIVE)
        assert d.allow_new_crawl_job == gate.allow_new_crawl_job


# ── AC1: job type 매핑(정본 6종 중 2종, fail-closed) ──────────────────────────

def test_crawl_job_type_for_platform_enum() -> None:
    assert policy.crawl_job_type_for(Platform.BAEMIN) == JOB_TYPE_CRAWL_BAEMIN
    assert policy.crawl_job_type_for(Platform.COUPANG) == JOB_TYPE_CRAWL_COUPANG


def test_crawl_job_type_for_platform_string() -> None:
    assert policy.crawl_job_type_for("BAEMIN") == JOB_TYPE_CRAWL_BAEMIN
    assert policy.crawl_job_type_for("COUPANG") == JOB_TYPE_CRAWL_COUPANG


def test_crawl_job_type_is_canonical_vocab_not_legacy() -> None:
    # 구표기(CRAWL/RENDER) 금지 — 정본 6종에 속해야 Agent capability 매칭이 안 깨진다.
    for plat in (Platform.BAEMIN, Platform.COUPANG):
        assert policy.crawl_job_type_for(plat) in JOB_TYPES


@pytest.mark.parametrize("bad", ["YOGIYO", "", "baemin"])
def test_crawl_job_type_unknown_platform_fail_closed(bad) -> None:
    with pytest.raises(ValueError):
        policy.crawl_job_type_for(bad)


# ── AC3: 플랫폼 circuit breaker(30% 초과 · min_samples 가드) ──────────────────

def test_breaker_min_samples_guard_prevents_small_sample_false_open() -> None:
    # 1/1=100% 지만 표본 부족 → open 오탐 방지(closed).
    assert policy.evaluate_breaker(1, 1) is False


def test_breaker_opens_above_threshold_with_enough_samples() -> None:
    # 10 표본 중 4 실패 = 40% > 30% → open.
    assert policy.evaluate_breaker(10, 4) is True


def test_breaker_threshold_is_strictly_greater_than_30_percent() -> None:
    # 정확히 30% 는 "초과" 아님 → closed(경계).
    assert policy.evaluate_breaker(10, 3) is False
    # 30% 바로 위는 open.
    assert policy.evaluate_breaker(100, 31) is True


def test_breaker_zero_total_is_closed() -> None:
    assert policy.evaluate_breaker(0, 0) is False


def test_breaker_custom_min_samples() -> None:
    # min_samples=2 면 2/2=100% open.
    assert policy.evaluate_breaker(2, 2, min_samples=2) is True
    assert policy.evaluate_breaker(1, 1, min_samples=2) is False


def test_breaker_state_string_mapping() -> None:
    assert policy.breaker_state(10, 4) == policy.BREAKER_OPEN
    assert policy.breaker_state(10, 1) == policy.BREAKER_CLOSED


# ── AC3: error_code별 backoff 가 DeliveryFailurePolicy 와 일치(고정 5초 아님) ──

def test_retry_run_after_matches_delivery_failure_policy_backoff() -> None:
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.CRAWL_FAILURE, attempt=1, max_attempts=5
    )
    expected_delay = DeliveryFailurePolicy.backoff_delay_seconds(1)
    assert rs.should_retry is True
    assert rs.delay_seconds == expected_delay
    assert rs.run_after == _NOW + timedelta(seconds=expected_delay)


def test_retry_backoff_is_not_fixed_five_seconds() -> None:
    # ADD-15: 고정 5초·무한 재시도 금지 — 결정적 backoff(기본 base=30s).
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.CRAWL_FAILURE, attempt=1, max_attempts=5
    )
    assert rs.delay_seconds != 5
    assert rs.delay_seconds == 30


def test_retry_backoff_grows_per_attempt() -> None:
    delays = [
        policy.retry_run_after(
            _NOW, error_code=FailureCategory.CRAWL_FAILURE, attempt=a, max_attempts=10
        ).delay_seconds
        for a in (1, 2, 3)
    ]
    assert delays == [30, 60, 120]  # 결정적 지수 backoff


def test_retry_auth_required_held_no_infinite_retry() -> None:
    # 사람-개입 카테고리는 무한 재시도하지 않는다(HELD, run_after=None).
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.AUTH_REQUIRED, attempt=1, max_attempts=5
    )
    assert rs.should_retry is False
    assert rs.run_after is None
    assert rs.status == "HELD"


def test_retry_exhausted_attempts_failed_no_run_after() -> None:
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.CRAWL_FAILURE, attempt=5, max_attempts=5
    )
    assert rs.should_retry is False
    assert rs.run_after is None
    assert rs.status == "FAILED"


# ── AC1: capacity/affinity throttle ───────────────────────────────────────────

def test_can_admit_blocks_when_no_capable_agent() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=10, aggregate_in_flight=0, capabilities=frozenset()
    )
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is False


def test_can_admit_blocks_when_capacity_full() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=2,
        aggregate_in_flight=2,
        capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN}),
    )
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is False


def test_can_admit_allows_with_capacity_and_capability() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=2,
        aggregate_in_flight=1,
        capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG}),
    )
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is True


def test_can_admit_uses_job_type_capacity_when_available() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=4,
        aggregate_in_flight=2,
        capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG}),
        capacity_by_job_type={
            JOB_TYPE_CRAWL_BAEMIN: 2,
            JOB_TYPE_CRAWL_COUPANG: 1,
        },
        in_flight_by_job_type={
            JOB_TYPE_CRAWL_BAEMIN: 0,
            JOB_TYPE_CRAWL_COUPANG: 1,
        },
    )

    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is True
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_COUPANG) is False


# ══════════════════════════════════════════════════════════════════════════
# QA gap-fill (qa-generate-e2e-tests, Story 5.4) — 추가 경계/분기 커버리지
# ══════════════════════════════════════════════════════════════════════════

# ── AC2: 게이트 합성 — warn_admin 보존 · lifecycle 활성 집합 전수 ───────────────

def test_decide_suspended_preserves_warn_admin_from_gate() -> None:
    # 차단(SUSPENDED)이어도 게이트가 표시한 운영자 경고를 결정 결과에 보존한다.
    d = policy.decide_schedule(
        SubscriptionStatus.SUSPENDED, CustomerLifecycleState.ACTIVE
    )
    assert d.allow_new_crawl_job is False
    assert d.warn_admin is True


def test_decide_no_subscription_sets_warn_admin() -> None:
    d = policy.decide_schedule(None, CustomerLifecycleState.ACTIVE)
    assert d.reason == "NO_SUBSCRIPTION"
    assert d.warn_admin is True


def test_decide_grace_blocked_by_inactive_lifecycle_preserves_warn() -> None:
    # 게이트는 통과(allow=True·warn_admin=True)지만 lifecycle 비활성 → 차단, 경고 보존.
    d = policy.decide_schedule(
        SubscriptionStatus.PAYMENT_FAILED_GRACE, CustomerLifecycleState.SETUP_PENDING
    )
    assert d.allow_new_crawl_job is False
    assert d.reason == "LIFECYCLE_INACTIVE"
    assert d.warn_admin is True


def test_active_lifecycle_set_is_exactly_active_and_payment_active() -> None:
    assert policy.ACTIVE_LIFECYCLE_STATES == frozenset(
        {CustomerLifecycleState.ACTIVE, CustomerLifecycleState.PAYMENT_ACTIVE}
    )


_NON_ACTIVE_LIFECYCLE = [
    s
    for s in CustomerLifecycleState
    if s not in (CustomerLifecycleState.ACTIVE, CustomerLifecycleState.PAYMENT_ACTIVE)
]


@pytest.mark.parametrize("lifecycle", _NON_ACTIVE_LIFECYCLE)
def test_decide_blocks_every_non_active_lifecycle_state(lifecycle) -> None:
    # AC2: 활성 집합({ACTIVE,PAYMENT_ACTIVE})이 아닌 모든 lifecycle 은 차단(샘플 아닌 전수).
    d = policy.decide_schedule(SubscriptionStatus.PAYMENT_ACTIVE, lifecycle)
    assert d.allow_new_crawl_job is False
    assert d.reason == "LIFECYCLE_INACTIVE"


@pytest.mark.parametrize(
    "lifecycle",
    [CustomerLifecycleState.ACTIVE, CustomerLifecycleState.PAYMENT_ACTIVE],
)
def test_decide_allows_exactly_active_set(lifecycle) -> None:
    assert (
        policy.decide_schedule(
            SubscriptionStatus.PAYMENT_ACTIVE, lifecycle
        ).allow_new_crawl_job
        is True
    )


# ── AC3: circuit breaker 경계(min_samples 경계·custom threshold·100%) ─────────

def test_breaker_total_equal_min_samples_evaluates() -> None:
    # total == min_samples(기본 5) 경계: 표본 충분으로 간주 → 실패율 평가(가드 통과).
    assert policy.evaluate_breaker(5, 5) is True  # 100% > 30%
    assert policy.evaluate_breaker(5, 1) is False  # 20% < 30%


def test_breaker_custom_threshold_shifts_open_point() -> None:
    assert policy.evaluate_breaker(10, 4, threshold=0.5) is False  # 40% < 50%
    assert policy.evaluate_breaker(10, 6, threshold=0.5) is True  # 60% > 50%


def test_breaker_full_failure_opens() -> None:
    assert policy.evaluate_breaker(10, 10) is True


# ── AC3: error_code별 backoff — 사람-개입·결정적·custom 파라미터 ──────────────

def test_retry_target_validation_failure_held_no_retry() -> None:
    # 또 하나의 사람-개입 카테고리도 무한 재시도하지 않는다(HELD, run_after=None).
    rs = policy.retry_run_after(
        _NOW,
        error_code=FailureCategory.TARGET_VALIDATION_FAILURE,
        attempt=1,
        max_attempts=5,
    )
    assert rs.should_retry is False
    assert rs.status == "HELD"
    assert rs.run_after is None


def test_retry_render_failure_is_deterministic_failed() -> None:
    # RENDER_FAILURE = 결정적 실패(같은 Snapshot 재렌더=동일 실패) → FAILED, 재시도 안 함.
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.RENDER_FAILURE, attempt=1, max_attempts=5
    )
    assert rs.should_retry is False
    assert rs.status == "FAILED"
    assert rs.run_after is None


def test_retry_custom_backoff_params_passthrough() -> None:
    # base/factor/cap 주입이 DeliveryFailurePolicy 로 전달돼 결정적 backoff 가 바뀐다.
    rs1 = policy.retry_run_after(
        _NOW,
        error_code=FailureCategory.CRAWL_FAILURE,
        attempt=1,
        max_attempts=5,
        base_seconds=10,
        factor=3,
        cap_seconds=1000,
    )
    assert rs1.delay_seconds == 10
    assert rs1.run_after == _NOW + timedelta(seconds=10)
    rs3 = policy.retry_run_after(
        _NOW,
        error_code=FailureCategory.CRAWL_FAILURE,
        attempt=3,
        max_attempts=5,
        base_seconds=10,
        factor=3,
        cap_seconds=1000,
    )
    assert rs3.delay_seconds == 90  # 10 * 3**2(결정적 지수)


def test_retry_cap_seconds_caps_backoff() -> None:
    rs = policy.retry_run_after(
        _NOW,
        error_code=FailureCategory.CRAWL_FAILURE,
        attempt=10,
        max_attempts=20,
        base_seconds=30,
        factor=2,
        cap_seconds=120,
    )
    assert rs.delay_seconds == 120  # min(120, 30*2**9) → 상한 적용(폭주 방지)


def test_retry_schedule_carries_error_code_and_attempt() -> None:
    rs = policy.retry_run_after(
        _NOW, error_code=FailureCategory.CRAWL_FAILURE, attempt=2, max_attempts=5
    )
    assert rs.error_code == "CRAWL_FAILURE"
    assert rs.attempt == 2


# ── AC1: capacity throttle 경계(zero capacity·마지막 슬롯) ────────────────────

def test_can_admit_zero_capacity_always_blocks() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=0,
        aggregate_in_flight=0,
        capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN}),
    )
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is False


def test_can_admit_last_available_slot() -> None:
    cap = policy.CapacityPolicy(
        aggregate_capacity=3,
        aggregate_in_flight=2,
        capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN}),
    )
    assert policy.can_admit(cap, JOB_TYPE_CRAWL_BAEMIN) is True
