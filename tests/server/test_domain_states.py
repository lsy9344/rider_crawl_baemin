"""Story 2.5 / AC2·AC5 — 상태/지원 enum 정본 잠금.

세 상태머신과 지원 enum이 ``(str, Enum)`` + 대문자(멤버 이름 == 값)로 정의되고, JSON
직렬화가 대문자 문자열로 나가는지 단언한다. 외부 호출 없음 — 순수 enum.
"""

from __future__ import annotations

import json
from enum import Enum

from rider_server.domain import (
    BaeminAuthState,
    BrowserProfileState,
    CustomerLifecycleState,
    Messenger,
    MessengerChannelState,
    MonitoringTargetStatus,
    Platform,
    SecretStorageClass,
    SubscriptionStatus,
)


def _names(enum_cls: type[Enum]) -> set[str]:
    return {member.name for member in enum_cls}


def test_all_enums_are_str_enum_with_name_equals_uppercase_value() -> None:
    enums = [
        CustomerLifecycleState,
        SubscriptionStatus,
        BaeminAuthState,
        Platform,
        Messenger,
        SecretStorageClass,
        MonitoringTargetStatus,
        MessengerChannelState,
        BrowserProfileState,
    ]
    for enum_cls in enums:
        assert issubclass(enum_cls, str), f"{enum_cls.__name__} must be (str, Enum)"
        for member in enum_cls:
            # 멤버 이름 == 값 == 대문자 문자열 (DB/API 문자열 정본 일치)
            assert member.value == member.name
            assert member.value == member.value.upper()
            assert member == member.value  # (str, Enum) 동등성


def test_str_enum_json_serializes_to_uppercase_string() -> None:
    # (str, Enum)이라 json.dumps가 대문자 문자열로 직렬화된다.
    assert json.dumps([CustomerLifecycleState.ACTIVE]) == '["ACTIVE"]'
    assert json.dumps([BaeminAuthState.CENTER_MISMATCH]) == '["CENTER_MISMATCH"]'
    assert CustomerLifecycleState.ACTIVE == "ACTIVE"


def test_customer_lifecycle_state_has_exact_11_members_in_contract_order() -> None:
    expected = [
        "LEAD",
        "SIGNED_UP",
        "PAYMENT_ACTIVE",
        "SETUP_PENDING",
        "PLATFORM_AUTH_PENDING",
        "MESSENGER_VERIFY_PENDING",
        "TEST_RUNNING",
        "ACTIVE",
        "DEGRADED",
        "AUTH_REQUIRED",
        "SUSPENDED",
    ]
    assert [m.name for m in CustomerLifecycleState] == expected
    assert len(list(CustomerLifecycleState)) == 11


def test_active_auth_required_degraded_suspended_are_four_distinct_members() -> None:
    # AC5: 네 상태가 MVP에서 서로 구분되는 별개 멤버.
    distinct = {
        CustomerLifecycleState.ACTIVE,
        CustomerLifecycleState.AUTH_REQUIRED,
        CustomerLifecycleState.DEGRADED,
        CustomerLifecycleState.SUSPENDED,
    }
    assert len(distinct) == 4


def test_subscription_status_has_exact_4_members() -> None:
    assert _names(SubscriptionStatus) == {
        "PAYMENT_ACTIVE",
        "PAYMENT_FAILED_GRACE",
        "SUSPENDED",
        "CANCELLED",
    }


def test_baemin_auth_state_has_exact_7_members() -> None:
    assert _names(BaeminAuthState) == {
        "UNKNOWN",
        "ACTIVE",
        "AUTH_REQUIRED",
        "USER_ACTION_PENDING",
        "AUTH_VERIFIED",
        "CENTER_MISMATCH",
        "BLOCKED_OR_CAPTCHA",
    }


def test_baemin_active_and_customer_active_are_different_typed_members() -> None:
    # 동명 멤버지만 서로 다른 타입(혼동 방지는 필드 타입으로).
    assert BaeminAuthState.ACTIVE == "ACTIVE"
    assert CustomerLifecycleState.ACTIVE == "ACTIVE"
    assert BaeminAuthState.ACTIVE is not CustomerLifecycleState.ACTIVE


def test_support_enums_have_expected_members() -> None:
    assert _names(Platform) == {"BAEMIN", "COUPANG"}
    assert _names(Messenger) == {"TELEGRAM", "KAKAO"}
    assert _names(SecretStorageClass) == {"CENTRAL", "AGENT_LOCAL", "NOT_STORED"}
    assert _names(MonitoringTargetStatus) == {"ACTIVE", "PAUSED", "INACTIVE"}
    assert _names(MessengerChannelState) == {
        "PENDING",
        "VERIFIED",
        "ACTIVE",
        "INACTIVE",
    }
    assert _names(BrowserProfileState) == {"UNKNOWN", "READY", "IN_USE", "INACTIVE"}


def test_soft_delete_inactive_members_exist() -> None:
    # AC6: 대상/채널 비활성 판별값 INACTIVE 존재.
    assert MonitoringTargetStatus.INACTIVE == "INACTIVE"
    assert MessengerChannelState.INACTIVE == "INACTIVE"


# --- QA 보강: 계약 순서·동명 멤버 충돌·전수 직렬화(gap fill) ---


def test_baemin_auth_state_in_contract_order() -> None:
    # AC2: data-api-contract(122-131) 순서 정본 잠금(기존엔 집합만 단언).
    assert [m.name for m in BaeminAuthState] == [
        "UNKNOWN",
        "ACTIVE",
        "AUTH_REQUIRED",
        "USER_ACTION_PENDING",
        "AUTH_VERIFIED",
        "CENTER_MISMATCH",
        "BLOCKED_OR_CAPTCHA",
    ]


def test_subscription_status_in_contract_order() -> None:
    # AC2/AC5: data-api-contract(112-119) 실행 게이트 순서 정본 잠금.
    assert [m.name for m in SubscriptionStatus] == [
        "PAYMENT_ACTIVE",
        "PAYMENT_FAILED_GRACE",
        "SUSPENDED",
        "CANCELLED",
    ]


def test_suspended_is_distinct_across_lifecycle_and_subscription_enums() -> None:
    # AC5: SUSPENDED는 CustomerLifecycleState·SubscriptionStatus 양쪽에 동명으로 있지만
    # 서로 다른 타입의 별개 멤버다(SubscriptionStatus는 별도 enum — 게이트 값 정본).
    assert SubscriptionStatus.SUSPENDED == "SUSPENDED"
    assert CustomerLifecycleState.SUSPENDED == "SUSPENDED"
    assert SubscriptionStatus.SUSPENDED is not CustomerLifecycleState.SUSPENDED


def test_every_enum_member_json_serializes_to_its_uppercase_name() -> None:
    # AC2: 2개 spot-check를 넘어 전 enum·전 멤버가 대문자 문자열로 직렬화됨을 잠근다.
    enums = [
        CustomerLifecycleState,
        SubscriptionStatus,
        BaeminAuthState,
        Platform,
        Messenger,
        SecretStorageClass,
        MonitoringTargetStatus,
        MessengerChannelState,
        BrowserProfileState,
    ]
    for enum_cls in enums:
        for member in enum_cls:
            assert json.dumps(member) == f'"{member.name}"'
