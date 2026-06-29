"""Story 5.11 / AC1·AC2·AC3·AC4 — Admin 엔티티 CRUD(always-run service/순수 + 라우트).

(1) always-run 순수/service(무 DB, in-memory fake repo + 주입 시각/actor): 5개 엔티티
    create/update/deactivate happy path, tenant scope 차단(cross-tenant→TenantScopeViolation),
    플랫폼 계정 비밀번호 DB 저장, center_name 위험 판정(쿠팡 빈/배민기본값), DeliveryRule 1:N
    fan-out, soft-delete 상태값 단언, audit before/after+result 기록.
(2) 라우트(TestClient + 주입 _OPERATOR): POST 200/HTMX fragment, VIEWER→403, 미인증→401,
    tenant 불일치→404, 플랫폼 계정 평문 비밀번호 저장, center_name 위험 경고 fragment.

fake 값만(실제 토큰/전화/이메일/chat_id 형태 금지). 평면 ``tests/server/`` 컨벤션.
``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async service 구동(5.4~5.7 선례).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path

import pytest
from fastapi.testclient import TestClient as _TestClient

from rider_crawl.config import DEFAULT_BAEMIN_CENTER_NAME
from rider_server.domain import (
    BaeminAuthState,
    CustomerLifecycleState,
    DeliveryRule,
    Messenger,
    MessengerChannel,
    MessengerChannelState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Subscription,
    SubscriptionStatus,
    Tenant,
)
from rider_server.main import create_app
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_action_service import (
    AdminActionNotFound,
    TenantScopeViolation,
)
from rider_server.services.admin_entity_service import (
    AdminEntityDeleteBlockedError,
    AdminEntityDuplicateError,
    AdminEntityService,
    InMemoryAdminEntityRepository,
    is_center_name_risky,
)
from rider_server.services.admin_entity_repository_postgres import PostgresAdminEntityRepository
from rider_server.services.channel_registration import InvalidChannelTransition
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 14, 12, 0, 0, tzinfo=timezone.utc)
_TENANT = "tn-1"
_OTHER = "tn-2"
_ACTOR = "11111111-1111-1111-1111-111111111111"
_REF = "vault://handle/ref"
_EMAIL_ADDRESS_REF = "vault://mail/address"
_EMAIL_APP_PASSWORD_REF = "vault://mail/app-password"
_FAKE_SETTINGS = Settings(app_env="test", app_version="9.9.9", build_sha=None, build_time=None)
_SAME_ORIGIN_HEADERS = {"Origin": "http://testserver"}
_OPERATOR = AdminPrincipal(
    actor_id=_ACTOR, role=AdminRole.OPERATOR, mfa_verified=True, source="ADMIN_UI/operator"
)
_VIEWER = AdminPrincipal(
    actor_id=_ACTOR, role=AdminRole.VIEWER, mfa_verified=True, source="ADMIN_UI/viewer"
)


def TestClient(app, *args, **kwargs):  # noqa: N802 - test helper mirrors imported class name.
    headers = dict(_SAME_ORIGIN_HEADERS)
    headers.update(kwargs.pop("headers", {}) or {})
    return _TestClient(app, *args, headers=headers, **kwargs)


def _run(coro):
    return asyncio.run(coro)


def _ref(handle: str = _REF) -> str:
    return handle


def _tenant(tenant_id: str = _TENANT) -> Tenant:
    return Tenant(
        id=tenant_id, name="고객", status=CustomerLifecycleState.ACTIVE, created_at=_NOW
    )


def _account(account_id="pa-1", *, tenant=_TENANT, platform=Platform.BAEMIN) -> PlatformAccount:
    return PlatformAccount(
        id=account_id,
        tenant_id=tenant,
        platform=platform,
        label="계정",
        username=_ref(),
        password=_ref(),
        auth_state=BaeminAuthState.UNKNOWN,
    )


def _target(target_id="mt-1", *, tenant=_TENANT, status=MonitoringTargetStatus.ACTIVE) -> MonitoringTarget:
    return MonitoringTarget(
        id=target_id,
        tenant_id=tenant,
        platform_account_id="pa-1",
        name="가게",
        center_name="센터",
        status=status,
    )


def _channel(channel_id="ch-1", *, tenant=_TENANT, state=MessengerChannelState.ACTIVE) -> MessengerChannel:
    return MessengerChannel(
        id=channel_id,
        tenant_id=tenant,
        messenger=Messenger.TELEGRAM,
        telegram_chat_id="-100123",
        thread_id="7",
        state=state,
    )


def _subscription(
    subscription_id="sub-1",
    *,
    tenant=_TENANT,
    status=SubscriptionStatus.PAYMENT_ACTIVE,
) -> Subscription:
    return Subscription(
        id=subscription_id,
        tenant_id=tenant,
        plan="basic",
        status=status,
    )


def _svc(repo: InMemoryAdminEntityRepository) -> AdminEntityService:
    return AdminEntityService(repo)


class _FailingSessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        raise AssertionError("empty tenant should not query Postgres")

    async def __aexit__(self, exc_type, exc, tb):
        return False


def test_postgres_admin_entity_repository_empty_tenant_lists_are_empty_without_uuid_query() -> None:
    repo = PostgresAdminEntityRepository(_FailingSessionFactory())  # type: ignore[arg-type]

    assert _run(repo.list_platform_accounts("")) == []
    assert _run(repo.list_monitoring_targets("")) == []
    assert _run(repo.list_messenger_channels("")) == []


# ══════════════════════════════════════════════════════════════════════════
# (순수) center_name 위험 판정
# ══════════════════════════════════════════════════════════════════════════

def test_center_name_risky_coupang_blank_or_baemin_default() -> None:
    assert is_center_name_risky(Platform.COUPANG, "") is True
    assert is_center_name_risky(Platform.COUPANG, "   ") is True
    assert is_center_name_risky(Platform.COUPANG, DEFAULT_BAEMIN_CENTER_NAME) is True


def test_center_name_not_risky_coupang_valid_or_baemin() -> None:
    assert is_center_name_risky(Platform.COUPANG, "쿠팡센터-강남") is False
    # 배민은 검증 대상이 아니라 항상 False(차단 아님).
    assert is_center_name_risky(Platform.BAEMIN, "") is False
    assert is_center_name_risky(Platform.BAEMIN, DEFAULT_BAEMIN_CENTER_NAME) is False


# ══════════════════════════════════════════════════════════════════════════
# (service) create happy path
# ══════════════════════════════════════════════════════════════════════════

def test_create_tenant_persists_and_audits() -> None:
    repo = InMemoryAdminEntityRepository()
    svc = _svc(repo)

    tenant = _run(
        svc.create_tenant(entity_id="new-tn", name="신규고객", at=_NOW, actor_id=_ACTOR)
    )

    assert tenant.id == "new-tn"
    assert tenant.created_at == _NOW  # 호출부 주입(자동 now() 금지)
    assert _run(repo.get_tenant("new-tn")).name == "신규고객"
    assert repo.audits[-1].action == "TENANT_CREATE"
    assert repo.audits[-1].result == "SUCCESS"


def test_create_subscription_persists_and_audits() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    subscription = _run(
        svc.create_subscription(
            entity_id="sub-new",
            tenant_id=_TENANT,
            plan="basic",
            status=SubscriptionStatus.PAYMENT_ACTIVE,
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert subscription.id == "sub-new"
    assert subscription.tenant_id == _TENANT
    assert subscription.status is SubscriptionStatus.PAYMENT_ACTIVE
    assert _run(repo.list_subscriptions(_TENANT))[0].id == "sub-new"
    assert repo.audits[-1].action == "SUBSCRIPTION_CREATE"


def test_update_subscription_status_persists_and_audits() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_subscription(_subscription(status=SubscriptionStatus.SUSPENDED))
    svc = _svc(repo)

    updated = _run(
        svc.update_subscription(
            "sub-1",
            tenant_id=_TENANT,
            status=SubscriptionStatus.PAYMENT_ACTIVE,
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert updated.status is SubscriptionStatus.PAYMENT_ACTIVE
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.PAYMENT_ACTIVE
    assert repo.audits[-1].action == "SUBSCRIPTION_UPDATE"


def test_create_platform_account_with_secret_refs() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    account = _run(
        svc.create_platform_account(
            entity_id="pa-new",
            tenant_id=_TENANT,
            platform=Platform.COUPANG,
            label="쿠팡",
            username="vault://u",
            password="vault://p",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert account.username == "vault://u"
    stored = _run(repo.get_platform_account("pa-new"))
    assert stored.platform is Platform.COUPANG
    assert stored.verification_email_address == ""
    assert stored.verification_email_app_password == ""
    assert stored.verification_email_subject_keyword == "인증번호"
    assert stored.verification_email_sender_keyword == "coupang"
    diff = repo.audits[-1].diff_redacted
    assert "username" not in diff
    assert "password" not in diff


def test_create_platform_account_with_verification_email() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    account = _run(
        svc.create_platform_account(
            entity_id="pa-mail",
            tenant_id=_TENANT,
            platform=Platform.COUPANG,
            label="쿠팡",
            username="vault://u",
            password="vault://p",
            verification_email_address=_EMAIL_ADDRESS_REF,
            verification_email_app_password=_EMAIL_APP_PASSWORD_REF,
            verification_email_subject_keyword="보안코드",
            verification_email_sender_keyword="wing",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert account.verification_email_address == _EMAIL_ADDRESS_REF
    assert account.verification_email_app_password == _EMAIL_APP_PASSWORD_REF
    assert account.verification_email_subject_keyword == "보안코드"
    assert account.verification_email_sender_keyword == "wing"
    diff = repo.audits[-1].diff_redacted
    assert "verification_email_address" not in diff
    assert "verification_email_app_password" not in diff


def test_create_platform_account_plaintext_password_stored() -> None:
    # 옵션 B: 평문 자격증명을 그대로 저장한다(핸들 강제 없음).
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    _run(
        svc.create_platform_account(
            entity_id="pa-plain",
            tenant_id=_TENANT,
            platform=Platform.BAEMIN,
            label="plain",
            username="myuser",
            password="mypass",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    stored = _run(repo.get_platform_account("pa-plain"))
    assert stored is not None
    assert stored.username == "myuser"
    assert stored.password == "mypass"


def test_create_platform_account_plaintext_email_secret_stored() -> None:
    # 옵션 B: 핸들과 평문을 섞어도 모두 그대로 저장한다.
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    _run(
        svc.create_platform_account(
            entity_id="pa-mail-plain",
            tenant_id=_TENANT,
            platform=Platform.COUPANG,
            label="mail",
            username="vault://coupang/user",
            password="vault://coupang/password",
            verification_email_address="mail@example.test",
            verification_email_app_password="plain-app-password",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    stored = _run(repo.get_platform_account("pa-mail-plain"))
    assert stored is not None
    assert stored.username == "vault://coupang/user"
    assert stored.verification_email_address == "mail@example.test"
    assert stored.verification_email_app_password == "plain-app-password"


def test_create_monitoring_target_links_account_and_flags_valid_coupang_center() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    svc = _svc(repo)

    result = _run(
        svc.create_monitoring_target(
            entity_id="mt-new",
            tenant_id=_TENANT,
            platform_account_id="pa-1",
            name="대상",
            center_name="쿠팡센터-강남",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert result.center_name_risky is False
    assert result.target.status is MonitoringTargetStatus.ACTIVE
    assert _run(repo.get_monitoring_target("mt-new")).name == "대상"
    assert repo.audits[-1].action == "MONITORING_TARGET_CREATE"


def test_create_monitoring_target_persists_send_window() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    svc = _svc(repo)

    result = _run(
        svc.create_monitoring_target(
            entity_id="mt-new",
            tenant_id=_TENANT,
            platform_account_id="pa-1",
            name="대상",
            center_name="센터",
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert result.target.schedule_enabled is True
    assert result.target.start_time == "09:00"
    assert result.target.stop_time == "22:00"
    stored = _run(repo.get_monitoring_target("mt-new"))
    assert stored is not None
    assert stored.schedule_enabled is True
    assert stored.start_time == "09:00"
    assert stored.stop_time == "22:00"


@pytest.mark.parametrize(
    "start_time,stop_time",
    [
        ("", "22:00"),
        ("09:00", ""),
        ("25:00", "22:00"),
        ("09:00", "09:00"),
    ],
)
def test_create_monitoring_target_rejects_invalid_enabled_send_window(
    start_time: str, stop_time: str
) -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.create_monitoring_target(
                entity_id="mt-new",
                tenant_id=_TENANT,
                platform_account_id="pa-1",
                name="대상",
                center_name="센터",
                schedule_enabled=True,
                start_time=start_time,
                stop_time=stop_time,
                at=_NOW,
                actor_id=_ACTOR,
            )
        )

    assert _run(repo.get_monitoring_target("mt-new")) is None
    assert repo.audits == []


def test_create_coupang_blank_center_rejected() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.create_monitoring_target(
                entity_id="mt-new",
                tenant_id=_TENANT,
                platform_account_id="pa-1",
                name="대상",
                center_name="",
                at=_NOW,
                actor_id=_ACTOR,
            )
        )

    assert _run(repo.get_monitoring_target("mt-new")) is None
    assert repo.audits == []


def test_create_telegram_messenger_channel_is_pending() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    channel = _run(
        svc.create_messenger_channel(
            entity_id="ch-new",
            tenant_id=_TENANT,
            messenger=Messenger.TELEGRAM,
            telegram_chat_id="-100999",
            registration_code="JOIN-CODE",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert channel.state is MessengerChannelState.PENDING
    assert _run(repo.get_messenger_channel("ch-new")).telegram_chat_id == "-100999"


def test_create_kakao_messenger_channel_with_room_is_active() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    channel = _run(
        svc.create_messenger_channel(
            entity_id="ch-kakao",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name="실적공유방",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert channel.state is MessengerChannelState.ACTIVE
    assert _run(repo.get_messenger_channel("ch-kakao")).state is MessengerChannelState.ACTIVE
    assert repo.audits[-1].diff_redacted["to_state"] == "ACTIVE"


def test_create_kakao_messenger_channel_rejects_duplicate_active_room() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-existing",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name="실적공유방",
            state=MessengerChannelState.ACTIVE,
        )
    )
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.create_messenger_channel(
                entity_id="ch-kakao",
                tenant_id=_TENANT,
                messenger=Messenger.KAKAO,
                kakao_room_name="실적공유방",
                at=_NOW,
                actor_id=_ACTOR,
            )
        )

    assert _run(repo.get_messenger_channel("ch-kakao")) is None


def test_create_delivery_rule_fan_out_one_target_two_channels() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    repo.seed_messenger_channel(_channel("ch-a"))
    repo.seed_messenger_channel(_channel("ch-b"))
    svc = _svc(repo)

    rule_a = _run(
        svc.create_delivery_rule(
            entity_id="dr-a", tenant_id=_TENANT, target_id="mt-1", channel_id="ch-a",
            at=_NOW, actor_id=_ACTOR,
        )
    )
    rule_b = _run(
        svc.create_delivery_rule(
            entity_id="dr-b", tenant_id=_TENANT, target_id="mt-1", channel_id="ch-b",
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert rule_a.target_id == rule_b.target_id == "mt-1"
    assert rule_a.channel_id != rule_b.channel_id  # 1:N fan-out
    assert {r.id for r in _run(repo.list_delivery_rules("mt-1"))} == {"dr-a", "dr-b"}


# ══════════════════════════════════════════════════════════════════════════
# (service) update + soft delete 상태값
# ══════════════════════════════════════════════════════════════════════════

def test_update_monitoring_target_records_before_after() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    repo.seed_monitoring_target(_target())
    svc = _svc(repo)

    result = _run(
        svc.update_monitoring_target(
            "mt-1", tenant_id=_TENANT, name="새이름", center_name="새센터",
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert result.target.name == "새이름"
    diff = repo.audits[-1].diff_redacted
    assert diff["from_name"] == "가게" and diff["to_name"] == "새이름"


def test_update_monitoring_target_send_window_can_disable_and_keep_times() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    repo.seed_monitoring_target(
        MonitoringTarget(
            id="mt-1",
            tenant_id=_TENANT,
            platform_account_id="pa-1",
            name="가게",
            center_name="센터",
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
        )
    )
    svc = _svc(repo)

    result = _run(
        svc.update_monitoring_target(
            "mt-1",
            tenant_id=_TENANT,
            schedule_enabled=False,
            start_time=None,
            stop_time=None,
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert result.target.schedule_enabled is False
    assert result.target.start_time == "09:00"
    assert result.target.stop_time == "22:00"
    stored = _run(repo.get_monitoring_target("mt-1"))
    assert stored is not None
    assert stored.schedule_enabled is False
    assert stored.start_time == "09:00"
    assert stored.stop_time == "22:00"


def test_deactivate_monitoring_target_soft_delete_inactive() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    svc = _svc(repo)

    target = _run(svc.deactivate_monitoring_target("mt-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert target.status is MonitoringTargetStatus.INACTIVE
    assert _run(repo.get_monitoring_target("mt-1")).status is MonitoringTargetStatus.INACTIVE
    assert repo.audits[-1].action == "MONITORING_TARGET_DEACTIVATE"


def test_deactivate_monitoring_target_idempotent_no_extra_audit() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target(status=MonitoringTargetStatus.INACTIVE))
    svc = _svc(repo)

    target = _run(svc.deactivate_monitoring_target("mt-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert target.status is MonitoringTargetStatus.INACTIVE
    assert repo.audits == []  # 이미 비활성 → no-op(중복 audit 0)


def test_reactivate_monitoring_target_restores_soft_deleted_target() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target(status=MonitoringTargetStatus.INACTIVE))
    svc = _svc(repo)

    target = _run(
        svc.reactivate_monitoring_target(
            "mt-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR
        )
    )

    assert target.status is MonitoringTargetStatus.ACTIVE
    assert _run(repo.get_monitoring_target("mt-1")).status is MonitoringTargetStatus.ACTIVE
    assert repo.audits[-1].action == "MONITORING_TARGET_REACTIVATE"


def test_deactivate_messenger_channel_soft_delete_inactive() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_channel(state=MessengerChannelState.ACTIVE))
    svc = _svc(repo)

    channel = _run(svc.deactivate_messenger_channel("ch-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert channel.state is MessengerChannelState.INACTIVE
    assert repo.audits[-1].action == "MESSENGER_CHANNEL_DEACTIVATE"


def test_deactivate_delivery_rule_soft_delete_disabled() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    svc = _svc(repo)

    rule = _run(svc.deactivate_delivery_rule("dr-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert rule.enabled is False
    assert _run(repo.get_delivery_rule("dr-1")).enabled is False


# ══════════════════════════════════════════════════════════════════════════
# (service) tenant scope — cross-tenant 차단(not-found 동급)
# ══════════════════════════════════════════════════════════════════════════

def test_cross_tenant_account_update_blocked() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(TenantScopeViolation):
        _run(svc.update_platform_account("pa-1", tenant_id=_TENANT, label="x", at=_NOW, actor_id=_ACTOR))


def test_cross_tenant_target_deactivate_blocked() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(TenantScopeViolation):
        _run(svc.deactivate_monitoring_target("mt-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))


def test_delivery_rule_create_cross_tenant_channel_blocked() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())  # tn-1
    repo.seed_messenger_channel(_channel("ch-x", tenant=_OTHER))  # 다른 tenant 채널
    svc = _svc(repo)

    with pytest.raises(TenantScopeViolation):
        _run(
            svc.create_delivery_rule(
                entity_id="dr-z", tenant_id=_TENANT, target_id="mt-1", channel_id="ch-x",
                at=_NOW, actor_id=_ACTOR,
            )
        )


def test_missing_entity_is_not_found() -> None:
    repo = InMemoryAdminEntityRepository()
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.update_tenant("nope", name="x", at=_NOW, actor_id=_ACTOR))


def test_audit_carries_actor_and_result() -> None:
    repo = InMemoryAdminEntityRepository()
    svc = _svc(repo)

    _run(svc.create_tenant(entity_id="t1", name="고객", at=_NOW, actor_id=_ACTOR, source="ADMIN_UI"))

    audit = repo.audits[-1]
    assert audit.actor_id == _ACTOR
    assert audit.result == "SUCCESS"
    assert audit.created_at == _NOW
    assert audit.target_id == "t1"


# ══════════════════════════════════════════════════════════════════════════
# (라우트) TestClient — POST/GET·권한·tenant·평문 secret·위험 경고
# ══════════════════════════════════════════════════════════════════════════

def _app_with(repo: InMemoryAdminEntityRepository, *, principal=_OPERATOR):
    app = create_app(_FAKE_SETTINGS, admin_entity_service=AdminEntityService(repo))
    app.state.resolve_admin_principal = lambda request: principal
    return app


def _seeded_repo() -> InMemoryAdminEntityRepository:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    return repo


def test_route_create_target_returns_fragment_and_persists() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={"platform_account_id": "pa-1", "name": "신규대상", "center_name": "센터"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "text/html" in resp.headers["content-type"]
    assert "모니터링 대상 생성됨" in resp.text
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert len(_run(repo.list_monitoring_targets("tn-1"))) == 1


def test_route_create_target_persists_send_window() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={
            "platform_account_id": "pa-1",
            "name": "신규대상",
            "center_name": "센터",
            "schedule_enabled": "true",
            "start_time": "09:00",
            "stop_time": "22:00",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    target = _run(repo.list_monitoring_targets("tn-1"))[0]
    assert target.schedule_enabled is True
    assert target.start_time == "09:00"
    assert target.stop_time == "22:00"


def test_route_update_target_disables_send_window_and_keeps_blank_times() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(
        MonitoringTarget(
            id="mt-1",
            tenant_id=_TENANT,
            platform_account_id="pa-1",
            name="가게",
            center_name="센터",
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets/mt-1?tenant=tn-1",
        data={"schedule_enabled": "false", "start_time": "", "stop_time": ""},
    )

    assert resp.status_code == HTTPStatus.OK
    stored = _run(repo.get_monitoring_target("mt-1"))
    assert stored is not None
    assert stored.schedule_enabled is False
    assert stored.start_time == "09:00"
    assert stored.stop_time == "22:00"


@pytest.mark.parametrize(
    "data",
    [
        {"schedule_enabled": "true", "start_time": "25:00", "stop_time": "22:00"},
        {"schedule_enabled": "true", "start_time": "", "stop_time": "22:00"},
        {"schedule_enabled": "true", "start_time": "09:00", "stop_time": "09:00"},
    ],
)
def test_route_create_target_rejects_invalid_send_window(data: dict[str, str]) -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={
            "platform_account_id": "pa-1",
            "name": "신규대상",
            "center_name": "센터",
            **data,
        },
    )

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.list_monitoring_targets("tn-1")) == []


def test_route_create_coupang_blank_center_rejected() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={"platform_account_id": "pa-1", "name": "대상", "center_name": ""},
    )

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.list_monitoring_targets("tn-1")) == []
    assert repo.audits == []


def test_route_update_coupang_blank_center_rejected() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    repo.seed_monitoring_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets/mt-1?tenant=tn-1",
        data={"center_name": ""},
    )

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.get_monitoring_target("mt-1")).center_name == "센터"
    assert repo.audits == []


def test_route_viewer_cannot_create() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={"platform_account_id": "pa-1", "name": "x", "center_name": "c"},
    )

    assert resp.status_code == HTTPStatus.FORBIDDEN


def test_route_unauthenticated_create_401() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=None))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={"platform_account_id": "pa-1", "name": "x", "center_name": "c"},
    )

    assert resp.status_code == HTTPStatus.UNAUTHORIZED


def test_route_cross_tenant_update_404() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/monitoring-targets/mt-1?tenant=tn-1", data={"name": "x"})

    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_entity_admin_form_guides_plaintext_credential_storage() -> None:
    # 옵션 B: 폼은 실제 값 입력을 안내하고, DB 저장 민감성을 경고한다.
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )
    account_form = template[
        template.index('<fieldset id="entity-account-form">') :
        template.index('<div hx-get="/admin/platform-accounts?tenant=')
    ]

    assert "실제 값 그대로" in account_form
    assert "DB" in account_form  # DB 저장 민감성 안내
    assert "로그인 비밀번호" in account_form
    assert "이메일 앱 비밀번호" in account_form
    assert 'name="password"' in account_form
    assert 'name="verification_email_app_password"' in account_form
    # 핸들도 여전히 허용된다는 안내가 남아 있다(에이전트 로컬 resolve 경로).
    assert "vault://" in account_form


def test_entity_admin_channel_form_toggles_fields_by_messenger() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )
    channel_form = template[
        template.index('<fieldset id="entity-channel-form">') :
        template.index('<div class="edit-row">', template.index('<fieldset id="entity-channel-form">'))
    ]

    assert 'id="channel-messenger"' in channel_form
    assert 'onchange="syncChannelCreateFields()"' in channel_form
    assert 'data-channel-messenger="TELEGRAM"' in channel_form
    assert 'data-channel-messenger="KAKAO"' in channel_form
    assert "function syncChannelCreateFields()" in template
    assert "field.disabled = field.dataset.channelMessenger !== messenger;" in template
    assert "document.addEventListener('DOMContentLoaded', syncChannelCreateFields);" in template


def test_route_platform_account_plaintext_password_stored() -> None:
    # 옵션 B: 라우트로 들어온 평문 자격증명도 정상 저장된다.
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=tn-1",
        data={
            "platform": "BAEMIN",
            "label": "x",
            "username": "myuser",
            "password": "mypass",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    accounts = _run(repo.list_platform_accounts("tn-1"))
    assert len(accounts) == 1
    assert accounts[0].password == "mypass"


def test_route_deactivate_channel_soft_delete() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_channel(state=MessengerChannelState.ACTIVE))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/messenger-channels/ch-1/deactivate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_messenger_channel("ch-1")).state is MessengerChannelState.INACTIVE


def test_route_get_entities_form_viewer_ok() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/entities?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "플랫폼 계정" in resp.text
    assert "admin-entity-changed from:body" in resp.text
    assert 'hx-trigger="load, admin-entity-changed from:body, every 30s"' in resp.text
    assert "메시지 템플릿 id" not in resp.text


def test_entities_form_shows_nearby_create_ctas_for_empty_dependencies() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/entities?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert 'href="#entity-account-form"' in resp.text
    assert 'href="#entity-channel-form"' in resp.text


def test_entities_form_without_tenant_renders_tenant_switcher() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_tenant(_tenant(_OTHER))
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/entities")

    assert resp.status_code == HTTPStatus.OK
    assert 'id="entity-tenant-switch"' in resp.text
    assert 'value="tn-1"' in resp.text
    assert 'value="tn-2"' in resp.text
    assert "switchTenant" in resp.text


def test_entities_tenant_switcher_keeps_manage_mode() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_tenant(_tenant(_OTHER))
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities").text

    assert 'id="entity-tenant-switch" onchange="switchTenant(this.value, \'manage\')"' in body
    assert 'url.searchParams.set("mode", mode);' in body
    assert 'url.hash = mode;' in body


def test_entities_form_without_any_tenant_guides_customer_first() -> None:
    repo = InMemoryAdminEntityRepository()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/entities")
    account_form = resp.text[
        resp.text.index('<fieldset id="entity-account-form">') :
        resp.text.index('<div hx-get="/admin/platform-accounts?tenant=')
    ]

    assert resp.status_code == HTTPStatus.OK
    assert "먼저 고객을 생성하거나 선택하세요" in account_form
    assert "계정 생성 불가" in account_form
    assert account_form.index("계정 생성 불가") < account_form.index(
        "먼저 고객을 생성하거나 선택하세요"
    )


def test_entities_form_exposes_full_edit_and_delivery_rule_controls() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    for marker in (
        'id="tgt-edit-name"',
        'id="tgt-edit-external"',
        'id="tgt-edit-url"',
        'id="tgt-edit-interval"',
        'name="schedule_enabled"',
        'name="start_time"',
        'name="stop_time"',
        'id="tgt-edit-schedule"',
        'id="tgt-edit-start"',
        'id="tgt-edit-stop"',
        'id="ch-edit-thread"',
        'id="ch-edit-kakao"',
        'id="rule-list-target"',
        'id="rule-edit-id"',
        'id="delivery-rule-list"',
        "loadDeliveryRules",
    ):
        assert marker in body
    assert "thread_id: cval('ch-edit-thread')" in body
    assert "kakao_room_name: cval('ch-edit-kakao')" in body
    assert "interval_minutes: cval('tgt-edit-interval')" in body
    assert "schedule_enabled: cval('tgt-edit-schedule')" in body
    assert "start_time: cval('tgt-edit-start')" in body
    assert "stop_time: cval('tgt-edit-stop')" in body


def test_route_list_targets_summary_includes_active_send_window() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(
        MonitoringTarget(
            id="mt-1",
            tenant_id=_TENANT,
            platform_account_id="pa-1",
            name="가게",
            center_name="센터",
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
        )
    )
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/monitoring-targets?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "가게 · 전송 09:00~22:00" in resp.text


def test_entities_form_does_not_expose_raw_template_id_editor() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    assert 'id="rule-edit-template"' not in body
    assert 'type="text" id="rule-edit-template"' not in body
    assert "template_id: cval(" not in body


def test_entities_form_filters_blank_edit_fields_before_update() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    assert "function filledValues" in body
    assert "crudButton(this, '/admin/monitoring-targets/', 'tgt-edit-id', '', filledValues({" in body
    assert "crudButton(this, '/admin/messenger-channels/', 'ch-edit-id', '', filledValues({" in body


def test_entity_admin_has_single_customer_create_form() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert template.count('hx-post="/admin/customers?tenant=') == 1


def test_entity_admin_required_selects_and_inline_error_handler_contract() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    for name in ("platform_account_id", "target_id", "channel_id", "tenant_id"):
        name_pos = template.index(f'name="{name}"')
        select_pos = template.rfind("<select", 0, name_pos)
        select_tag = template[select_pos : template.index(">", name_pos)]
        assert "required" in select_tag

    assert "function syncEntityFormButtons()" in template
    assert "data-requires=" in template
    assert 'document.body.addEventListener("htmx:responseError"' in template
    assert "inline-action-status" in template


def test_entity_admin_inline_error_handler_reads_error_envelope() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    assert "friendlyErrorMessage" in template
    assert "data.error" in template
    assert "message_redacted" in template
    assert "source not allowed" in template
    assert "허용된 관리자 접속 위치가 아닙니다" in template


def test_action_result_fragment_does_not_create_nested_live_region() -> None:
    template = Path("src/rider_server/admin/templates/_action_result.html").read_text(
        encoding="utf-8"
    )

    assert "role=" not in template
    assert "aria-live" not in template
    assert "inline-action-status" not in template


def test_entities_form_guides_target_edit_fields_keep_existing_values() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    assert "바꿀 칸만 입력하세요. 비운 칸은 기존 값을 유지합니다." in body
    assert body.count('<span class="field-help">비우면 유지</span>') == 0
    assert "입력하지 않으면 현재" not in body
    for marker in (
        'placeholder="비우면 유지"',
        'id="tgt-edit-name"',
        'id="tgt-edit-center"',
        'id="tgt-edit-external"',
        'id="tgt-edit-url"',
        'id="tgt-edit-interval"',
    ):
        assert marker in body


def test_entities_form_exposes_customer_delete_button() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    assert "고객을 완전히 삭제" in body
    assert "crudButton(this, '/admin/customers/', 'cust-edit-id', '/delete'" in body


def test_entities_form_exposes_subscription_controls_and_customer_status() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    assert "구독 관리" in body
    assert 'hx-post="/admin/subscriptions?tenant={{ tenant_id | urlencode }}"' not in body
    assert 'hx-post="/admin/subscriptions?tenant=' in body
    assert 'hx-get="/admin/subscriptions/options?tenant=' in body
    assert "PAYMENT_ACTIVE" in body
    assert "cust-edit-status" in body
    assert "status: cval('cust-edit-status')" in body


def test_entities_list_hides_raw_ids_by_default() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(_target())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/monitoring-targets?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "<th>id</th>" not in resp.text
    assert "<code>mt-1</code>" not in resp.text
    assert "가게" in resp.text


def test_delivery_rule_list_hides_internal_target_and_channel_ids() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(_target())
    repo.seed_messenger_channel(_channel())
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/delivery-rules?tenant=tn-1&target_id=mt-1")

    assert resp.status_code == HTTPStatus.OK
    assert "전송 규칙" in resp.text
    assert "mt-1" not in resp.text
    assert "ch-1" not in resp.text
    assert "dr-1" not in resp.text


def test_route_success_fragments_hide_created_internal_ids(monkeypatch) -> None:
    from rider_server.admin import crud_routes

    ids = iter(("mt-created", "dr-created"))
    monkeypatch.setattr(crud_routes, "_new_id", lambda: next(ids))
    repo = _seeded_repo()
    repo.seed_monitoring_target(_target())
    repo.seed_messenger_channel(_channel())
    client = TestClient(_app_with(repo))

    target_resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={
            "platform_account_id": "pa-1",
            "name": "새 가게",
            "center_name": "센터",
            "interval_minutes": "5",
        },
    )
    rule_resp = client.post(
        "/admin/delivery-rules?tenant=tn-1",
        data={"target_id": "mt-1", "channel_id": "ch-1"},
    )

    assert target_resp.status_code == HTTPStatus.OK
    assert "mt-created" not in target_resp.text
    assert rule_resp.status_code == HTTPStatus.OK
    assert "dr-created" not in rule_resp.text
    assert "mt-1" not in rule_resp.text
    assert "ch-1" not in rule_resp.text


class _DuplicateChannelRepo(InMemoryAdminEntityRepository):
    async def create_messenger_channel(self, channel, audit, *, registration_code=None):
        raise AdminEntityDuplicateError("registration_code", "이미 등록된 채널 등록 코드입니다")


def test_route_duplicate_messenger_channel_returns_conflict_message() -> None:
    repo = _DuplicateChannelRepo()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/messenger-channels?tenant=tn-1",
        data={"messenger": "TELEGRAM", "registration_code": "JOIN-CODE"},
    )

    assert resp.status_code == HTTPStatus.CONFLICT
    assert "중복" in resp.text


def test_route_list_targets_fragment() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(_target())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/monitoring-targets?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "가게" in resp.text


def test_route_dashboard_includes_entity_section() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo))

    resp = client.get("/admin?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "엔티티 관리" in resp.text
    assert "/reactivate" in resp.text


# ══════════════════════════════════════════════════════════════════════════
# ░░ QA gap-fill (bmad-qa-generate-e2e-tests, 2026-06-14) ░░
# dev 가 커버하지 않은 update 경로·비활성 경계·라우트 계약을 추가로 잠근다.
# (memory/stale-test-count-a2 — qa-e2e 가 dev 노트 이후 케이스를 append)
# ══════════════════════════════════════════════════════════════════════════

# ── (service) update happy path — dev 는 monitoring_target update 만 커버 ──────────────

def test_update_tenant_records_before_after() -> None:
    """G1 — ``update_tenant`` happy path(dev 미커버: missing-entity 만 호출). 상태/이름 before/after."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())  # name="고객", status=ACTIVE
    svc = _svc(repo)

    updated = _run(
        svc.update_tenant(
            _TENANT, name="새고객", status=CustomerLifecycleState.SUSPENDED,
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert updated.name == "새고객"
    assert updated.status is CustomerLifecycleState.SUSPENDED
    assert _run(repo.get_tenant(_TENANT)).status is CustomerLifecycleState.SUSPENDED
    diff = repo.audits[-1].diff_redacted
    assert repo.audits[-1].action == "TENANT_UPDATE"
    assert diff["from_name"] == "고객" and diff["to_name"] == "새고객"
    assert diff["from_status"] == "ACTIVE" and diff["to_status"] == "SUSPENDED"


def test_update_tenant_rejects_sending_enabled_true_without_readiness_evidence() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.update_tenant(
                _TENANT,
                sending_enabled=True,
                at=_NOW,
                actor_id=_ACTOR,
            )
        )

    assert _run(repo.get_tenant(_TENANT)).sending_enabled is False
    assert repo.audits == []


def test_update_tenant_allows_idempotent_sending_enabled_true_on_already_live() -> None:
    # 게이트는 OFF→ON 전이만 막는다. 이미 ON 인 고객의 다른 필드 편집 시 폼이 현재 값
    # sending_enabled=True 를 그대로 재전송해도 거부하지 않는다(no-op re-assert 허용).
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
            sending_enabled=True,
        )
    )
    svc = _svc(repo)

    updated = _run(
        svc.update_tenant(
            _TENANT,
            name="새이름",
            sending_enabled=True,
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert updated.name == "새이름"
    assert updated.sending_enabled is True
    assert _run(repo.get_tenant(_TENANT)).sending_enabled is True


def test_update_tenant_allows_sending_on_after_send_test_passed() -> None:
    # 0023: 전송 테스트 통과(send_test_passed_at 존재)면 OFF→ON 전이를 허용한다(게이트 해제 조건).
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    # 채널 전송 테스트 성공이 스탬프하는 것과 동일한 경로(send_test_passed_at 설정).
    _run(
        svc.update_tenant(
            _TENANT, send_test_passed_at=_NOW, at=_NOW, actor_id=_ACTOR
        )
    )
    updated = _run(
        svc.update_tenant(_TENANT, sending_enabled=True, at=_NOW, actor_id=_ACTOR)
    )

    assert updated.sending_enabled is True
    assert _run(repo.get_tenant(_TENANT)).send_test_passed_at == _NOW
    diff = repo.audits[-1].diff_redacted
    assert diff["from_sending_enabled"] is False and diff["to_sending_enabled"] is True


def test_update_tenant_clearing_send_test_passed_reblocks_gate() -> None:
    # send_test_passed_at 을 None 으로 지우면(미통과로 되돌림) 다시 OFF→ON 이 막힌다.
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
            send_test_passed_at=_NOW,
        )
    )
    svc = _svc(repo)

    _run(svc.update_tenant(_TENANT, send_test_passed_at=None, at=_NOW, actor_id=_ACTOR))
    with pytest.raises(ValueError):
        _run(svc.update_tenant(_TENANT, sending_enabled=True, at=_NOW, actor_id=_ACTOR))


def test_delete_tenant_removes_empty_customer_and_audits() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    deleted = _run(
        svc.delete_tenant(_TENANT, at=_NOW, actor_id=_ACTOR, reason="테스트 삭제")
    )

    assert deleted.id == _TENANT
    assert _run(repo.get_tenant(_TENANT)) is None
    assert repo.audits[-1].action == "TENANT_DELETE"
    assert repo.audits[-1].target_id == _TENANT
    assert repo.audits[-1].diff_redacted["op"] == "delete"


@pytest.mark.parametrize(
    "seed_dependency",
    [
        lambda repo: repo.seed_platform_account(_account()),
        lambda repo: repo.seed_monitoring_target(_target()),
        lambda repo: repo.seed_messenger_channel(_channel()),
    ],
)
def test_delete_tenant_with_dependencies_is_blocked_without_audit(seed_dependency) -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    seed_dependency(repo)
    svc = _svc(repo)

    with pytest.raises(AdminEntityDeleteBlockedError):
        _run(svc.delete_tenant(_TENANT, at=_NOW, actor_id=_ACTOR))

    assert _run(repo.get_tenant(_TENANT)) is not None
    assert repo.audits == []


def test_update_platform_account_label_keeps_creds() -> None:
    """G2 — ``update_platform_account`` happy path. 라벨만 바꾸고 자격증명은 보존."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(
        _account(
            platform=Platform.COUPANG,
        )
    )
    svc = _svc(repo)

    updated = _run(
        svc.update_platform_account(
            "pa-1", tenant_id=_TENANT, label="새라벨", at=_NOW, actor_id=_ACTOR
        )
    )

    assert updated.label == "새라벨"
    assert updated.username == _REF
    assert updated.verification_email_app_password == ""
    assert repo.audits[-1].action == "PLATFORM_ACCOUNT_UPDATE"
    diff = repo.audits[-1].diff_redacted
    assert diff["from_label"] == "계정" and diff["to_label"] == "새라벨"


def test_update_platform_account_verification_email() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    svc = _svc(repo)

    updated = _run(
        svc.update_platform_account(
            "pa-1",
            tenant_id=_TENANT,
            verification_email_app_password=_EMAIL_APP_PASSWORD_REF,
            verification_email_subject_keyword="보안코드",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert updated.verification_email_app_password == _EMAIL_APP_PASSWORD_REF
    assert updated.verification_email_subject_keyword == "보안코드"
    diff = repo.audits[-1].diff_redacted
    assert "verification_email_app_password" not in diff


def test_update_platform_account_plaintext_password_stored() -> None:
    """G3(옵션 B) — 평문 자격증명을 update 로 주면 그대로 저장한다."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account())
    svc = _svc(repo)

    _run(
        svc.update_platform_account(
            "pa-1", tenant_id=_TENANT,
            password="my-new-password",
            verification_email_app_password="my-new-app-password",
            at=_NOW, actor_id=_ACTOR,
        )
    )

    stored = _run(repo.get_platform_account("pa-1"))
    assert stored.password == "my-new-password"
    assert stored.verification_email_app_password == "my-new-app-password"


def test_update_messenger_channel_routing_fields_no_transition() -> None:
    """G4 — ``update_messenger_channel`` (dev 전혀 미커버). 라우팅 식별자만 바꾸고 상태는 불변."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(_channel(state=MessengerChannelState.ACTIVE))
    svc = _svc(repo)

    updated = _run(
        svc.update_messenger_channel(
            "ch-1", tenant_id=_TENANT, telegram_chat_id="-100888", thread_id="9",
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert updated.telegram_chat_id == "-100888" and updated.thread_id == "9"
    assert updated.state is MessengerChannelState.ACTIVE  # 라우팅 편집은 전이 아님
    assert repo.audits[-1].action == "MESSENGER_CHANNEL_UPDATE"


def test_update_pending_kakao_messenger_channel_with_room_activates() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-kakao",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name=None,
            state=MessengerChannelState.PENDING,
        )
    )
    svc = _svc(repo)

    updated = _run(
        svc.update_messenger_channel(
            "ch-kakao",
            tenant_id=_TENANT,
            kakao_room_name="실적공유방",
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert updated.state is MessengerChannelState.ACTIVE
    assert _run(repo.get_messenger_channel("ch-kakao")).state is MessengerChannelState.ACTIVE
    assert repo.audits[-1].diff_redacted["to_state"] == "ACTIVE"


def test_update_pending_kakao_messenger_channel_rejects_duplicate_active_room() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-existing",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name="실적공유방",
            state=MessengerChannelState.ACTIVE,
        )
    )
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-kakao",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name=None,
            state=MessengerChannelState.PENDING,
        )
    )
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.update_messenger_channel(
                "ch-kakao",
                tenant_id=_TENANT,
                kakao_room_name="실적공유방",
                at=_NOW,
                actor_id=_ACTOR,
            )
        )

    assert _run(repo.get_messenger_channel("ch-kakao")).state is MessengerChannelState.PENDING


def test_update_delivery_rule_options_before_after() -> None:
    """G5 — ``update_delivery_rule`` (dev 전혀 미커버). 템플릿/변경시에만전송 옵션 편집."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    svc = _svc(repo)

    updated = _run(
        svc.update_delivery_rule(
            "dr-1", tenant_id=_TENANT, template_id="tpl-7", send_only_on_change=True,
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert updated.template_id == "tpl-7" and updated.send_only_on_change is True
    assert updated.enabled is True  # 편집은 비활성과 무관
    diff = repo.audits[-1].diff_redacted
    assert repo.audits[-1].action == "DELIVERY_RULE_UPDATE"
    assert diff["to_template_id"] == "tpl-7" and diff["to_send_only_on_change"] is True


# ── (service) 비활성 경계 — 멱등성·전이 비대칭 ───────────────────────────────────────

def test_deactivate_delivery_rule_idempotent_no_extra_audit() -> None:
    """G6 — 이미 ``enabled=False`` 인 규칙 재-비활성 → 멱등 no-op(중복 audit 0). target 와 동형."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(
        DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1", enabled=False)
    )
    svc = _svc(repo)

    rule = _run(svc.deactivate_delivery_rule("dr-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert rule.enabled is False
    assert repo.audits == []  # 이미 비활성 → no-op


def test_deactivate_messenger_channel_already_inactive_is_rejected() -> None:
    """G7 — 채널 비활성은 **멱등이 아니다**(전이표 ``INACTIVE→{PENDING}`` 만 — target/rule 과 비대칭).

    이미 INACTIVE 인 채널을 다시 비활성하면 ``InvalidChannelTransition``(=ValueError→400)으로
    거부된다(5.5 전이 허용표 재사용). 회귀로 "채널도 멱등일 것" 가정을 차단한다.
    """
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(_channel(state=MessengerChannelState.INACTIVE))
    svc = _svc(repo)

    with pytest.raises(InvalidChannelTransition):
        _run(svc.deactivate_messenger_channel("ch-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))
    assert repo.audits == []  # 미정의 전이 → audit 0


def test_deactivate_messenger_channel_from_pending_allowed() -> None:
    """G8 — 갓 생성한 PENDING 채널은 비활성 가능(``PENDING→INACTIVE`` 허용 전이)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(_channel(state=MessengerChannelState.PENDING))
    svc = _svc(repo)

    channel = _run(svc.deactivate_messenger_channel("ch-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))

    assert channel.state is MessengerChannelState.INACTIVE
    assert repo.audits[-1].action == "MESSENGER_CHANNEL_DEACTIVATE"


# ── (service) 검증·scope 경계 ────────────────────────────────────────────────────────

def test_create_tenant_blank_name_rejected() -> None:
    """G9 — 고객명 공백은 ``ValueError``(미반영). dev 는 happy path 만 커버."""
    repo = InMemoryAdminEntityRepository()
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(svc.create_tenant(entity_id="t-x", name="   ", at=_NOW, actor_id=_ACTOR))
    assert _run(repo.get_tenant("t-x")) is None


def test_create_monitoring_target_blank_name_rejected() -> None:
    """G10 — 대상 표시명 공백은 ``ValueError``(미반영)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    svc = _svc(repo)

    with pytest.raises(ValueError):
        _run(
            svc.create_monitoring_target(
                entity_id="mt-x", tenant_id=_TENANT, platform_account_id="pa-1",
                name="", center_name="센터", at=_NOW, actor_id=_ACTOR,
            )
        )
    assert _run(repo.get_monitoring_target("mt-x")) is None


def test_deactivate_delivery_rule_cross_tenant_blocked() -> None:
    """G11 — DeliveryRule scope 는 ``target_id``→target.tenant 로 도출되므로 cross-tenant 차단."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))  # 대상은 tn-2 소유
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    svc = _svc(repo)

    with pytest.raises(TenantScopeViolation):
        _run(svc.deactivate_delivery_rule("dr-1", tenant_id=_TENANT, at=_NOW, actor_id=_ACTOR))


# ── (라우트) create/update/deactivate happy + 검증 400 — dev 미커버 리소스 ───────────────

def test_route_create_customer_redirects_to_new_customer_manage_context(monkeypatch) -> None:
    """G12 — ``POST /admin/customers`` switches the UI to the newly created tenant."""
    from rider_server.admin import crud_routes

    monkeypatch.setattr(crud_routes, "_new_id", lambda: "tn-new")
    repo = InMemoryAdminEntityRepository()
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/customers", data={"name": "신규고객"})

    assert resp.status_code == HTTPStatus.OK
    assert "생성 완료" in resp.text
    assert resp.headers["HX-Redirect"] == "/admin?tenant=tn-new&mode=manage#manage"
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert len(_run(repo.list_tenants())) == 1
    assert _run(repo.get_tenant("tn-new")).name == "신규고객"


def test_route_create_subscription_returns_fragment_and_persists() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/subscriptions?tenant=tn-1",
        data={"tenant_id": _TENANT, "plan": "basic", "status": "PAYMENT_ACTIVE"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "구독 생성됨" in resp.text
    subscriptions = _run(repo.list_subscriptions(_TENANT))
    assert len(subscriptions) == 1
    assert subscriptions[0].status is SubscriptionStatus.PAYMENT_ACTIVE


def test_route_list_subscriptions_summary_includes_customer_name() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="H&J",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
        )
    )
    repo.seed_subscription(_subscription())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/subscriptions?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "구독 목록 (1건)" in resp.text
    assert "H&amp;J · basic" in resp.text


def test_route_update_subscription_status_returns_fragment() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_subscription(_subscription(status=SubscriptionStatus.SUSPENDED))
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/subscriptions/sub-1?tenant=tn-1",
        data={"status": "PAYMENT_ACTIVE"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "구독 저장됨" in resp.text
    assert _run(repo.get_subscription("sub-1")).status is SubscriptionStatus.PAYMENT_ACTIVE


def test_entity_admin_buttons_have_inline_status_targets() -> None:
    template = Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )

    action_buttons = template.count("<button")
    status_targets = template.count('class="inline-action-status"')

    assert status_targets >= action_buttons
    assert 'hx-target="find .inline-action-status"' in template
    assert "crudButton(this" in template


def test_route_create_customer_viewer_forbidden() -> None:
    """G13 — customers 변경 라우트도 OPERATOR 게이트(리소스별 게이트 회귀 차단)."""
    repo = InMemoryAdminEntityRepository()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.post("/admin/customers", data={"name": "x"})

    assert resp.status_code == HTTPStatus.FORBIDDEN


def test_route_update_customer_returns_fragment() -> None:
    """G14 — ``POST /admin/customers/{id}`` 편집 happy."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(f"/admin/customers/{_TENANT}", data={"name": "새이름"})

    assert resp.status_code == HTTPStatus.OK
    assert "고객 편집됨" in resp.text
    assert _run(repo.get_tenant(_TENANT)).name == "새이름"


def test_route_update_customer_rejects_sending_enabled_true() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        f"/admin/customers/{_TENANT}",
        data={"sending_enabled": "true"},
    )

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.get_tenant(_TENANT)).sending_enabled is False
    assert repo.audits == []


def test_route_update_customer_allows_sending_enabled_false() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(
        Tenant(
            id=_TENANT,
            name="고객",
            status=CustomerLifecycleState.ACTIVE,
            created_at=_NOW,
            sending_enabled=True,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.post(
        f"/admin/customers/{_TENANT}",
        data={"sending_enabled": "false"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_tenant(_TENANT)).sending_enabled is False


def test_route_delete_customer_removes_empty_tenant() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(f"/admin/customers/{_TENANT}/delete")

    assert resp.status_code == HTTPStatus.OK
    assert "고객 삭제됨" in resp.text
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert _run(repo.get_tenant(_TENANT)) is None


def test_route_delete_customer_with_dependencies_returns_conflict() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    client = TestClient(_app_with(repo))

    resp = client.post(f"/admin/customers/{_TENANT}/delete")

    assert resp.status_code == HTTPStatus.CONFLICT
    assert "연결 데이터" in resp.text
    assert _run(repo.get_tenant(_TENANT)) is not None
    assert repo.audits == []


def test_route_delete_customer_viewer_forbidden() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.post(f"/admin/customers/{_TENANT}/delete")

    assert resp.status_code == HTTPStatus.FORBIDDEN
    assert _run(repo.get_tenant(_TENANT)) is not None


def test_route_create_platform_account_happy() -> None:
    """G15 — ``POST /admin/platform-accounts`` happy(dev 는 평문 400 만 커버)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=tn-1",
        data={"platform": "BAEMIN", "label": "배민", "username": "vault://u", "password": "vault://p"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "플랫폼 계정 생성됨" in resp.text
    assert len(_run(repo.list_platform_accounts("tn-1"))) == 1


def test_route_create_platform_account_missing_tenant_returns_fragment_not_500() -> None:
    repo = InMemoryAdminEntityRepository()
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=",
        data={
            "platform": "COUPANG",
            "label": "쿠팡",
            "username": "coupang-user",
            "password": "plain-password",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    assert "먼저 고객" in resp.text
    assert _run(repo.list_platform_accounts("")) == []


def test_route_create_platform_account_unknown_platform_400() -> None:
    """G16 — 알 수 없는 플랫폼 문자열 → 400(``_platform_or_400``)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=tn-1",
        data={"platform": "NOTREAL", "label": "x", "username": "vault://u", "password": "vault://p"},
    )

    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_create_platform_account_plaintext_password_stored() -> None:
    # 옵션 B: 평문 password/이메일 앱비번이 라우트로 들어와도 저장된다.
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=tn-1",
        data={
            "platform": "BAEMIN",
            "label": "배민",
            "username": "coupang-user",
            "password": "plain-password",
            "verification_email_app_password": "mail-app-password",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    accounts = _run(repo.list_platform_accounts("tn-1"))
    assert len(accounts) == 1
    assert accounts[0].password == "plain-password"
    assert accounts[0].verification_email_app_password == "mail-app-password"


def test_route_create_telegram_messenger_channel_pending() -> None:
    """G17 — Telegram ``POST /admin/messenger-channels`` happy → PENDING 사전 생성."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/messenger-channels?tenant=tn-1",
        data={"messenger": "TELEGRAM", "telegram_chat_id": "-100777"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "메시지 채널 생성됨" in resp.text
    channels = _run(repo.list_messenger_channels("tn-1"))
    assert len(channels) == 1 and channels[0].state is MessengerChannelState.PENDING


def test_route_create_kakao_messenger_channel_with_room_active() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/messenger-channels?tenant=tn-1",
        data={"messenger": "KAKAO", "kakao_room_name": "실적공유방"},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "상태: ACTIVE" in resp.text
    channels = _run(repo.list_messenger_channels("tn-1"))
    assert len(channels) == 1
    assert channels[0].state is MessengerChannelState.ACTIVE
    assert channels[0].kakao_room_name == "실적공유방"


def test_route_update_pending_kakao_messenger_channel_with_room_active() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-kakao",
            tenant_id=_TENANT,
            messenger=Messenger.KAKAO,
            kakao_room_name=None,
            state=MessengerChannelState.PENDING,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/messenger-channels/ch-kakao?tenant=tn-1",
        data={"kakao_room_name": "실적공유방"},
    )

    assert resp.status_code == HTTPStatus.OK
    channel = _run(repo.get_messenger_channel("ch-kakao"))
    assert channel.state is MessengerChannelState.ACTIVE
    assert channel.kakao_room_name == "실적공유방"


def test_route_activate_messenger_channel_manual_returns_fragment_and_persists() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_channel("ch-pending", state=MessengerChannelState.PENDING))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/messenger-channels/ch-pending/activate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "메시지 채널 활성화됨" in resp.text
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert _run(repo.get_messenger_channel("ch-pending")).state is MessengerChannelState.ACTIVE
    assert repo.audits[-1].action == "MESSENGER_CHANNEL_ACTIVATE"


def test_route_activate_messenger_channel_rejects_missing_route() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(
        MessengerChannel(
            id="ch-pending",
            tenant_id=_TENANT,
            messenger=Messenger.TELEGRAM,
            telegram_chat_id=None,
            state=MessengerChannelState.PENDING,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/messenger-channels/ch-pending/activate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.get_messenger_channel("ch-pending")).state is MessengerChannelState.PENDING
    assert repo.audits == []


def test_route_activate_messenger_channel_rejects_duplicate_active_topic() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_channel("ch-active", state=MessengerChannelState.ACTIVE))
    repo.seed_messenger_channel(_channel("ch-pending", state=MessengerChannelState.PENDING))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/messenger-channels/ch-pending/activate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.BAD_REQUEST
    assert _run(repo.get_messenger_channel("ch-pending")).state is MessengerChannelState.PENDING
    assert repo.audits == []


def test_route_create_messenger_channel_unknown_messenger_400() -> None:
    """G18 — 알 수 없는 메신저 문자열 → 400(``_messenger_or_400``)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/messenger-channels?tenant=tn-1", data={"messenger": "NOTREAL"})

    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_create_delivery_rule_fan_out_persists() -> None:
    """G19 — ``POST /admin/delivery-rules`` 로 1:N fan-out(한 대상 → 2채널)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    repo.seed_messenger_channel(_channel("ch-a"))
    repo.seed_messenger_channel(_channel("ch-b"))
    client = TestClient(_app_with(repo))

    r1 = client.post("/admin/delivery-rules?tenant=tn-1", data={"target_id": "mt-1", "channel_id": "ch-a"})
    r2 = client.post("/admin/delivery-rules?tenant=tn-1", data={"target_id": "mt-1", "channel_id": "ch-b"})

    assert r1.status_code == HTTPStatus.OK and r2.status_code == HTTPStatus.OK
    assert "전송 규칙 생성됨" in r1.text
    assert r1.headers["HX-Trigger"] == "admin-entity-changed"
    assert len(_run(repo.list_delivery_rules("mt-1"))) == 2


def test_route_create_delivery_rule_missing_fields_400() -> None:
    """G20 — target_id/channel_id 누락 → 400(라우트 선검증)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/delivery-rules?tenant=tn-1", data={"target_id": "mt-1"})

    assert resp.status_code == HTTPStatus.BAD_REQUEST


def test_route_deactivate_target_soft_delete() -> None:
    """G21 — ``POST /admin/monitoring-targets/{id}/deactivate`` → INACTIVE 영속."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/monitoring-targets/mt-1/deactivate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert _run(repo.get_monitoring_target("mt-1")).status is MonitoringTargetStatus.INACTIVE


def test_route_reactivate_target_restores_soft_delete() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target(status=MonitoringTargetStatus.INACTIVE))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/monitoring-targets/mt-1/reactivate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "복구" in resp.text
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert _run(repo.get_monitoring_target("mt-1")).status is MonitoringTargetStatus.ACTIVE


def test_route_deactivate_delivery_rule_disabled() -> None:
    """G22 — ``POST /admin/delivery-rules/{id}/deactivate`` → enabled=False 영속."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/delivery-rules/dr-1/deactivate?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.headers["HX-Trigger"] == "admin-entity-changed"
    assert _run(repo.get_delivery_rule("dr-1")).enabled is False


# ── (라우트) 목록 fragment — dev 는 monitoring-targets 목록만 커버 ──────────────────────

def test_route_list_customers_fragment() -> None:
    """G23 — ``GET /admin/customers`` 목록 fragment(VIEWER 가능)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/customers?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "고객" in resp.text


def test_route_list_platform_accounts_fragment() -> None:
    """G24 — ``GET /admin/platform-accounts`` 목록 fragment."""
    repo = _seeded_repo()  # tenant + account(BAEMIN · 계정)
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/platform-accounts?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "BAEMIN" in resp.text


def test_route_list_messenger_channels_fragment() -> None:
    """G25 — ``GET /admin/messenger-channels`` 목록 fragment."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_messenger_channel(_channel())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/messenger-channels?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "TELEGRAM" in resp.text


def test_route_list_delivery_rules_fragment_by_target() -> None:
    """G26 — ``GET /admin/delivery-rules?target_id=`` 목록 fragment(target 기준 조회)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/delivery-rules?tenant=tn-1&target_id=mt-1")

    assert resp.status_code == HTTPStatus.OK
    assert "전송 규칙 · 항상" in resp.text
    assert "mt-1" not in resp.text
    assert "ch-1" not in resp.text


# ── (review fix) delivery-rules 조회 tenant 격리 — AC3 조회 경로 누설 차단 ─────────────


def test_list_delivery_rules_cross_tenant_returns_empty() -> None:
    """REV1 (AC3) — DeliveryRule 조회도 tenant scope 강제: 다른 tenant 대상의 규칙은 미노출.

    write 경로(update/deactivate)는 ``_scoped_rule`` 로 scope 를 강제하지만, 목록 조회는
    ``target_id`` 만으로 필터하면 cross-tenant 규칙이 노출된다. service 가 target→tenant 로
    scope 를 도출해 불일치면 빈 목록을 반환해야 한다(404 동급 — 존재 누설 방지).
    """
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))  # 대상은 tn-2 소유
    repo.seed_delivery_rule(DeliveryRule(id="dr-1", target_id="mt-1", channel_id="ch-1"))
    svc = _svc(repo)

    # tn-1 요청자가 tn-2 대상의 규칙을 조회 → 빈 목록(누설 0).
    assert _run(svc.list_delivery_rules("mt-1", tenant_id=_TENANT)) == []
    # 소유 tenant(tn-2)로는 정상 조회.
    owned = _run(svc.list_delivery_rules("mt-1", tenant_id=_OTHER))
    assert {r.id for r in owned} == {"dr-1"}


def test_route_list_delivery_rules_cross_tenant_not_exposed() -> None:
    """REV2 (AC3) — 라우트 목록 fragment 도 cross-tenant 규칙을 노출하지 않는다(항목 없음)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))  # tn-2 소유 대상
    repo.seed_delivery_rule(DeliveryRule(id="dr-secret", target_id="mt-1", channel_id="ch-1"))
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/delivery-rules?tenant=tn-1&target_id=mt-1")

    assert resp.status_code == HTTPStatus.OK
    assert "dr-secret" not in resp.text  # 다른 tenant 규칙 id 미노출
    assert "항목이 없습니다" in resp.text


# ══════════════════════════════════════════════════════════════════════════
# 2026-06-29 — 관리 탭 현재값 로드(edit-state): scoped read service + operator-only JSON
# + 템플릿 hook + 비밀값 미노출 회귀. 비밀값은 설정됨/미설정 라벨만, 일반값은 그대로.
# ══════════════════════════════════════════════════════════════════════════

# ── (service) edit-state scoped read — tenant scope 강제 ──────────────────────────────

def test_get_monitoring_target_for_edit_is_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_monitoring_target_for_edit("mt-1", tenant_id=_TENANT))


def test_get_platform_account_for_edit_is_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_platform_account_for_edit("pa-1", tenant_id=_TENANT))


def test_get_subscription_for_edit_is_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_subscription(_subscription(tenant=_OTHER))
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_subscription_for_edit("sub-1", tenant_id=_TENANT))


def test_get_delivery_rule_for_edit_is_tenant_scoped_through_target() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target("mt-other", tenant=_OTHER))
    repo.seed_delivery_rule(
        DeliveryRule(id="rule-1", target_id="mt-other", channel_id="ch-1")
    )
    svc = _svc(repo)

    with pytest.raises(AdminActionNotFound):
        _run(svc.get_delivery_rule_for_edit("rule-1", tenant_id=_TENANT))


def test_get_monitoring_target_for_edit_happy_path() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target())
    svc = _svc(repo)

    target = _run(svc.get_monitoring_target_for_edit("mt-1", tenant_id=_TENANT))

    assert target.id == "mt-1"
    assert target.name == "가게"


# ── (route) edit-state JSON — operator-only, tenant scope, 비밀값 미노출 ────────────────

def test_monitoring_target_edit_state_returns_safe_current_values() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account())
    repo.seed_monitoring_target(
        MonitoringTarget(
            id="mt-1",
            tenant_id="tn-1",
            platform_account_id="pa-1",
            name="H&J",
            center_name="제이앤에이치플러스 의정부남부",
            external_id="store-77",
            url="https://example.test/dashboard",
            interval_minutes=2,
            schedule_enabled=True,
            start_time="09:00",
            stop_time="22:00",
            status=MonitoringTargetStatus.ACTIVE,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {
        "id": "mt-1",
        "name": "H&J",
        "center_name": "제이앤에이치플러스 의정부남부",
        "external_id": "store-77",
        "url": "https://example.test/dashboard",
        "interval_minutes": 2,
        "schedule_enabled": True,
        "start_time": "09:00",
        "stop_time": "22:00",
        "status": "ACTIVE",
    }


def test_platform_account_edit_state_never_returns_raw_credentials() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.BAEMIN,
            label="쿠팡 운영 계정",
            username="real-login-id",
            password="plain-password",
            verification_email_address="owner@example.test",
            verification_email_app_password="mail-app-password",
            verification_email_subject_keyword="인증번호",
            verification_email_sender_keyword="coupang",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/platform-accounts/pa-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    body = resp.text
    assert "real-login-id" not in body
    assert "plain-password" not in body
    assert "owner@example.test" not in body
    assert "mail-app-password" not in body
    assert resp.json() == {
        "id": "pa-1",
        "platform": "BAEMIN",
        "label": "쿠팡 운영 계정",
        "username_label": "설정됨",
        "password_label": "설정됨",
        "verification_email_address_label": "설정됨",
        "verification_email_app_password_label": "설정됨",
        "verification_email_subject_keyword": "인증번호",
        "verification_email_sender_keyword": "coupang",
        "auth_state": "UNKNOWN",
    }


def test_platform_account_edit_state_unset_secrets_are_not_configured() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.COUPANG,
            label="빈 계정",
            username="",
            password="",
            verification_email_address="",
            verification_email_app_password="",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo))

    body = client.get("/admin/platform-accounts/pa-1/edit-state?tenant=tn-1").json()

    assert body["username_label"] == "미설정"
    assert body["password_label"] == "미설정"
    assert body["verification_email_address_label"] == "미설정"
    assert body["verification_email_app_password_label"] == "미설정"


def test_customer_edit_state_returns_safe_telegram_labels() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(
        Tenant(
            id="tn-1",
            name="H&J",
            status=CustomerLifecycleState.PAYMENT_ACTIVE,
            created_at=_NOW,
            telegram_bot_token="secret-bot-token",
            telegram_webhook_secret="",
            sending_enabled=False,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/customers/tn-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert "secret-bot-token" not in resp.text
    assert resp.json() == {
        "id": "tn-1",
        "name": "H&J",
        "status": "PAYMENT_ACTIVE",
        "telegram_bot_token_label": "설정됨",
        "telegram_webhook_secret_label": "미설정",
        "sending_enabled": False,
        "send_test_passed": False,
    }


def test_subscription_edit_state_returns_status() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_subscription(_subscription(status=SubscriptionStatus.PAYMENT_ACTIVE))
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/subscriptions/sub-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {"id": "sub-1", "status": "PAYMENT_ACTIVE"}


def test_delivery_rule_edit_state_returns_flags() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target())
    repo.seed_delivery_rule(
        DeliveryRule(
            id="rule-1", target_id="mt-1", channel_id="ch-1", send_only_on_change=True
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/delivery-rules/rule-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    assert resp.json() == {
        "id": "rule-1",
        "enabled": True,
        "send_only_on_change": True,
    }


def test_edit_state_routes_require_operator() -> None:
    repo = _seeded_repo()
    repo.seed_monitoring_target(_target())
    client = TestClient(_app_with(repo, principal=_VIEWER))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.FORBIDDEN


def test_edit_state_routes_are_tenant_scoped() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_monitoring_target(_target(tenant=_OTHER))
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/monitoring-targets/mt-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.NOT_FOUND


def test_platform_account_edit_state_redacts_all_secret_like_values() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.COUPANG,
            label="계정",
            username="coupang-real-user",
            password="coupang-real-password",
            verification_email_address="mail-owner@example.test",
            verification_email_app_password="real-mail-app-password",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo))

    resp = client.get("/admin/platform-accounts/pa-1/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.OK
    text = resp.text
    for forbidden in (
        "coupang-real-user",
        "coupang-real-password",
        "mail-owner@example.test",
        "real-mail-app-password",
    ):
        assert forbidden not in text
    for expected in (
        '"username_label":"설정됨"',
        '"password_label":"설정됨"',
        '"verification_email_address_label":"설정됨"',
        '"verification_email_app_password_label":"설정됨"',
    ):
        assert expected in text


def test_edit_state_does_not_leak_into_options_fragment() -> None:
    # /options 는 viewer 도 접근 가능 — credential 상태/URL 같은 상세값을 싣지 않는다.
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(
        PlatformAccount(
            id="pa-1",
            tenant_id="tn-1",
            platform=Platform.COUPANG,
            label="계정",
            username="leak-user",
            password="leak-pass",
            auth_state=BaeminAuthState.UNKNOWN,
        )
    )
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/platform-accounts/options?tenant=tn-1").text

    assert "leak-user" not in body
    assert "leak-pass" not in body
    assert "설정됨" not in body
    assert "username_label" not in body


# ── (template) 현재값 로드 hook + 비밀값 라벨 전용 + 실패 inline 표시 ────────────────────

def _entity_admin_template() -> str:
    return Path("src/rider_server/admin/templates/_entity_admin.html").read_text(
        encoding="utf-8"
    )


def test_entity_admin_has_current_value_load_hooks() -> None:
    template = _entity_admin_template()

    assert 'onchange="loadTargetEditState(this)"' in template
    assert 'onchange="loadPlatformAccountEditState(this)"' in template
    assert 'onchange="loadCustomerEditState(this)"' in template
    assert 'onchange="loadSubscriptionEditState(this)"' in template
    assert 'onchange="loadDeliveryRuleEditState(this)"' in template
    assert "function fetchEditState(" in template
    assert "/edit-state?tenant=" in template


def test_entity_admin_secret_current_values_are_status_labels_only() -> None:
    template = _entity_admin_template()

    assert 'id="acc-current-username"' in template
    assert 'id="acc-current-password"' in template
    assert 'id="acc-current-email"' in template
    assert 'id="acc-current-email-password"' in template
    assert "username_label" in template
    assert "password_label" in template
    assert "verification_email_app_password_label" in template
    assert "document.getElementById('acc-edit-password').value = data.password" not in template
    assert "document.getElementById('acc-edit-email-password').value = data.verification_email_app_password" not in template


def test_entity_admin_template_has_no_raw_secret_field_mapping() -> None:
    template = _entity_admin_template()

    for forbidden_snippet in (
        ".value = data.username",
        ".value = data.password",
        ".value = data.verification_email_address",
        ".value = data.verification_email_app_password",
        "data-password",
        "data-verification-email-app-password",
    ):
        assert forbidden_snippet not in template


def test_entity_admin_channel_autofill_contract_stays_in_place() -> None:
    template = _entity_admin_template()

    assert 'onchange="populateChannelFields(this)"' in template
    assert "function populateChannelFields(select)" in template
    assert "option.dataset.chat" in template
    assert "option.dataset.thread" in template
    assert "option.dataset.kakao" in template


def test_entity_admin_edit_state_failure_uses_inline_status() -> None:
    template = _entity_admin_template()

    assert "현재값 조회 실패 · 권한 또는 대상을 확인하세요" in template
    assert "select.closest('.edit-row')" in template
    assert "row.querySelector('.inline-action-status')" in template


def test_entity_admin_keeps_partial_update_semantics() -> None:
    template = _entity_admin_template()

    # filledValues 는 여전히 편집 submit 에 쓰인다(비우면 유지). edit-state 로드는 이를 바꾸지 않는다.
    assert "function filledValues" in template
    assert "filledValues({" in template
    assert "비운 칸은 기존 값을 유지합니다" in template
    # 비밀값 재노출 안내가 명시돼 있다(설정 여부만 표시).
    assert "설정 여부만 표시" in template


# ── (review fix) 2026-06-29 검토 반영 ─────────────────────────────────────────────────

def test_customer_edit_state_cross_tenant_query_is_not_found() -> None:
    """Finding 2 — 고객 edit-state 도 활성 tenant(?tenant=) 와 path tenant 불일치를 404 로 막는다."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant(_OTHER))  # tn-2 만 존재
    client = TestClient(_app_with(repo))

    # path 는 tn-2 인데 활성 tenant 는 tn-1 → cross-tenant 조회로 404(존재 누설 방지).
    resp = client.get("/admin/customers/tn-2/edit-state?tenant=tn-1")

    assert resp.status_code == HTTPStatus.NOT_FOUND
    assert "고객" not in resp.text  # tn-2 의 고객명 등 상세를 노출하지 않는다


def test_customer_edit_state_same_tenant_query_ok() -> None:
    """Finding 2 — path tenant 와 활성 tenant 가 같으면 정상 조회된다(빈 ?tenant= 도 허용)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())  # tn-1
    client = TestClient(_app_with(repo))

    same = client.get("/admin/customers/tn-1/edit-state?tenant=tn-1")
    no_active = client.get("/admin/customers/tn-1/edit-state")

    assert same.status_code == HTTPStatus.OK
    assert same.json()["id"] == "tn-1"
    assert no_active.status_code == HTTPStatus.OK
    assert no_active.json()["id"] == "tn-1"


def test_entity_admin_edit_state_has_stale_response_guard() -> None:
    """Finding 1 — 모든 loader 가 요청-시점 id 가드(editStateStillCurrent)를 거쳐야 한다.

    A→B 빠른 전환 시 늦게 온 A 응답이 B 폼에 적용되는 경쟁을 막는다. 가드는 (1)현재 선택 유지
    (2)응답 id 일치 둘 다 확인한다. JS 단언이라 실제 브라우저 비동기까지 잡지는 못하므로 route
    test 와 함께 둔다.
    """
    template = _entity_admin_template()

    assert "function editStateStillCurrent(" in template
    assert "select.value === requestedId" in template
    assert "data.id === requestedId" in template
    # 5개 loader 전부 가드를 호출한다.
    assert template.count("if (!editStateStillCurrent(select, requestedId, data)) { return; }") >= 5
    # requestedId 를 응답 전에 캡처한다(select.value 를 then 안에서 다시 읽지 않음).
    assert "var requestedId = select.value;" in template


def test_entity_admin_send_gate_customer_select_loads_state() -> None:
    """Finding 3 — 실발송 게이트 고객 select 도 선택 시 현재값/게이트 상태를 갱신한다."""
    template = _entity_admin_template()

    assert 'onchange="loadSendGateCustomerState(this)"' in template
    assert "function loadSendGateCustomerState(select)" in template
    # 게이트 통과/ON 허용은 syncSendingGate 로 반영한다.
    assert "syncSendingGate" in template


def test_entity_admin_edit_state_load_gate_locks_save_until_loaded() -> None:
    """Finding 1 보강 — 선택 직후 ~ 현재값 도착 전 창에서 이전 항목 값으로 저장되는 것을 막는다.

    선택 즉시 입력칸 clear + loadedId 비움(저장 잠금), 응답 stale 가드 통과 후에만 loadedId 채워
    저장 해제. syncEntityFormButtons 와 crudButton 양쪽에서 loadedId===value 를 강제(이중 방어).
    """
    template = _entity_admin_template()

    # 핵심 헬퍼 존재.
    assert "function beginEditStateLoad(" in template
    assert "function markEditStateLoaded(" in template
    assert "function editStateLoaded(" in template
    assert "select.dataset.loadedId" in template

    # 값 수정 저장 버튼 5종 + 게이트 저장이 data-needs-loaded 로 잠긴다.
    for select_id in (
        "tgt-edit-id",
        "acc-edit-id",
        "cust-edit-id",
        "sub-edit-id",
        "rule-edit-id",
        "tg-edit-id",
    ):
        assert f'data-needs-loaded="{select_id}"' in template

    # syncEntityFormButtons 가 로드 여부를 본다.
    assert "button.dataset.needsLoaded" in template
    assert "editStateLoaded(needsLoaded)" in template

    # crudButton 최종 방어선도 동일 게이트를 건다.
    assert "현재값을 불러오는 중입니다 · 잠시 후 다시 시도하세요" in template

    # 계정 편집은 선택 즉시 비밀번호/앱 비밀번호 입력칸을 비운다(이전 계정 입력 누출 방지).
    assert "'acc-edit-password'" in template
    assert "'acc-edit-email-password'" in template

    # 고객 전환 시 텔레그램 봇 토큰/보안키 입력칸도 비우고, 그 저장 버튼도 로드 게이트로 잠근다.
    assert "'tg-edit-token'" in template
    assert "'tg-edit-secret'" in template
    cred_btn = template[
        template.index("saveTelegramCredentials(this)") - 200 :
        template.index("saveTelegramCredentials(this)")
    ]
    assert 'data-needs-loaded="tg-edit-id"' in cred_btn

    # '현재값 불러옴' 성공 라벨은 stale 가드 통과 후(markEditStateLoaded)에만 표시한다.
    assert "'현재값 불러옴'" in template
    markmark = template.index("function markEditStateLoaded(")
    assert "'현재값 불러옴'" in template[markmark : markmark + 400]


def test_entity_admin_deactivate_buttons_not_load_gated() -> None:
    """비활성화/복구/삭제는 id 만 필요하므로 현재값 로드 게이트를 걸지 않는다(잘못 잠그면 운영 불가)."""
    template = _entity_admin_template()

    # 비활성화/복구/삭제 onclick 라인에는 data-needs-loaded 가 붙지 않는다.
    for suffix in ("/deactivate", "/reactivate", "/delete"):
        for line in template.splitlines():
            if f"crudButton(this, " in line and f"'{suffix}'" in line:
                assert "data-needs-loaded" not in line
