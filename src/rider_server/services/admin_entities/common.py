"""Common helpers for Admin entity CRUD services."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from rider_crawl.config import DEFAULT_BAEMIN_CENTER_NAME
from rider_server.domain import (
    AuditResult,
    CustomerLifecycleState,
    DeliveryRule,
    MessengerChannel,
    MonitoringTarget,
    Platform,
    PlatformAccount,
    Subscription,
    Tenant,
)
from rider_server.services.admin_action_service import (
    AdminActionNotFound,
    AuditEntry,
    TARGET_TYPE_CHANNEL,
    TARGET_TYPE_TARGET,
    TenantScopeViolation,
    build_diff_redacted,
)

ACTION_TENANT_CREATE = "TENANT_CREATE"
ACTION_TENANT_UPDATE = "TENANT_UPDATE"
ACTION_TENANT_DELETE = "TENANT_DELETE"
ACTION_SUBSCRIPTION_CREATE = "SUBSCRIPTION_CREATE"
ACTION_SUBSCRIPTION_UPDATE = "SUBSCRIPTION_UPDATE"
ACTION_PLATFORM_ACCOUNT_CREATE = "PLATFORM_ACCOUNT_CREATE"
ACTION_PLATFORM_ACCOUNT_UPDATE = "PLATFORM_ACCOUNT_UPDATE"
ACTION_MONITORING_TARGET_CREATE = "MONITORING_TARGET_CREATE"
ACTION_MONITORING_TARGET_UPDATE = "MONITORING_TARGET_UPDATE"
ACTION_MONITORING_TARGET_DEACTIVATE = "MONITORING_TARGET_DEACTIVATE"
ACTION_MONITORING_TARGET_REACTIVATE = "MONITORING_TARGET_REACTIVATE"
ACTION_MESSENGER_CHANNEL_CREATE = "MESSENGER_CHANNEL_CREATE"
ACTION_MESSENGER_CHANNEL_UPDATE = "MESSENGER_CHANNEL_UPDATE"
ACTION_MESSENGER_CHANNEL_ACTIVATE = "MESSENGER_CHANNEL_ACTIVATE"
ACTION_MESSENGER_CHANNEL_DEACTIVATE = "MESSENGER_CHANNEL_DEACTIVATE"
ACTION_DELIVERY_RULE_CREATE = "DELIVERY_RULE_CREATE"
ACTION_DELIVERY_RULE_UPDATE = "DELIVERY_RULE_UPDATE"
ACTION_DELIVERY_RULE_DEACTIVATE = "DELIVERY_RULE_DEACTIVATE"

TARGET_TYPE_TENANT = "tenant"
TARGET_TYPE_SUBSCRIPTION = "subscription"
TARGET_TYPE_PLATFORM_ACCOUNT = "platform_account"
TARGET_TYPE_DELIVERY_RULE = "delivery_rule"

DEFAULT_VERIFICATION_EMAIL_SUBJECT_KEYWORD = "인증번호"
DEFAULT_VERIFICATION_EMAIL_SENDER_KEYWORD = "coupang"


class AdminEntityDuplicateError(ValueError):
    """운영자가 고칠 수 있는 중복 입력/unique violation."""

    def __init__(self, field: str, message: str = "중복된 값입니다") -> None:
        if "중복" not in message:
            message = f"중복: {message}"
        super().__init__(message)
        self.field = field


class AdminEntityDeleteBlockedError(ValueError):
    """연결 데이터가 있어 tenant 물리 삭제를 차단해야 하는 경우."""


def is_center_name_risky(platform: Platform, center_name: str) -> bool:
    """쿠팡 대상의 ``center_name`` 이 비었거나 배민 기본값이면 위험으로 본다."""

    if platform is not Platform.COUPANG:
        return False
    normalized = (center_name or "").strip()
    return not normalized or normalized == DEFAULT_BAEMIN_CENTER_NAME


def _keyword_or_default(value: str | None, default: str) -> str:
    normalized = (value or "").strip()
    return normalized or default


_SECRET_REF_PREFIXES = ("vault://", "local:", "env:", "dpapi:")


def _secret_ref_or_empty(value: str | None, field_name: str) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    if "://" in normalized or normalized.startswith(_SECRET_REF_PREFIXES):
        return normalized
    raise ValueError(f"{field_name}은 secret ref 핸들만 허용됩니다")


def _secret_change_label(old: str, new: str) -> str:
    if old == new:
        return "unchanged"
    if not new:
        return "cleared"
    return "set"


@dataclass(frozen=True)
class TargetWriteResult:
    """모니터링 대상 생성/편집 결과 + center_name 위험 경고 플래그."""

    target: MonitoringTarget
    center_name_risky: bool


class AdminEntityRepository(Protocol):
    """Admin 엔티티 CRUD 영속 포트."""

    async def get_tenant(self, tenant_id: str) -> Tenant | None: ...

    async def get_subscription(self, subscription_id: str) -> Subscription | None: ...

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None: ...

    async def get_monitoring_target(self, target_id: str) -> MonitoringTarget | None: ...

    async def get_messenger_channel(self, channel_id: str) -> MessengerChannel | None: ...

    async def get_delivery_rule(self, rule_id: str) -> DeliveryRule | None: ...

    async def list_tenants(self) -> list[Tenant]: ...

    async def list_subscriptions(self, tenant_id: str) -> list[Subscription]: ...

    async def list_platform_accounts(self, tenant_id: str) -> list[PlatformAccount]: ...

    async def list_monitoring_targets(self, tenant_id: str) -> list[MonitoringTarget]: ...

    async def list_messenger_channels(self, tenant_id: str) -> list[MessengerChannel]: ...

    async def list_delivery_rules(self, target_id: str) -> list[DeliveryRule]: ...

    async def tenant_has_dependencies(self, tenant_id: str) -> bool: ...

    async def create_tenant(self, tenant: Tenant, audit: AuditEntry) -> None: ...

    async def create_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None: ...

    async def create_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None: ...

    async def create_monitoring_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None: ...

    async def create_messenger_channel(
        self,
        channel: MessengerChannel,
        audit: AuditEntry,
        *,
        registration_code: str | None = None,
    ) -> None: ...

    async def create_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None: ...

    async def save_tenant(self, tenant: Tenant, audit: AuditEntry) -> None: ...

    async def save_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None: ...

    async def save_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None: ...

    async def save_monitoring_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None: ...

    async def save_messenger_channel(
        self, channel: MessengerChannel, audit: AuditEntry
    ) -> None: ...

    async def save_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None: ...

    async def delete_tenant(self, tenant_id: str, audit: AuditEntry) -> None: ...


def build_admin_audit(
    *,
    actor_id: str | None,
    action: str,
    target_type: str,
    target_id: str | None,
    at: datetime,
    diff: dict,
    source: str | None = None,
    reason: str | None = None,
    result: str = AuditResult.SUCCESS.value,
) -> AuditEntry:
    from rider_crawl.redaction import redact

    return AuditEntry(
        actor_id=actor_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        diff_redacted=build_diff_redacted(diff),
        created_at=at,
        source=redact(source) if source else None,
        reason=redact(reason) if reason else None,
        result=result,
    )


async def scoped_tenant(repo: AdminEntityRepository, tenant_id: str) -> Tenant:
    tenant = await repo.get_tenant(tenant_id)
    if tenant is None:
        raise AdminActionNotFound(TARGET_TYPE_TENANT, tenant_id)
    if tenant.id != tenant_id:
        raise TenantScopeViolation(TARGET_TYPE_TENANT, tenant_id)
    return tenant


async def scoped_subscription(
    repo: AdminEntityRepository, subscription_id: str, *, tenant_id: str
) -> Subscription:
    subscription = await repo.get_subscription(subscription_id)
    if subscription is None:
        raise AdminActionNotFound(TARGET_TYPE_SUBSCRIPTION, subscription_id)
    if subscription.tenant_id != tenant_id:
        raise TenantScopeViolation(TARGET_TYPE_SUBSCRIPTION, subscription_id)
    return subscription


async def scoped_platform_account(
    repo: AdminEntityRepository, account_id: str, *, tenant_id: str
) -> PlatformAccount:
    account = await repo.get_platform_account(account_id)
    if account is None:
        raise AdminActionNotFound(TARGET_TYPE_PLATFORM_ACCOUNT, account_id)
    if account.tenant_id != tenant_id:
        raise TenantScopeViolation(TARGET_TYPE_PLATFORM_ACCOUNT, account_id)
    return account


async def scoped_target(
    repo: AdminEntityRepository, target_id: str, *, tenant_id: str
) -> MonitoringTarget:
    target = await repo.get_monitoring_target(target_id)
    if target is None:
        raise AdminActionNotFound(TARGET_TYPE_TARGET, target_id)
    if target.tenant_id != tenant_id:
        raise TenantScopeViolation(TARGET_TYPE_TARGET, target_id)
    return target


async def scoped_channel(
    repo: AdminEntityRepository, channel_id: str, *, tenant_id: str
) -> MessengerChannel:
    channel = await repo.get_messenger_channel(channel_id)
    if channel is None:
        raise AdminActionNotFound(TARGET_TYPE_CHANNEL, channel_id)
    if channel.tenant_id != tenant_id:
        raise TenantScopeViolation(TARGET_TYPE_CHANNEL, channel_id)
    return channel


async def scoped_rule(
    repo: AdminEntityRepository, rule_id: str, *, tenant_id: str
) -> tuple[DeliveryRule, MonitoringTarget]:
    rule = await repo.get_delivery_rule(rule_id)
    if rule is None:
        raise AdminActionNotFound(TARGET_TYPE_DELIVERY_RULE, rule_id)
    target = await scoped_target(repo, rule.target_id, tenant_id=tenant_id)
    return rule, target
