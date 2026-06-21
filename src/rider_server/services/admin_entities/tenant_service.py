"""Tenant-specific Admin entity write service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Awaitable, Callable

from rider_server.domain import CustomerLifecycleState, Tenant
from rider_server.services.admin_action_service import AuditEntry

from .common import (
    ACTION_TENANT_CREATE,
    ACTION_TENANT_DELETE,
    ACTION_TENANT_UPDATE,
    AdminEntityDeleteBlockedError,
    AdminEntityRepository,
    TARGET_TYPE_TENANT,
    _secret_change_label,
)


class TenantAdminEntityService:
    """Tenant create/update/delete behavior."""

    def __init__(
        self,
        repository: AdminEntityRepository,
        audit_factory: Callable[..., AuditEntry],
        scoped_tenant: Callable[[str], Awaitable[Tenant]],
    ) -> None:
        self._repo = repository
        self._audit = audit_factory
        self._scoped_tenant = scoped_tenant

    async def create_tenant(
        self,
        *,
        entity_id: str,
        name: str,
        at: datetime,
        actor_id: str | None,
        status: CustomerLifecycleState = CustomerLifecycleState.LEAD,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        if not (name or "").strip():
            raise ValueError("고객명(name)이 필요합니다")
        tenant = Tenant(id=entity_id, name=name, status=status, created_at=at)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TENANT_CREATE,
            target_type=TARGET_TYPE_TENANT,
            target_id=entity_id,
            at=at,
            diff={"op": "create", "name": name, "to_status": status.value, "reason": reason},
            source=source,
            reason=reason,
        )
        await self._repo.create_tenant(tenant, audit)
        return tenant

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        status: CustomerLifecycleState | None = None,
        telegram_bot_token: str | None = None,
        telegram_webhook_secret: str | None = None,
        sending_enabled: bool | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        existing = await self._scoped_tenant(tenant_id)
        # 실발송 게이트(임시): 테스트 통과 상태 모델은 후속 범위(work-order "후속 범위")라
        # 아직 없다. 그래서 OFF→ON **전이**만 막는다 — 이미 켜진 tenant 의 다른 필드 편집(폼이
        # 현재 값 sending_enabled=True 를 그대로 재전송)은 거부하지 않는다(no-op re-assert 허용).
        # OFF 로 끄는 것은 항상 허용한다(안전 방향).
        if sending_enabled is True and not existing.sending_enabled:
            raise ValueError(
                "수집 테스트와 전송 테스트 완료 전에는 실제 메시지 보내기를 켤 수 없습니다"
            )
        updated = replace(
            existing,
            name=name if name is not None and name.strip() else existing.name,
            status=status or existing.status,
            telegram_bot_token=(
                telegram_bot_token
                if telegram_bot_token is not None
                else existing.telegram_bot_token
            ),
            telegram_webhook_secret=(
                telegram_webhook_secret
                if telegram_webhook_secret is not None
                else existing.telegram_webhook_secret
            ),
            sending_enabled=(
                sending_enabled if sending_enabled is not None else existing.sending_enabled
            ),
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TENANT_UPDATE,
            target_type=TARGET_TYPE_TENANT,
            target_id=tenant_id,
            at=at,
            diff={
                "from_name": existing.name,
                "to_name": updated.name,
                "from_status": existing.status.value,
                "to_status": updated.status.value,
                "telegram_bot_token": _secret_change_label(
                    existing.telegram_bot_token, updated.telegram_bot_token
                ),
                "telegram_webhook_secret": _secret_change_label(
                    existing.telegram_webhook_secret, updated.telegram_webhook_secret
                ),
                "from_sending_enabled": existing.sending_enabled,
                "to_sending_enabled": updated.sending_enabled,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_tenant(updated, audit)
        return updated

    async def delete_tenant(
        self,
        tenant_id: str,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        existing = await self._scoped_tenant(tenant_id)
        if await self._repo.tenant_has_dependencies(tenant_id):
            raise AdminEntityDeleteBlockedError(
                "연결 데이터가 있어 고객을 삭제할 수 없습니다"
            )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TENANT_DELETE,
            target_type=TARGET_TYPE_TENANT,
            target_id=tenant_id,
            at=at,
            diff={
                "op": "delete",
                "name": existing.name,
                "from_status": existing.status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.delete_tenant(tenant_id, audit)
        return existing
