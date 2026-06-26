"""Tenant-specific Admin entity write service."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Awaitable, Callable

# update_tenant 의 send_test_passed_at 파라미터 sentinel — None 은 "테스트 통과 시각을 NULL 로
# 지운다(미통과로 되돌림)"는 명시적 의미라, "키 없음=유지" 와 구분해야 한다(다른 3-state 필드와
# 동형이되, None 자체가 유효 값이라 별도 sentinel 사용).
_UNSET: Any = object()

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
        send_test_passed_at: datetime | None = _UNSET,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        existing = await self._scoped_tenant(tenant_id)
        # send_test_passed_at: sentinel(_UNSET)이면 유지, 그 외(datetime 또는 None)면 명시 설정.
        # ChannelTestService 가 채널 전송 테스트 성공 시 시각을 스탬프하고, 실패/초기화 시 None 으로
        # 지운다. 이 필드가 게이트의 해제 조건이다.
        next_send_test_passed_at = (
            existing.send_test_passed_at
            if send_test_passed_at is _UNSET
            else send_test_passed_at
        )
        # 실발송 게이트: 전송 테스트가 통과(send_test_passed_at 존재)해야 OFF→ON 전이를 허용한다.
        # 통과 시각이 없으면(미통과) OFF→ON 을 막는다 — 다른 chat/방 오발송보다 미발송이 안전
        # (fail-closed). 이미 켜진 tenant 의 다른 필드 편집(폼이 sending_enabled=True 재전송)은
        # 거부하지 않고(no-op re-assert 허용), OFF 로 끄는 것은 항상 허용한다(안전 방향).
        if (
            sending_enabled is True
            and not existing.sending_enabled
            and next_send_test_passed_at is None
        ):
            raise ValueError(
                "전송 테스트 완료 전에는 실제 메시지 보내기를 켤 수 없습니다"
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
            send_test_passed_at=next_send_test_passed_at,
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
                "from_send_test_passed_at": (
                    existing.send_test_passed_at.isoformat()
                    if existing.send_test_passed_at is not None
                    else None
                ),
                "to_send_test_passed_at": (
                    updated.send_test_passed_at.isoformat()
                    if updated.send_test_passed_at is not None
                    else None
                ),
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
