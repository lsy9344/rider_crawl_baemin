"""Monitoring target-specific Admin entity write service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Awaitable, Callable

from rider_server.domain import (
    MonitoringTarget,
    MonitoringTargetStatus,
    PlatformAccount,
)
from rider_server.services.admin_action_service import AuditEntry, TARGET_TYPE_TARGET

from .common import (
    ACTION_MONITORING_TARGET_CREATE,
    ACTION_MONITORING_TARGET_DEACTIVATE,
    ACTION_MONITORING_TARGET_REACTIVATE,
    ACTION_MONITORING_TARGET_UPDATE,
    AdminEntityRepository,
    TargetWriteResult,
    is_center_name_risky,
)


class TargetAdminEntityService:
    """Monitoring target create/update/deactivate behavior."""

    def __init__(
        self,
        repository: AdminEntityRepository,
        audit_factory: Callable[..., AuditEntry],
        scoped_target: Callable[..., Awaitable[MonitoringTarget]],
        scoped_platform_account: Callable[..., Awaitable[PlatformAccount]],
    ) -> None:
        self._repo = repository
        self._audit = audit_factory
        self._scoped_target = scoped_target
        self._scoped_platform_account = scoped_platform_account

    async def create_monitoring_target(
        self,
        *,
        entity_id: str,
        tenant_id: str,
        platform_account_id: str,
        name: str,
        center_name: str,
        external_id: str = "",
        url: str = "",
        interval_minutes: int = 0,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> TargetWriteResult:
        if not (name or "").strip():
            raise ValueError("대상 표시명(name)이 필요합니다")
        account = await self._scoped_platform_account(
            platform_account_id, tenant_id=tenant_id
        )
        risky = is_center_name_risky(account.platform, center_name)
        if risky:
            raise ValueError("쿠팡 센터/상점명(center_name)이 필요합니다")
        target = MonitoringTarget(
            id=entity_id,
            tenant_id=tenant_id,
            platform_account_id=platform_account_id,
            name=name,
            center_name=center_name,
            external_id=external_id,
            url=url,
            interval_minutes=interval_minutes,
            status=MonitoringTargetStatus.ACTIVE,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MONITORING_TARGET_CREATE,
            target_type=TARGET_TYPE_TARGET,
            target_id=entity_id,
            at=at,
            diff={
                "op": "create",
                "platform_account_id": platform_account_id,
                "name": name,
                "center_name": center_name,
                "center_name_risky": risky,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.create_monitoring_target(target, audit)
        return TargetWriteResult(target=target, center_name_risky=risky)

    async def update_monitoring_target(
        self,
        target_id: str,
        *,
        tenant_id: str,
        name: str | None = None,
        center_name: str | None = None,
        external_id: str | None = None,
        url: str | None = None,
        interval_minutes: int | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> TargetWriteResult:
        existing = await self._scoped_target(target_id, tenant_id=tenant_id)
        account = await self._scoped_platform_account(
            existing.platform_account_id, tenant_id=tenant_id
        )
        updated = replace(
            existing,
            name=name if name is not None and name.strip() else existing.name,
            center_name=center_name if center_name is not None else existing.center_name,
            external_id=external_id if external_id is not None else existing.external_id,
            url=url if url is not None else existing.url,
            interval_minutes=(
                interval_minutes if interval_minutes is not None else existing.interval_minutes
            ),
        )
        risky = is_center_name_risky(account.platform, updated.center_name)
        if risky:
            raise ValueError("쿠팡 센터/상점명(center_name)이 필요합니다")
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MONITORING_TARGET_UPDATE,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={
                "from_name": existing.name,
                "to_name": updated.name,
                "from_center_name": existing.center_name,
                "to_center_name": updated.center_name,
                "center_name_risky": risky,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_monitoring_target(updated, audit)
        return TargetWriteResult(target=updated, center_name_risky=risky)

    async def deactivate_monitoring_target(
        self,
        target_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MonitoringTarget:
        existing = await self._scoped_target(target_id, tenant_id=tenant_id)
        if existing.status is MonitoringTargetStatus.INACTIVE:
            return existing
        updated = replace(existing, status=MonitoringTargetStatus.INACTIVE)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MONITORING_TARGET_DEACTIVATE,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={
                "from_status": existing.status.value,
                "to_status": updated.status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_monitoring_target(updated, audit)
        return updated

    async def reactivate_monitoring_target(
        self,
        target_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MonitoringTarget:
        existing = await self._scoped_target(target_id, tenant_id=tenant_id)
        if existing.status is not MonitoringTargetStatus.INACTIVE:
            return existing
        updated = replace(existing, status=MonitoringTargetStatus.ACTIVE)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MONITORING_TARGET_REACTIVATE,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={
                "from_status": existing.status.value,
                "to_status": updated.status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_monitoring_target(updated, audit)
        return updated
