"""Story 2.5 / AC1·AC2·AC3·AC6·AC7 — 8개 도메인 dataclass 정본 잠금.

임포트·필드·frozen·관계·fan-out·soft delete를 단언한다. 외부 호출 없음 — 순수 객체.
모든 fixture는 가짜 ID/ref만 사용한다(실제 토큰/전화/이메일/chat_id 형태 금지 — A1).
"""

from __future__ import annotations

import dataclasses
from datetime import datetime

import pytest

import rider_server.domain as domain_pkg
from rider_server.domain import (
    BaeminAuthState,
    BrowserProfile,
    BrowserProfileState,
    CustomerLifecycleState,
    DeliveryLog,
    DeliveryRule,
    DeliveryStatus,
    FailureCategory,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    SecretRef,
    SecretStorageClass,
    Subscription,
    SubscriptionStatus,
    Tenant,
)

# --- 가짜 fixture(평문 secret 없음 — A1) ---
_FAKE_USERNAME = "vault://t/user-ref"
_FAKE_PASSWORD = "vault://t/pass-ref"
_FAKE_PROFILE_REF = SecretRef("local:profile-ref", SecretStorageClass.AGENT_LOCAL)


def _fields(cls: type) -> set[str]:
    return {f.name for f in dataclasses.fields(cls)}


def make_target() -> MonitoringTarget:
    return MonitoringTarget(
        id="mt-1",
        tenant_id="tnt-1",
        platform_account_id="acc-1",
        name="표시명",
        center_name="기대-센터",
        external_id="ext-1",
        url="https://example.invalid/store",
    )


def make_channel() -> MessengerChannel:
    return MessengerChannel(
        id="ch-1",
        tenant_id="tnt-1",
        messenger=Messenger.TELEGRAM,
        telegram_chat_id="-100123",
        thread_id="7",
    )


# --- AC1: 임포트·필드 집합 ---


def test_all_eight_models_importable() -> None:
    models = [
        Tenant,
        Subscription,
        PlatformAccount,
        MonitoringTarget,
        BrowserProfile,
        MessengerChannel,
        DeliveryRule,
        SecretRef,
    ]
    assert all(dataclasses.is_dataclass(m) for m in models)


def test_model_field_sets_match_contract() -> None:
    # 0012: tenant 별 텔레그램 설정(봇 토큰/webhook secret/실발송 게이트) 추가.
    assert _fields(Tenant) == {
        "id",
        "name",
        "status",
        "created_at",
        "telegram_bot_token",
        "telegram_webhook_secret",
        "sending_enabled",
    }
    assert _fields(Subscription) == {
        "id",
        "tenant_id",
        "plan",
        "status",
        "current_period_end",
        "quotas",
    }
    assert _fields(PlatformAccount) == {
        "id",
        "tenant_id",
        "platform",
        "label",
        "username",
        "password",
        "verification_email_address",
        "verification_email_app_password",
        "verification_email_subject_keyword",
        "verification_email_sender_keyword",
        "auth_state",
    }
    assert _fields(MonitoringTarget) == {
        "id",
        "tenant_id",
        "platform_account_id",
        "name",
        "center_name",
        "external_id",
        "url",
        "interval_minutes",
        "schedule_enabled",
        "start_time",
        "stop_time",
        "status",
    }
    assert _fields(BrowserProfile) == {
        "id",
        "agent_id",
        "target_id",
        "profile_path_ref",
        "cdp_port",
        "state",
    }
    assert _fields(MessengerChannel) == {
        "id",
        "tenant_id",
        "messenger",
        "telegram_chat_id",
        "thread_id",
        "kakao_room_name",
        "state",
    }
    assert _fields(DeliveryRule) == {
        "id",
        "target_id",
        "channel_id",
        "template_id",
        "enabled",
        "send_only_on_change",
    }
    assert _fields(SecretRef) == {"ref", "storage_class", "secret_kind"}


def test_models_are_frozen() -> None:
    target = make_target()
    with pytest.raises(dataclasses.FrozenInstanceError):
        target.id = "mt-2"  # type: ignore[misc]


def test_default_typed_state_fields() -> None:
    account = PlatformAccount(
        id="acc-1",
        tenant_id="tnt-1",
        platform=Platform.BAEMIN,
        label="배민 계정",
        username=_FAKE_USERNAME,
        password=_FAKE_PASSWORD,
    )
    assert account.auth_state is BaeminAuthState.UNKNOWN
    assert account.verification_email_address == ""
    assert account.verification_email_app_password == ""
    assert account.verification_email_subject_keyword == "인증번호"
    assert account.verification_email_sender_keyword == "coupang"
    assert make_target().status is MonitoringTargetStatus.ACTIVE
    assert make_channel().state is MessengerChannelState.PENDING
    tenant = Tenant(id="tnt-1", name="고객", status=CustomerLifecycleState.LEAD,
                     created_at=datetime(2026, 1, 1))
    assert tenant.status is CustomerLifecycleState.LEAD
    sub = Subscription(id="sub-1", tenant_id="tnt-1", plan="basic",
                       status=SubscriptionStatus.PAYMENT_ACTIVE)
    assert sub.quotas == {}  # default_factory — 자동 now()/공유 가변 기본값 없음


# --- AC2: 관계 + SecretRef 평문 비보유 ---


def test_monitoring_target_relationship_fields() -> None:
    target = make_target()
    assert target.platform_account_id == "acc-1"
    assert target.center_name == "기대-센터"
    assert target.url == "https://example.invalid/store"
    assert target.external_id == "ext-1"


def test_browser_profile_links_target() -> None:
    target = make_target()
    profile = BrowserProfile(
        id="bp-1",
        agent_id="agt-1",
        target_id=target.id,
        profile_path_ref=_FAKE_PROFILE_REF,
        state=BrowserProfileState.READY,
    )
    assert profile.target_id == target.id  # 대상 ↔ 프로필 역참조 연결


def test_platform_account_credentials_are_string_ref_handles() -> None:
    account = PlatformAccount(
        id="acc-1",
        tenant_id="tnt-1",
        platform=Platform.COUPANG,
        label="쿠팡 계정",
        username=_FAKE_USERNAME,
        password=_FAKE_PASSWORD,
    )
    assert isinstance(account.username, str)
    assert isinstance(account.password, str)
    assert isinstance(account.verification_email_address, str)
    assert isinstance(account.verification_email_app_password, str)


# --- AC3(fan-out): 한 대상 → 2채널 ---


def test_delivery_rule_fan_out_one_target_to_multiple_channels() -> None:
    target = make_target()
    rule_a = DeliveryRule(id="dr-1", target_id=target.id, channel_id="ch-1")
    rule_b = DeliveryRule(id="dr-2", target_id=target.id, channel_id="ch-2")
    assert rule_a.target_id == rule_b.target_id == target.id
    assert rule_a.channel_id != rule_b.channel_id
    assert len({rule_a.channel_id, rule_b.channel_id}) == 2  # 2개 이상 채널 fan-out


# --- AC6·AC7: soft delete(물리 삭제 금지) ---


def test_soft_delete_target_preserves_history() -> None:
    target = make_target()
    inactive = dataclasses.replace(target, status=MonitoringTargetStatus.INACTIVE)
    assert inactive.status is MonitoringTargetStatus.INACTIVE
    # id·관계 FK·이름 보존(물리 삭제 아님).
    assert inactive.id == target.id
    assert inactive.tenant_id == target.tenant_id
    assert inactive.platform_account_id == target.platform_account_id
    assert inactive.name == target.name
    assert inactive.center_name == target.center_name


def test_soft_delete_channel_and_rule_state_values() -> None:
    channel = make_channel()
    inactive_channel = dataclasses.replace(channel, state=MessengerChannelState.INACTIVE)
    assert inactive_channel.state is MessengerChannelState.INACTIVE
    assert inactive_channel.id == channel.id
    assert inactive_channel.telegram_chat_id == channel.telegram_chat_id

    rule = DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1")
    disabled = dataclasses.replace(rule, enabled=False)
    assert disabled.enabled is False
    assert disabled.id == rule.id
    assert disabled.target_id == rule.target_id  # 이력 보존


# --- QA 보강: __all__ 재노출·기본값·가변 기본값 격리·Kakao variant·평문 비누출(gap fill) ---


def test_package_all_reexports_eight_models_and_all_enums() -> None:
    # AC1: domain/__init__.py가 8개 모델 + 모든 enum을 __all__로 명시 재노출하는지 잠근다.
    expected = {
        # 8 핵심 도메인 모델
        "Tenant",
        "Subscription",
        "PlatformAccount",
        "MonitoringTarget",
        "BrowserProfile",
        "MessengerChannel",
        "DeliveryRule",
        "SecretRef",
        # Story 3.2 — 정규화 Snapshot 레코드(9번째)
        "Snapshot",
        # Story 3.3 — Message 렌더 레코드(10번째)
        "Message",
        # Story 3.5 — DeliveryLog 전송 결과·dedup 레코드(11번째)
        "DeliveryLog",
        # 상태머신 enum
        "CustomerLifecycleState",
        "SubscriptionStatus",
        "BaeminAuthState",
        "DeliveryStatus",
        # Story 3.6 — error_code 운영 카테고리 enum
        "FailureCategory",
        # Story 5.8 — audit 결과 어휘 enum
        "AuditResult",
        # 지원 enum
        "Platform",
        "Messenger",
        "SecretStorageClass",
        "MonitoringTargetStatus",
        "MessengerChannelState",
        "BrowserProfileState",
        "SnapshotQualityState",
    }
    assert set(domain_pkg.__all__) == expected
    assert len(domain_pkg.__all__) == len(set(domain_pkg.__all__))  # 중복 없음
    for name in domain_pkg.__all__:
        assert hasattr(domain_pkg, name), f"{name} not re-exported"
    model_names = {
        "Tenant",
        "Subscription",
        "PlatformAccount",
        "MonitoringTarget",
        "BrowserProfile",
        "MessengerChannel",
        "DeliveryRule",
        "SecretRef",
        "Snapshot",  # Story 3.2 — 9번째 도메인 모델
        "Message",  # Story 3.3 — 10번째 도메인 모델
        "DeliveryLog",  # Story 3.5 — 11번째 도메인 모델
    }
    for name in model_names:
        assert dataclasses.is_dataclass(getattr(domain_pkg, name))


def test_optional_field_defaults_match_contract() -> None:
    # AC1/AC3: 계약 기본값 정본 잠금(미지정 시 빈/비활성 아님/None).
    target = MonitoringTarget(
        id="mt-1",
        tenant_id="tnt-1",
        platform_account_id="acc-1",
        name="표시명",
        center_name="기대-센터",
    )
    assert target.external_id == ""
    assert target.url == ""
    assert target.interval_minutes == 0
    assert target.schedule_enabled is False
    assert target.start_time == ""
    assert target.stop_time == ""
    assert target.status is MonitoringTargetStatus.ACTIVE

    rule = DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1")
    assert rule.template_id == ""
    assert rule.enabled is True  # 기본은 활성 — soft delete만 enabled=False(AC6)
    assert rule.send_only_on_change is False

    profile = BrowserProfile(
        id="bp-1", agent_id="agt-1", target_id="mt-1", profile_path_ref=_FAKE_PROFILE_REF
    )
    assert profile.cdp_port is None
    assert profile.state is BrowserProfileState.UNKNOWN

    sub = Subscription(
        id="sub-1", tenant_id="tnt-1", plan="basic", status=SubscriptionStatus.PAYMENT_ACTIVE
    )
    assert sub.current_period_end is None

    ref = SecretRef("vault://t/ref", SecretStorageClass.CENTRAL)
    assert ref.secret_kind == ""


def test_subscription_quotas_default_not_shared_between_instances() -> None:
    # dev-note(d): default_factory라 인스턴스 간 가변 기본값(dict)을 공유하지 않는다.
    a = Subscription(
        id="sub-1", tenant_id="tnt-1", plan="basic", status=SubscriptionStatus.PAYMENT_ACTIVE
    )
    b = Subscription(
        id="sub-2", tenant_id="tnt-2", plan="pro", status=SubscriptionStatus.PAYMENT_ACTIVE
    )
    a.quotas["jobs"] = 5  # frozen은 속성 재할당만 막음 — dict 내용 변경은 가능
    assert b.quotas == {}
    assert a.quotas is not b.quotas


def test_messenger_channel_kakao_variant() -> None:
    # AC1: 텔레그램 chat/topic 또는 Kakao room 중 한쪽만 채워진다 — Kakao 변형 잠금.
    kakao = MessengerChannel(
        id="ch-2",
        tenant_id="tnt-1",
        messenger=Messenger.KAKAO,
        kakao_room_name="가게-방",
    )
    assert kakao.messenger is Messenger.KAKAO
    assert kakao.kakao_room_name == "가게-방"
    assert kakao.telegram_chat_id is None  # 텔레그램 라우팅 식별자는 비어 있음
    assert kakao.thread_id is None
    assert kakao.state is MessengerChannelState.PENDING


def test_secret_ref_holds_only_opaque_handle_no_plaintext_leak() -> None:
    # NFR-8/ADD-15: SecretRef는 핸들/분류/메타뿐 — 저장값·repr에 평문 secret이 없다.
    ref = SecretRef(
        "vault://t/pass-ref", SecretStorageClass.AGENT_LOCAL, secret_kind="coupang_password"
    )
    assert dataclasses.astuple(ref) == (
        "vault://t/pass-ref",
        "AGENT_LOCAL",
        "coupang_password",
    )
    assert "vault://t/pass-ref" in repr(ref)  # 불투명 핸들만 노출(평문 비밀번호/토큰 아님)


# --- Story 3.5: DeliveryLog(11번째 모델) + DeliveryStatus 계약 정본 잠금 ---


def test_delivery_log_field_set_and_defaults_match_contract() -> None:
    # AC4: data-api-contract delivery_logs 컬럼 집합·기본값(error_code/sent_at=None) 잠금.
    assert _fields(DeliveryLog) == {
        "id",
        "message_id",
        "channel_id",
        "status",
        "dedup_key",
        "error_code",
        "sent_at",
    }
    log = DeliveryLog(
        id="dl-1",
        message_id="msg-1",
        channel_id="ch-tg",
        status=DeliveryStatus.SENT,
        dedup_key="mt-1|ch-tg|2026-01-01T00:00:00|tmpl.v1|" + "a" * 64,
    )
    assert log.error_code is None  # 3.6 소유 — 본 스토리는 항상 None
    assert log.sent_at is None  # 미주입 시 None(SENT만 호출부가 sent_at 주입)
    with pytest.raises(dataclasses.FrozenInstanceError):
        log.status = DeliveryStatus.DUPLICATE_BLOCKED  # type: ignore[misc]


def test_delivery_status_has_exactly_five_members_str_enum() -> None:
    # Story 3.6 계약 반영 갱신: 3.5의 dedup 결과 2개(SENT/DUPLICATE_BLOCKED)에 FR-26의 채널별
    # 운영 상태 3개(FAILED/RETRYING/HELD)가 additive로 더해져 5멤버. (str, Enum)·멤버명==대문자값.
    expected = {"SENT", "DUPLICATE_BLOCKED", "SENDING", "FAILED", "RETRYING", "HELD"}
    assert {s.value for s in DeliveryStatus} == expected
    assert {s.name for s in DeliveryStatus} == expected
    assert DeliveryStatus.SENT == "SENT"  # str enum — json 직렬화 시 "SENT"
    assert DeliveryStatus.DUPLICATE_BLOCKED == "DUPLICATE_BLOCKED"
    assert DeliveryStatus.SENDING == "SENDING"
    assert DeliveryStatus.FAILED == "FAILED"
    assert DeliveryStatus.RETRYING == "RETRYING"
    assert DeliveryStatus.HELD == "HELD"


def test_delivery_log_error_code_can_carry_failure_category_value() -> None:
    # Story 3.6: DeliveryLog 필드는 무증가 — error_code(str | None)에 FailureCategory 값을
    # 담을 수 있음을 잠근다(3.5는 항상 None, 3.6이 분류 값을 채운다).
    log = DeliveryLog(
        id="dl-1",
        message_id="msg-1",
        channel_id="ch-kakao",
        status=DeliveryStatus.FAILED,
        dedup_key="mt-1|ch-kakao|2026-01-01T00:00:00|tmpl.v1|" + "a" * 64,
        error_code=FailureCategory.KAKAO_FAILURE.value,
    )
    assert log.error_code == "KAKAO_FAILURE"
    assert log.status is DeliveryStatus.FAILED
    # 필드 집합은 그대로(무증가).
    assert _fields(DeliveryLog) == {
        "id",
        "message_id",
        "channel_id",
        "status",
        "dedup_key",
        "error_code",
        "sent_at",
    }
