"""Story 2.6 / AC1~AC3 (FR-6·FR-30, ADD-9) — SubscriptionGate 순수 게이트 정책 잠금.

외부 호출 없음 — 순수 함수·frozen 값 객체만 단언한다. 모든 fixture는 가짜 ID
(``"sub-1"``/``"tnt-1"``)·가짜 사유 문자열·고정 ``datetime`` 만 쓴다(비결정 ``now()``
금지, secret/식별자 평문 금지).
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime

import pytest

from rider_server.domain import Subscription, SubscriptionStatus
from rider_server.services import (
    DispatchJobStatus,
    GateDecision,
    HeldDisposition,
    SubscriptionGate,
    SubscriptionStateChange,
)

# 결정성: 게이트 내부 now() 금지 — 전이 시각은 호출부가 주입한다(고정값).
_FIXED_AT = datetime(2026, 1, 1, 0, 0, 0)


def _make_subscription(status: SubscriptionStatus) -> Subscription:
    return Subscription(
        id="sub-1",
        tenant_id="tnt-1",
        plan="basic",
        status=status,
        current_period_end=datetime(2026, 2, 1),
        quotas={"crawl_per_day": 10},
    )


# ── AC1: evaluate() 예약/전송 게이트 ──────────────────────────────────────


def test_payment_active_allows_crawl_and_dispatch_no_warning() -> None:
    decision = SubscriptionGate.evaluate(_make_subscription(SubscriptionStatus.PAYMENT_ACTIVE))
    assert decision.allow_new_crawl_job is True
    assert decision.allow_new_dispatch_job is True
    assert decision.warn_admin is False
    assert decision.reason == "PAYMENT_ACTIVE"


def test_payment_failed_grace_continues_but_warns_admin() -> None:
    # 결제 실패 유예 — 계속 수집/전송하되 Admin 경고 표시(data-api-contract 117).
    decision = SubscriptionGate.evaluate(
        _make_subscription(SubscriptionStatus.PAYMENT_FAILED_GRACE)
    )
    assert decision.allow_new_crawl_job is True
    assert decision.allow_new_dispatch_job is True
    assert decision.warn_admin is True
    assert decision.reason == "PAYMENT_FAILED_GRACE"


def test_suspended_blocks_new_jobs() -> None:
    decision = SubscriptionGate.evaluate(_make_subscription(SubscriptionStatus.SUSPENDED))
    assert decision.allow_new_crawl_job is False
    assert decision.allow_new_dispatch_job is False
    assert decision.warn_admin is True
    assert decision.reason == "SUSPENDED"


def test_cancelled_blocks_new_jobs() -> None:
    # 차단만 — secret revoke/profile archival(retention)은 Epic 5 소유.
    decision = SubscriptionGate.evaluate(_make_subscription(SubscriptionStatus.CANCELLED))
    assert decision.allow_new_crawl_job is False
    assert decision.allow_new_dispatch_job is False
    assert decision.warn_admin is True
    assert decision.reason == "CANCELLED"


def test_evaluate_does_not_mutate_subscription() -> None:
    # 비파괴: 게이트는 작업 생성만 막고 구독/설정/secret을 변경하지 않는다(AC1 보존).
    sub = _make_subscription(SubscriptionStatus.SUSPENDED)
    SubscriptionGate.evaluate(sub)
    assert sub.status == SubscriptionStatus.SUSPENDED
    assert sub.id == "sub-1" and sub.tenant_id == "tnt-1"


def test_unknown_status_is_fail_closed_blocked() -> None:
    # fail-closed 불변식 ③: 미매핑/미지 상태는 허용이 아니라 차단으로 판정.
    decision = SubscriptionGate.evaluate_status("MYSTERY_STATE")  # type: ignore[arg-type]
    assert decision.allow_new_crawl_job is False
    assert decision.allow_new_dispatch_job is False
    assert decision.reason == "UNKNOWN"


def test_gate_decision_is_frozen() -> None:
    decision = SubscriptionGate.evaluate_status(SubscriptionStatus.PAYMENT_ACTIVE)
    assert dataclasses.is_dataclass(decision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.allow_new_crawl_job = False  # type: ignore[misc]


# ── AC2: 중지 전환 시 미전송 → HELD, 성공 기록 재발송 금지 ────────────────


def test_hold_undelivered_pending_becomes_held() -> None:
    assert SubscriptionGate.hold_undelivered(DispatchJobStatus.PENDING) == DispatchJobStatus.HELD


def test_hold_undelivered_held_is_idempotent() -> None:
    assert SubscriptionGate.hold_undelivered(DispatchJobStatus.HELD) == DispatchJobStatus.HELD


def test_hold_undelivered_never_touches_succeeded() -> None:
    # 불변식 ①: 성공 기록은 보류로도, 발송 가능 상태로도 되돌아가지 않는다.
    assert (
        SubscriptionGate.hold_undelivered(DispatchJobStatus.SUCCEEDED)
        == DispatchJobStatus.SUCCEEDED
    )


def test_hold_undelivered_discarded_stays_discarded() -> None:
    assert (
        SubscriptionGate.hold_undelivered(DispatchJobStatus.DISCARDED)
        == DispatchJobStatus.DISCARDED
    )


# ── AC3: 복구 시 HELD는 운영자 확인 후에만, 중지 사유·시각 기록 ────────────


def test_dispose_held_discard_returns_discarded() -> None:
    assert (
        SubscriptionGate.dispose_held(DispatchJobStatus.HELD, HeldDisposition.DISCARD)
        == DispatchJobStatus.DISCARDED
    )


def test_dispose_held_resume_returns_pending() -> None:
    # 운영자 RESUME 결정 시에만 재발송 후보(PENDING)로 복귀.
    assert (
        SubscriptionGate.dispose_held(DispatchJobStatus.HELD, HeldDisposition.RESUME)
        == DispatchJobStatus.PENDING
    )


@pytest.mark.parametrize(
    "status",
    [DispatchJobStatus.SUCCEEDED, DispatchJobStatus.PENDING, DispatchJobStatus.DISCARDED],
)
@pytest.mark.parametrize("disposition", [HeldDisposition.DISCARD, HeldDisposition.RESUME])
def test_dispose_held_rejects_non_held(status, disposition) -> None:
    # fail-closed 불변식 ①: HELD가 아닌 입력(특히 SUCCEEDED)은 재발송/오처리 방지로 거부.
    with pytest.raises(ValueError):
        SubscriptionGate.dispose_held(status, disposition)


def test_suspend_transitions_status_and_preserves_other_fields() -> None:
    sub = _make_subscription(SubscriptionStatus.PAYMENT_ACTIVE)
    new_sub, change = SubscriptionGate.suspend(sub, reason="결제 실패", at=_FIXED_AT)

    # status만 전이, 나머지 식별·plan·청구주기·쿼터 보존(dataclasses.replace).
    assert new_sub.status == SubscriptionStatus.SUSPENDED
    assert new_sub.id == "sub-1"
    assert new_sub.tenant_id == "tnt-1"
    assert new_sub.plan == "basic"
    assert new_sub.current_period_end == datetime(2026, 2, 1)
    assert new_sub.quotas == {"crawl_per_day": 10}
    # 원본 불변(frozen) — 입력 구독은 그대로.
    assert sub.status == SubscriptionStatus.PAYMENT_ACTIVE

    assert change.subscription_id == "sub-1"
    assert change.from_status == SubscriptionStatus.PAYMENT_ACTIVE
    assert change.to_status == SubscriptionStatus.SUSPENDED
    assert change.reason == "결제 실패"
    assert change.changed_at == _FIXED_AT


def test_resume_defaults_to_payment_active_and_records_change() -> None:
    suspended = _make_subscription(SubscriptionStatus.SUSPENDED)
    new_sub, change = SubscriptionGate.resume(suspended, reason="결제 정상화", at=_FIXED_AT)

    assert new_sub.status == SubscriptionStatus.PAYMENT_ACTIVE
    assert new_sub.id == "sub-1"
    assert change.from_status == SubscriptionStatus.SUSPENDED
    assert change.to_status == SubscriptionStatus.PAYMENT_ACTIVE
    assert change.reason == "결제 정상화"
    assert change.changed_at == _FIXED_AT


def test_resume_does_not_take_dispatch_argument() -> None:
    # 불변식 ②: 복구는 구독 상태만 바꾸고 HELD Dispatch를 자동으로 건드리지 않는다.
    # 시그니처에 Dispatch 인자가 없음을 구조로 보장(키워드 파라미터 집합 검사).
    import inspect

    params = set(inspect.signature(SubscriptionGate.resume).parameters)
    assert params == {"subscription", "reason", "at", "to_status"}


def test_state_change_is_frozen() -> None:
    _, change = SubscriptionGate.suspend(
        _make_subscription(SubscriptionStatus.PAYMENT_ACTIVE), reason="r", at=_FIXED_AT
    )
    assert dataclasses.is_dataclass(change)
    with pytest.raises(dataclasses.FrozenInstanceError):
        change.reason = "tampered"  # type: ignore[misc]


# ── 직렬화 정본·누출 방지 ─────────────────────────────────────────────────


def test_dispatch_and_disposition_enums_serialize_to_uppercase_string() -> None:
    # (str, Enum) — json.dumps/== 는 대문자 문자열.
    assert json.dumps([DispatchJobStatus.HELD]) == '["HELD"]'
    assert DispatchJobStatus.HELD == "HELD"
    assert json.dumps([HeldDisposition.RESUME]) == '["RESUME"]'
    assert HeldDisposition.DISCARD == "DISCARD"


# ══════════════════════════════════════════════════════════════════════════
# QA gap-fill (bmad-qa-generate-e2e-tests, Story 2.6) — 위 케이스가 비운
# AC/Task 명세 행위를 추가로 잠근다. 전부 순수·additive·가짜 ID·고정 datetime.
# 구현 행위를 그대로 단언(=커버리지 갭, 버그 헌트 아님).
# ══════════════════════════════════════════════════════════════════════════

# ── AC1 보강: 매핑 완전성·delegation·fail-closed 경고 ─────────────────────

# 게이트가 "정상 실행"으로 허용하는 상태(예약·전송 둘 다 True).
_ALLOWED_STATUSES = [
    SubscriptionStatus.PAYMENT_ACTIVE,
    SubscriptionStatus.PAYMENT_FAILED_GRACE,
]
# 게이트가 차단하는 상태(예약·전송 둘 다 False).
_BLOCKED_STATUSES = [
    SubscriptionStatus.SUSPENDED,
    SubscriptionStatus.CANCELLED,
]


@pytest.mark.parametrize("status", list(SubscriptionStatus))
def test_evaluate_status_maps_every_known_status_without_fail_closed(status) -> None:
    # AC1 매핑 완전성: 알려진 4개 SubscriptionStatus는 모두 fail-closed("UNKNOWN")로
    # 새지 않고 자기 상태명을 reason으로 돌려준다. 새 멤버가 매핑 없이 추가되면 깨진다.
    decision = SubscriptionGate.evaluate_status(status)
    assert decision.reason == status.value
    assert decision.reason != "UNKNOWN"


@pytest.mark.parametrize("status", _ALLOWED_STATUSES)
def test_allowed_statuses_permit_both_crawl_and_dispatch(status) -> None:
    decision = SubscriptionGate.evaluate_status(status)
    assert decision.allow_new_crawl_job is True
    assert decision.allow_new_dispatch_job is True


@pytest.mark.parametrize("status", _BLOCKED_STATUSES)
def test_blocked_statuses_forbid_both_crawl_and_dispatch_with_warning(status) -> None:
    decision = SubscriptionGate.evaluate_status(status)
    assert decision.allow_new_crawl_job is False
    assert decision.allow_new_dispatch_job is False
    assert decision.warn_admin is True


@pytest.mark.parametrize("status", list(SubscriptionStatus))
def test_evaluate_agrees_with_evaluate_status(status) -> None:
    # evaluate(subscription)는 evaluate_status(subscription.status)에 위임한다(5.4가
    # 둘 중 무엇을 호출하든 동일 결정). GateDecision은 frozen dataclass → 값 동등.
    sub = _make_subscription(status)
    assert SubscriptionGate.evaluate(sub) == SubscriptionGate.evaluate_status(status)


def test_unknown_status_decision_also_warns_admin() -> None:
    # fail-closed 불변식 ③: 미지 상태는 차단 + Admin 경고(운영자가 인지하도록).
    decision = SubscriptionGate.evaluate_status("MYSTERY_STATE")  # type: ignore[arg-type]
    assert decision.warn_admin is True
    assert decision.reason == "UNKNOWN"


# ── AC2 보강: 보류 멱등(합성)·성공분 보존 ─────────────────────────────────


def test_hold_undelivered_is_composition_idempotent() -> None:
    # 보류를 두 번 적용해도 한 번과 같다(PENDING → HELD → HELD).
    once = SubscriptionGate.hold_undelivered(DispatchJobStatus.PENDING)
    twice = SubscriptionGate.hold_undelivered(once)
    assert once == DispatchJobStatus.HELD
    assert twice == once


def test_no_gate_function_resurrects_succeeded() -> None:
    # 불변식 ① 통합 단언: 어떤 게이트 함수도 SUCCEEDED를 발송 가능/대기 상태로 되돌리지
    # 않는다. hold_undelivered는 그대로 두고, dispose_held는 거부(ValueError)한다.
    assert (
        SubscriptionGate.hold_undelivered(DispatchJobStatus.SUCCEEDED)
        == DispatchJobStatus.SUCCEEDED
    )
    for disposition in HeldDisposition:
        with pytest.raises(ValueError):
            SubscriptionGate.dispose_held(DispatchJobStatus.SUCCEEDED, disposition)


# ── AC3 보강: suspend 멱등·resume to_status 오버라이드·None 보존 ──────────


def test_suspend_is_idempotent_when_already_suspended() -> None:
    # Task 4: from_status==to_status(이미 같은 상태)여도 결정론적으로 기록을 만든다.
    already = _make_subscription(SubscriptionStatus.SUSPENDED)
    new_sub, change = SubscriptionGate.suspend(already, reason="재중지", at=_FIXED_AT)
    assert new_sub.status == SubscriptionStatus.SUSPENDED
    assert change.from_status == SubscriptionStatus.SUSPENDED
    assert change.to_status == SubscriptionStatus.SUSPENDED
    assert change.reason == "재중지"
    assert change.changed_at == _FIXED_AT


def test_resume_honors_explicit_to_status() -> None:
    # resume는 to_status 오버라이드를 받는다(기본 PAYMENT_ACTIVE 외 명시 복구 대상).
    # 여기선 비차단 상태인 PAYMENT_FAILED_GRACE로 복구하는 경로를 잠근다.
    suspended = _make_subscription(SubscriptionStatus.SUSPENDED)
    new_sub, change = SubscriptionGate.resume(
        suspended,
        reason="유예 복구",
        at=_FIXED_AT,
        to_status=SubscriptionStatus.PAYMENT_FAILED_GRACE,
    )
    assert new_sub.status == SubscriptionStatus.PAYMENT_FAILED_GRACE
    assert change.from_status == SubscriptionStatus.SUSPENDED
    assert change.to_status == SubscriptionStatus.PAYMENT_FAILED_GRACE
    # 복구 대상도 게이트가 정상 허용해야 한다(예약·전송 True).
    assert SubscriptionGate.evaluate(new_sub).allow_new_crawl_job is True


def test_suspend_and_resume_preserve_none_period_end() -> None:
    # 경계: current_period_end=None(기본값)도 전이 후 보존된다(status만 바뀜).
    sub = Subscription(
        id="sub-2",
        tenant_id="tnt-2",
        plan="basic",
        status=SubscriptionStatus.PAYMENT_ACTIVE,
        current_period_end=None,
    )
    suspended, _ = SubscriptionGate.suspend(sub, reason="결제 실패", at=_FIXED_AT)
    resumed, _ = SubscriptionGate.resume(suspended, reason="복구", at=_FIXED_AT)
    assert suspended.current_period_end is None
    assert resumed.current_period_end is None
    assert resumed.quotas == {}


# ── 어휘 잠금: enum 멤버 집합 정본(드리프트 가드 — 2.5 == 컨벤션 계승) ──────


def test_dispatch_job_status_members_are_locked() -> None:
    # 게이트-facing 최소 부분집합 정본. 멤버가 추가/삭제되면 의도적으로 깨진다.
    assert {m.value for m in DispatchJobStatus} == {
        "PENDING",
        "HELD",
        "SUCCEEDED",
        "DISCARDED",
    }


def test_held_disposition_members_are_locked() -> None:
    assert {m.value for m in HeldDisposition} == {"DISCARD", "RESUME"}


# ── 직렬화 정본 확장: 모든 멤버 round-trip(== / json.dumps / .value) ──────


@pytest.mark.parametrize("member", list(DispatchJobStatus))
def test_every_dispatch_status_serializes_round_trip(member) -> None:
    # (str, Enum) 정본 직렬화는 .value/== (대문자) — str()/f-string은 신뢰하지 않는다.
    assert member == member.value
    assert json.dumps(member) == f'"{member.value}"'


@pytest.mark.parametrize("member", list(HeldDisposition))
def test_every_held_disposition_serializes_round_trip(member) -> None:
    assert member == member.value
    assert json.dumps(member) == f'"{member.value}"'


# ── E2E 게이트 정책 워크플로(순수 함수 조합 — 한 고객 전체 수명주기) ──────


def test_full_lifecycle_suspend_hold_recover_dispose_workflow() -> None:
    """정상 → 중지(작업 차단·미전송 보류·성공분 보존) → 복구 → 운영자 처리.

    이 모듈의 "E2E" — 외부 의존 0, 순수 게이트 정책 함수만 조합해 한 고객의 전체
    게이트 수명주기와 3 불변식을 한 시나리오로 잠근다.
    """
    # 1) 정상 고객: 신규 작업 허용.
    sub = _make_subscription(SubscriptionStatus.PAYMENT_ACTIVE)
    assert SubscriptionGate.evaluate(sub).allow_new_crawl_job is True

    # 2) 운영자가 결제 실패로 중지 → 신규 작업 차단(보존, 비파괴).
    suspended, suspend_change = SubscriptionGate.suspend(sub, reason="결제 실패", at=_FIXED_AT)
    blocked = SubscriptionGate.evaluate(suspended)
    assert blocked.allow_new_crawl_job is False
    assert blocked.allow_new_dispatch_job is False
    assert suspend_change.from_status == SubscriptionStatus.PAYMENT_ACTIVE
    assert suspend_change.to_status == SubscriptionStatus.SUSPENDED

    # 3) 중지 시점의 미전송/성공 Dispatch 보류 판정.
    held = SubscriptionGate.hold_undelivered(DispatchJobStatus.PENDING)
    kept = SubscriptionGate.hold_undelivered(DispatchJobStatus.SUCCEEDED)
    assert held == DispatchJobStatus.HELD            # 미전송 → 보류
    assert kept == DispatchJobStatus.SUCCEEDED        # 성공분 불변(불변식 ①)

    # 4) 복구: 구독 상태만 PAYMENT_ACTIVE로 — HELD는 자동 발송되지 않음(불변식 ②).
    resumed, resume_change = SubscriptionGate.resume(suspended, reason="결제 정상화", at=_FIXED_AT)
    assert SubscriptionGate.evaluate(resumed).allow_new_crawl_job is True
    assert resume_change.from_status == SubscriptionStatus.SUSPENDED
    assert resume_change.to_status == SubscriptionStatus.PAYMENT_ACTIVE

    # 5) 운영자가 HELD를 명시 처리 — 재개/폐기 둘 다 가능, 성공분은 손대지 못함.
    assert SubscriptionGate.dispose_held(held, HeldDisposition.RESUME) == DispatchJobStatus.PENDING
    assert SubscriptionGate.dispose_held(held, HeldDisposition.DISCARD) == DispatchJobStatus.DISCARDED
    with pytest.raises(ValueError):
        SubscriptionGate.dispose_held(kept, HeldDisposition.RESUME)
