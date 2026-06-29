"""PostgreSQL ``AdminEntityRepository`` 구현 — Story 5.11 (AC1·AC2·AC3·AC4).

:class:`rider_server.services.admin_entity_service.AdminEntityRepository` 포트의 실 DB 구현.
5.2 ``db/base.py`` 의 ``async_sessionmaker`` 를 주입받아 쓰고 새 엔진을 만들지 않는다
(``PostgresAdminActionRepository``/``PostgresChannelRepository`` 선례). async 본문은 DB I/O 만 한다.

**CREATE 는 신규 INSERT 경로(코드베이스 최초):** 5.7 ``transition_*`` 는 UPDATE-only, 5.5
``PostgresChannelRepository.save`` 도 UPDATE-only 라 5개 엔티티 모두 ``insert(Row).values(...)``
를 새로 작성한다. **같은 트랜잭션(AC4):** entity INSERT/UPDATE 와 ``audit_logs`` INSERT 를 **한
세션·한 commit** 으로 묶는다 — 액션만 성공하고 audit 가 누락되는 경우가 없다. 신규 컬럼/테이블/
마이그레이션 0(기존 14표를 INSERT/UPDATE 만).

ORM(``db.models.*``) ↔ 순수 domain 변환은 명시적(레이어 분리 — domain 은 SQLAlchemy import 0).
audit 값 변환·target/actor UUID 파싱은 5.7 :func:`_audit_values` 를 재사용한다(중복 0).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from rider_server.db.models.agent import BrowserProfile as BrowserProfileRow
from rider_server.db.models.agent import Job as JobRow
from rider_server.db.models.account import MonitoringTarget as MonitoringTargetRow
from rider_server.db.models.account import PlatformAccount as PlatformAccountRow
from rider_server.db.models.audit import AuditLog as AuditLogRow
from rider_server.db.models.messaging import DeliveryRule as DeliveryRuleRow
from rider_server.db.models.messaging import MessengerChannel as MessengerChannelRow
from rider_server.db.models.messaging import Snapshot as SnapshotRow
from rider_server.db.models.tenancy import Subscription as SubscriptionRow
from rider_server.db.models.tenancy import Tenant as TenantRow
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

from .admin_action_repository_postgres import _audit_values, _target_to_domain
from .admin_action_service import AuditEntry
from .admin_entity_service import AdminEntityDeleteBlockedError
from .admin_entity_service import AdminEntityDuplicateError


def _uuid(value: str | uuid.UUID) -> uuid.UUID:
    """문자열 id/FK 를 ``uuid.UUID`` 로 강제한다(Uuid 컬럼 INSERT 용 — service 는 str 로 다룸)."""

    return value if isinstance(value, uuid.UUID) else uuid.UUID(value)


def _tenant_to_domain(row: TenantRow) -> Tenant:
    return Tenant(
        id=str(row.id),
        name=row.name,
        status=CustomerLifecycleState(row.status),
        created_at=row.created_at,
        telegram_bot_token=row.telegram_bot_token,
        telegram_webhook_secret=row.telegram_webhook_secret,
        sending_enabled=row.sending_enabled,
        send_test_passed_at=row.send_test_passed_at,
    )


def _subscription_to_domain(row: SubscriptionRow) -> Subscription:
    return Subscription(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        plan=row.plan,
        status=SubscriptionStatus(row.status),
        current_period_end=row.current_period_end,
        quotas=dict(row.quotas or {}),
    )


def _account_to_domain(row: PlatformAccountRow) -> PlatformAccount:
    return PlatformAccount(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        platform=Platform(row.platform),
        label=row.label,
        username=row.username,
        password=row.password,
        verification_email_address=row.verification_email_address,
        verification_email_app_password=row.verification_email_app_password,
        verification_email_subject_keyword=row.verification_email_subject_keyword,
        verification_email_sender_keyword=row.verification_email_sender_keyword,
        auth_state=BaeminAuthState(row.auth_state),
    )


def _channel_to_domain(row: MessengerChannelRow) -> MessengerChannel:
    return MessengerChannel(
        id=str(row.id),
        tenant_id=str(row.tenant_id),
        messenger=Messenger(row.messenger),
        telegram_chat_id=row.telegram_chat_id,
        thread_id=row.thread_id,
        kakao_room_name=row.kakao_room_name,
        state=MessengerChannelState(row.state),
    )


def _rule_to_domain(row: DeliveryRuleRow) -> DeliveryRule:
    return DeliveryRule(
        id=str(row.id),
        target_id=str(row.target_id),
        channel_id=str(row.channel_id),
        template_id=row.template_id,
        enabled=row.enabled,
        send_only_on_change=row.send_only_on_change,
    )


class PostgresAdminEntityRepository:
    """async SQLAlchemy 기반 ``AdminEntityRepository`` — entity INSERT/UPDATE + audit INSERT 동일 트랜잭션."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # ── read(get by id) ─────────────────────────────────────────────────────
    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        stmt = select(TenantRow).where(TenantRow.id == tenant_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _tenant_to_domain(row)

    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        stmt = select(SubscriptionRow).where(SubscriptionRow.id == subscription_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _subscription_to_domain(row)

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None:
        stmt = select(PlatformAccountRow).where(PlatformAccountRow.id == account_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _account_to_domain(row)

    async def get_monitoring_target(self, target_id: str) -> MonitoringTarget | None:
        stmt = select(MonitoringTargetRow).where(MonitoringTargetRow.id == target_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _target_to_domain(row)

    async def get_messenger_channel(self, channel_id: str) -> MessengerChannel | None:
        stmt = select(MessengerChannelRow).where(MessengerChannelRow.id == channel_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _channel_to_domain(row)

    async def get_delivery_rule(self, rule_id: str) -> DeliveryRule | None:
        stmt = select(DeliveryRuleRow).where(DeliveryRuleRow.id == rule_id)
        async with self._session_factory() as session:
            row = (await session.execute(stmt)).scalar_one_or_none()
        return None if row is None else _rule_to_domain(row)

    # ── list(조회) ───────────────────────────────────────────────────────────
    async def list_tenants(self) -> list[Tenant]:
        async with self._session_factory() as session:
            rows = (await session.execute(select(TenantRow))).scalars().all()
        return [_tenant_to_domain(r) for r in rows]

    async def list_subscriptions(self, tenant_id: str) -> list[Subscription]:
        if not tenant_id.strip():
            return []
        stmt = select(SubscriptionRow).where(SubscriptionRow.tenant_id == tenant_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_subscription_to_domain(r) for r in rows]

    async def list_platform_accounts(self, tenant_id: str) -> list[PlatformAccount]:
        if not tenant_id.strip():
            return []
        stmt = select(PlatformAccountRow).where(PlatformAccountRow.tenant_id == tenant_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_account_to_domain(r) for r in rows]

    async def list_monitoring_targets(self, tenant_id: str) -> list[MonitoringTarget]:
        if not tenant_id.strip():
            return []
        stmt = select(MonitoringTargetRow).where(MonitoringTargetRow.tenant_id == tenant_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_target_to_domain(r) for r in rows]

    async def list_messenger_channels(self, tenant_id: str) -> list[MessengerChannel]:
        if not tenant_id.strip():
            return []
        stmt = select(MessengerChannelRow).where(MessengerChannelRow.tenant_id == tenant_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_channel_to_domain(r) for r in rows]

    async def list_delivery_rules(self, target_id: str) -> list[DeliveryRule]:
        stmt = select(DeliveryRuleRow).where(DeliveryRuleRow.target_id == target_id)
        async with self._session_factory() as session:
            rows = (await session.execute(stmt)).scalars().all()
        return [_rule_to_domain(r) for r in rows]

    async def tenant_has_dependencies(self, tenant_id: str) -> bool:
        tenant_uuid = _uuid(tenant_id)
        dependency_queries = (
            select(SubscriptionRow.id)
            .where(SubscriptionRow.tenant_id == tenant_uuid)
            .limit(1),
            select(PlatformAccountRow.id)
            .where(PlatformAccountRow.tenant_id == tenant_uuid)
            .limit(1),
            select(MonitoringTargetRow.id)
            .where(MonitoringTargetRow.tenant_id == tenant_uuid)
            .limit(1),
            select(MessengerChannelRow.id)
            .where(MessengerChannelRow.tenant_id == tenant_uuid)
            .limit(1),
        )
        async with self._session_factory() as session:
            for stmt in dependency_queries:
                if (await session.execute(stmt)).first() is not None:
                    return True
        return False

    async def monitoring_target_has_dependencies(self, target_id: str) -> bool:
        target_uuid = _uuid(target_id)
        dependency_queries = (
            select(DeliveryRuleRow.id)
            .where(DeliveryRuleRow.target_id == target_uuid)
            .limit(1),
            select(SnapshotRow.id).where(SnapshotRow.target_id == target_uuid).limit(1),
            select(JobRow.id).where(JobRow.target_id == target_uuid).limit(1),
            select(BrowserProfileRow.id)
            .where(BrowserProfileRow.target_id == target_uuid)
            .limit(1),
        )
        async with self._session_factory() as session:
            for stmt in dependency_queries:
                if (await session.execute(stmt)).first() is not None:
                    return True
        return False

    # ── create(신규 INSERT + audit, 동일 트랜잭션) ──────────────────────────────
    async def create_tenant(self, tenant: Tenant, audit: AuditEntry) -> None:
        values = {
            "id": _uuid(tenant.id),
            "name": tenant.name,
            "status": tenant.status.value,
            "created_at": tenant.created_at,
            "telegram_bot_token": tenant.telegram_bot_token,
            "telegram_webhook_secret": tenant.telegram_webhook_secret,
            "sending_enabled": tenant.sending_enabled,
            "send_test_passed_at": tenant.send_test_passed_at,
        }
        await self._insert_with_audit(TenantRow, values, audit)

    async def create_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None:
        values = {
            "id": _uuid(subscription.id),
            "tenant_id": _uuid(subscription.tenant_id),
            "plan": subscription.plan,
            "status": subscription.status.value,
            "current_period_end": subscription.current_period_end,
            "quotas": subscription.quotas,
        }
        await self._insert_with_audit(SubscriptionRow, values, audit)

    async def create_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None:
        values = {
            "id": _uuid(account.id),
            "tenant_id": _uuid(account.tenant_id),
            "platform": account.platform.value,
            "label": account.label,
            "username": account.username,
            "password": account.password,
            "verification_email_address": account.verification_email_address,
            "verification_email_app_password": account.verification_email_app_password,
            "verification_email_subject_keyword": account.verification_email_subject_keyword,
            "verification_email_sender_keyword": account.verification_email_sender_keyword,
            "auth_state": account.auth_state.value,
        }
        await self._insert_with_audit(PlatformAccountRow, values, audit)

    async def create_monitoring_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None:
        values = {
            "id": _uuid(target.id),
            "tenant_id": _uuid(target.tenant_id),
            "platform_account_id": _uuid(target.platform_account_id),
            "name": target.name,
            "center_name": target.center_name,
            "external_id": target.external_id,
            "url": target.url,
            "interval_minutes": target.interval_minutes,
            "schedule_enabled": target.schedule_enabled,
            "start_time": target.start_time,
            "stop_time": target.stop_time,
            "status": target.status.value,
        }
        await self._insert_with_audit(MonitoringTargetRow, values, audit)

    async def create_messenger_channel(
        self,
        channel: MessengerChannel,
        audit: AuditEntry,
        *,
        registration_code: str | None = None,
    ) -> None:
        values = {
            "id": _uuid(channel.id),
            "tenant_id": _uuid(channel.tenant_id),
            "messenger": channel.messenger.value,
            "telegram_chat_id": channel.telegram_chat_id,
            "thread_id": channel.thread_id,
            "kakao_room_name": channel.kakao_room_name,
            "state": channel.state.value,
            "registration_code": registration_code,  # 라우팅 코드(비domain, secret 아님)
        }
        await self._insert_with_audit(MessengerChannelRow, values, audit)

    async def create_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None:
        try:
            async with self._session_factory() as session:
                tenant_id = (
                    await session.execute(
                        select(MonitoringTargetRow.tenant_id).where(
                            MonitoringTargetRow.id == _uuid(rule.target_id)
                        )
                    )
                ).scalar_one()
                await session.execute(
                    insert(DeliveryRuleRow).values(
                        id=_uuid(rule.id),
                        tenant_id=tenant_id,
                        target_id=_uuid(rule.target_id),
                        channel_id=_uuid(rule.channel_id),
                        template_id=rule.template_id,
                        enabled=rule.enabled,
                        send_only_on_change=rule.send_only_on_change,
                    )
                )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc

    # ── save(UPDATE + audit, 동일 트랜잭션) ─────────────────────────────────────
    async def save_tenant(
        self,
        tenant: Tenant,
        audit: AuditEntry,
        *,
        schedule_resets: dict[str, datetime] | None = None,
    ) -> None:
        values = {
            "name": tenant.name,
            "status": tenant.status.value,
            "telegram_bot_token": tenant.telegram_bot_token,
            "telegram_webhook_secret": tenant.telegram_webhook_secret,
            "sending_enabled": tenant.sending_enabled,
            "send_test_passed_at": tenant.send_test_passed_at,
        }
        # no-catchup: 고객 reactivation 시 tenant UPDATE + ACTIVE targets next_run_at reset + audit
        # 를 **한 세션·한 commit** 으로 묶는다(부분 반영 없음). last_enqueued_at/last_success_at 은
        # 건드리지 않는다(실제 enqueue 아님, 성공 이력은 snapshots 파생).
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(TenantRow).where(TenantRow.id == tenant.id).values(**values)
                )
                for target_id, next_run_at in (schedule_resets or {}).items():
                    await session.execute(
                        update(MonitoringTargetRow)
                        .where(MonitoringTargetRow.id == _uuid(target_id))
                        .values(next_run_at=next_run_at)
                    )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc

    async def save_subscription(
        self,
        subscription: Subscription,
        audit: AuditEntry,
        *,
        schedule_resets: dict[str, datetime] | None = None,
    ) -> None:
        values = {
            "plan": subscription.plan,
            "status": subscription.status.value,
            "current_period_end": subscription.current_period_end,
            "quotas": subscription.quotas,
        }
        # no-catchup: 구독 복구 시 subscription UPDATE + tenant 의 ACTIVE targets next_run_at reset
        # + audit 를 **한 세션·한 commit** 으로 묶는다(부분 반영 없음). schedule_resets 없으면 기존
        # 단일 UPDATE 동작과 동일하다.
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(SubscriptionRow)
                    .where(SubscriptionRow.id == subscription.id)
                    .values(**values)
                )
                for target_id, next_run_at in (schedule_resets or {}).items():
                    await session.execute(
                        update(MonitoringTargetRow)
                        .where(MonitoringTargetRow.id == _uuid(target_id))
                        .values(next_run_at=next_run_at)
                    )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc

    async def save_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None:
        await self._update_with_audit(
            PlatformAccountRow,
            account.id,
            {
                "label": account.label,
                "username": account.username,
                "password": account.password,
                "verification_email_address": account.verification_email_address,
                "verification_email_app_password": account.verification_email_app_password,
                "verification_email_subject_keyword": account.verification_email_subject_keyword,
                "verification_email_sender_keyword": account.verification_email_sender_keyword,
            },
            audit,
        )

    async def save_monitoring_target(
        self,
        target: MonitoringTarget,
        audit: AuditEntry,
        *,
        schedule_reset_to: datetime | None = None,
    ) -> None:
        values = {
            "name": target.name,
            "center_name": target.center_name,
            "external_id": target.external_id,
            "url": target.url,
            "interval_minutes": target.interval_minutes,
            "schedule_enabled": target.schedule_enabled,
            "start_time": target.start_time,
            "stop_time": target.stop_time,
            "status": target.status.value,  # soft delete = INACTIVE 포함
        }
        # no-catchup: CRUD reactivation(INACTIVE→ACTIVE)은 status 와 같은 UPDATE 로 next_run_at 을
        # 민다(같은 트랜잭션). 일반 편집(schedule_reset_to=None)은 next_run_at 을 건드리지 않는다.
        if schedule_reset_to is not None:
            values["next_run_at"] = schedule_reset_to
        await self._update_with_audit(
            MonitoringTargetRow,
            target.id,
            values,
            audit,
        )

    async def save_messenger_channel(
        self, channel: MessengerChannel, audit: AuditEntry
    ) -> None:
        await self._update_with_audit(
            MessengerChannelRow,
            channel.id,
            {
                "telegram_chat_id": channel.telegram_chat_id,
                "thread_id": channel.thread_id,
                "kakao_room_name": channel.kakao_room_name,
                "state": channel.state.value,  # soft delete = INACTIVE 포함
            },
            audit,
        )

    async def save_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None:
        await self._update_with_audit(
            DeliveryRuleRow,
            rule.id,
            {
                "template_id": rule.template_id,
                "enabled": rule.enabled,  # soft delete = False 포함
                "send_only_on_change": rule.send_only_on_change,
            },
            audit,
        )

    # ── delete(물리 DELETE + audit, 동일 트랜잭션) ──────────────────────────────
    async def delete_tenant(self, tenant_id: str, audit: AuditEntry) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    delete(TenantRow).where(TenantRow.id == _uuid(tenant_id))
                )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise AdminEntityDeleteBlockedError(
                "연결 데이터가 있어 고객을 삭제할 수 없습니다"
            ) from exc

    async def delete_monitoring_target(
        self, target_id: str, audit: AuditEntry
    ) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    delete(MonitoringTargetRow).where(
                        MonitoringTargetRow.id == _uuid(target_id)
                    )
                )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise AdminEntityDeleteBlockedError(
                "연결 데이터가 있어 업체를 삭제할 수 없습니다"
            ) from exc

    # ── 공통: INSERT/UPDATE + audit INSERT 를 한 세션·한 commit 으로 ─────────────
    async def _insert_with_audit(self, row_cls, values: dict, audit: AuditEntry) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(insert(row_cls).values(**values))
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc

    async def _update_with_audit(
        self, row_cls, entity_id: str, values: dict, audit: AuditEntry
    ) -> None:
        try:
            async with self._session_factory() as session:
                await session.execute(
                    update(row_cls).where(row_cls.id == entity_id).values(**values)
                )
                await session.execute(insert(AuditLogRow).values(**_audit_values(audit)))
                await session.commit()
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc


def _duplicate_error(exc: IntegrityError) -> AdminEntityDuplicateError:
    text = str(getattr(exc, "orig", exc))
    lowered = text.lower()
    if "registration_code" in lowered:
        return AdminEntityDuplicateError("registration_code", "중복된 채널 등록 코드입니다")
    if "telegram" in lowered or "chat" in lowered or "messenger_channels" in lowered:
        return AdminEntityDuplicateError("messenger_channel", "중복된 메시지 채널입니다")
    return AdminEntityDuplicateError("unique", "중복된 값입니다")
