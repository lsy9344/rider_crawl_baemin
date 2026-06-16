"""Story 5.11 / AC1·AC2·AC3·AC4 — Admin 엔티티 CRUD(always-run service/순수 + 라우트).

(1) always-run 순수/service(무 DB, in-memory fake repo + 주입 시각/actor): 5개 엔티티
    create/update/deactivate happy path, tenant scope 차단(cross-tenant→TenantScopeViolation),
    secret 평문 거부(ValueError), center_name 위험 판정(쿠팡 빈/배민기본값), DeliveryRule 1:N
    fan-out, soft-delete 상태값 단언, audit before/after+result 기록.
(2) 라우트(TestClient + 주입 _OPERATOR): POST 200/HTMX fragment, VIEWER→403, 미인증→401,
    tenant 불일치→404, 평문 secret→400, center_name 위험 경고 fragment.

fake 값만(실제 토큰/전화/이메일/chat_id 형태 금지). 평면 ``tests/server/`` 컨벤션.
``pytest-asyncio`` 미도입 → ``asyncio.run`` 으로 async service 구동(5.4~5.7 선례).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from http import HTTPStatus

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
    Tenant,
)
from rider_server.main import create_app
from rider_server.security import AdminPrincipal, AdminRole
from rider_server.services.admin_action_service import (
    AdminActionNotFound,
    TenantScopeViolation,
)
from rider_server.services.admin_entity_service import (
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


def test_create_platform_account_with_creds_plaintext() -> None:
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


def test_create_platform_account_plaintext_now_accepted() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    svc = _svc(repo)

    account = _run(
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

    assert account.username == "myuser"
    assert account.password == "mypass"
    assert _run(repo.get_platform_account("pa-plain")).username == "myuser"


def test_create_monitoring_target_links_account_and_flags_coupang_risk() -> None:
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
            center_name="",  # 쿠팡 빈 center → 위험
            at=_NOW,
            actor_id=_ACTOR,
        )
    )

    assert result.center_name_risky is True
    assert result.target.status is MonitoringTargetStatus.ACTIVE
    assert _run(repo.get_monitoring_target("mt-new")).name == "대상"
    assert repo.audits[-1].action == "MONITORING_TARGET_CREATE"


def test_create_messenger_channel_is_pending() -> None:
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


def test_route_create_coupang_blank_center_warns() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    repo.seed_platform_account(_account(platform=Platform.COUPANG))
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/monitoring-targets?tenant=tn-1",
        data={"platform_account_id": "pa-1", "name": "대상", "center_name": ""},
    )

    assert resp.status_code == HTTPStatus.OK
    assert "위험" in resp.text  # center_name 위험 경고(차단 아님 — 저장됨)
    assert len(_run(repo.list_monitoring_targets("tn-1"))) == 1


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


def test_route_platform_account_plaintext_accepted() -> None:
    repo = InMemoryAdminEntityRepository()
    repo.seed_tenant(_tenant())
    client = TestClient(_app_with(repo))

    resp = client.post(
        "/admin/platform-accounts?tenant=tn-1",
        data={
            "platform": "BAEMIN",
            "label": "x",
            "username": "myuser",
            "password": "vault://p",
        },
    )

    assert resp.status_code == HTTPStatus.OK
    assert "플랫폼 계정 생성됨" in resp.text


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


def test_entities_form_exposes_full_edit_and_delivery_rule_controls() -> None:
    repo = _seeded_repo()
    client = TestClient(_app_with(repo, principal=_VIEWER))

    body = client.get("/admin/entities?tenant=tn-1").text

    for marker in (
        'id="tgt-edit-name"',
        'id="tgt-edit-external"',
        'id="tgt-edit-url"',
        'id="tgt-edit-interval"',
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
    assert "crudUpdate('/admin/monitoring-targets/', 'tgt-edit-id', '', filledValues({" in body
    assert "crudUpdate('/admin/messenger-channels/', 'ch-edit-id', '', filledValues({" in body


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


def test_update_platform_account_plaintext_now_accepted() -> None:
    """G3 — 평문 자격증명을 update 로 주면 그대로 저장된다(SecretRef 제거됨)."""
    repo = InMemoryAdminEntityRepository()
    repo.seed_platform_account(_account())
    svc = _svc(repo)

    updated = _run(
        svc.update_platform_account(
            "pa-1", tenant_id=_TENANT,
            password="my-new-password",
            at=_NOW, actor_id=_ACTOR,
        )
    )

    assert updated.password == "my-new-password"
    assert repo.audits[-1].action == "PLATFORM_ACCOUNT_UPDATE"


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

def test_route_create_customer_persists() -> None:
    """G12 — ``POST /admin/customers`` happy(dev 미커버 리소스). fragment + 영속."""
    repo = InMemoryAdminEntityRepository()
    client = TestClient(_app_with(repo))

    resp = client.post("/admin/customers", data={"name": "신규고객"})

    assert resp.status_code == HTTPStatus.OK
    assert "고객 생성됨" in resp.text
    assert resp.headers["HX-Redirect"].startswith("/admin?tenant=")
    assert len(_run(repo.list_tenants())) == 1


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


def test_route_create_messenger_channel_pending() -> None:
    """G17 — ``POST /admin/messenger-channels`` happy → PENDING 사전 생성."""
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
