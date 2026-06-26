"""Admin 엔티티 생성/편집 CRUD 오케스트레이션 — Story 5.11 (AC1·AC2·AC3·AC4).

5.6 읽기 전용 대시보드·5.7 운영 액션(상태 토글) 위에 **엔티티 CRUD**(생성/편집/비활성화)를
얹는다. 핵심은 새 정책을 만들기보다 기존 5.5~5.8 seam 을 wiring 하는 것이다 — 단, **CREATE
(INSERT) 경로만은 코드베이스 어디에도 없어 신규 작성**한다(5.7 ``transition_*`` 는 UPDATE-only,
``PostgresChannelRepository.save`` 도 UPDATE-only).

**쓰기 경계(architecture #Service-Boundaries):** 엔티티 write 는 **이 service(+repository)에서만**
일어난다 — 라우트/템플릿은 이 service 만 호출한다(``admin/crud_routes.py``). 영속은
:class:`AdminEntityRepository` 포트(in-memory fake / PostgreSQL)가 담당하고, **모든 write 는
엔티티 write + audit INSERT 를 같은 트랜잭션** 으로 묶는다(AC4 — 액션 성공·audit 누락 불가,
5.7 ``transition_target(entity, audit)`` 선례).

**재사용(재구현 금지):**
  * audit diff/redaction = 5.7 :func:`build_diff_redacted` + :class:`AuditEntry`/:class:`AuditResult`.
  * tenant scope = 5.7 ``_scoped_*`` 패턴(load 후 ``tenant_id`` 불일치 → :class:`TenantScopeViolation`
    = ``AdminActionNotFound`` 하위 → 404, 존재 누설 방지).
  * center_name 위험 판정 = 쿠팡 기대 센터/상점명 정본(``rider_crawl.config.DEFAULT_BAEMIN_CENTER_NAME``).

**결정성(5.7 규약):** 내부에서 ``datetime.now()`` 를 호출하지 않는다 — 시각 ``at`` 과 신규 ``id``
는 호출부(라우트=실 ``now()``/``uuid4``, 테스트=고정값) 주입. 단방향 import: ``rider_server`` →
``rider_crawl`` 만, ``rider_agent`` import 0. domain 은 SQLAlchemy import 0.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from rider_server.domain import (
    AuditResult,
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
from rider_server.services.admin_action_service import (
    AdminActionNotFound,
    AuditEntry,
    TARGET_TYPE_CHANNEL,
    TARGET_TYPE_TARGET,
    TenantScopeViolation,
)
from rider_server.services.admin_entities.common import (
    ACTION_DELIVERY_RULE_CREATE,
    ACTION_DELIVERY_RULE_DEACTIVATE,
    ACTION_DELIVERY_RULE_UPDATE,
    ACTION_MESSENGER_CHANNEL_ACTIVATE,
    ACTION_MESSENGER_CHANNEL_CREATE,
    ACTION_MESSENGER_CHANNEL_DEACTIVATE,
    ACTION_MESSENGER_CHANNEL_UPDATE,
    ACTION_PLATFORM_ACCOUNT_CREATE,
    ACTION_PLATFORM_ACCOUNT_UPDATE,
    ACTION_SUBSCRIPTION_CREATE,
    ACTION_SUBSCRIPTION_UPDATE,
    AdminEntityDeleteBlockedError,
    AdminEntityDuplicateError,
    AdminEntityRepository,
    DEFAULT_VERIFICATION_EMAIL_SENDER_KEYWORD,
    DEFAULT_VERIFICATION_EMAIL_SUBJECT_KEYWORD,
    TARGET_TYPE_DELIVERY_RULE,
    TARGET_TYPE_PLATFORM_ACCOUNT,
    TARGET_TYPE_SUBSCRIPTION,
    TARGET_TYPE_TENANT,
    TargetWriteResult,
    _keyword_or_default,
    _secret_change_label,
    _secret_ref_or_empty,
    build_admin_audit,
    is_center_name_risky,
    scoped_channel,
    scoped_platform_account,
    scoped_rule,
    scoped_subscription,
    scoped_target,
    scoped_tenant,
)
from rider_server.services.admin_entities.target_service import TargetAdminEntityService
from rider_server.services.admin_entities.tenant_service import (
    _UNSET as _UNSET_SEND_TEST,
    TenantAdminEntityService,
)
from rider_server.services.channel_registration import (
    assert_channel_transition,
    assert_unique_kakao_rooms,
    assert_unique_telegram_topics,
)


# ══════════════════════════════════════════════════════════════════════════
# 엔티티 CRUD service(write 단일 소유처 — 라우트는 이것만 호출)
# ══════════════════════════════════════════════════════════════════════════

class AdminEntityService:
    """5개 엔티티(고객/플랫폼 계정/모니터링 대상/메시지 채널/전송 규칙) CRUD 오케스트레이션.

    write 는 entity write + audit 를 repository 가 같은 트랜잭션으로 persist 한다(AC4). tenant
    scope·secret 위생·center_name 위험 경고·soft delete 상태값은 이 service 가 강제한다.
    """

    def __init__(self, repository: AdminEntityRepository) -> None:
        self._repo = repository
        self._tenant_service = TenantAdminEntityService(
            repository,
            self._audit,
            self._scoped_tenant,
        )
        self._target_service = TargetAdminEntityService(
            repository,
            self._audit,
            self._scoped_target,
            self._scoped_platform_account,
        )

    # ── 조회(list) — 라우트는 service 만 호출(repo 직접 접근 금지) ─────────────────
    async def list_tenants(self) -> list[Tenant]:
        return await self._repo.list_tenants()

    async def list_subscriptions(self, tenant_id: str) -> list[Subscription]:
        return await self._repo.list_subscriptions(tenant_id)

    async def list_platform_accounts(self, tenant_id: str) -> list[PlatformAccount]:
        return await self._repo.list_platform_accounts(tenant_id)

    async def list_monitoring_targets(self, tenant_id: str) -> list[MonitoringTarget]:
        return await self._repo.list_monitoring_targets(tenant_id)

    async def list_messenger_channels(self, tenant_id: str) -> list[MessengerChannel]:
        return await self._repo.list_messenger_channels(tenant_id)

    async def get_messenger_channel(self, channel_id: str) -> MessengerChannel | None:
        """채널 단건 조회(전송 테스트 seam 용 — scope 는 호출부가 강제)."""
        return await self._repo.get_messenger_channel(channel_id)

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        """tenant 단건 조회(전송 테스트 게이트 상태 표시 seam 용)."""
        return await self._repo.get_tenant(tenant_id)

    async def list_delivery_rules(
        self, target_id: str, *, tenant_id: str
    ) -> list[DeliveryRule]:
        """전송 규칙 목록(대상별) — 다른 list_* 와 동일하게 tenant scope 를 강제한다(AC3 조회 격리).

        DeliveryRule 은 직접 ``tenant_id`` 가 없어 ``target_id``→target.tenant_id 로 scope 를
        도출한다(write 경로의 :meth:`_scoped_rule` 와 동형). 대상이 없거나 요청 tenant 소유가
        아니면 **빈 목록** 으로 반환해 cross-tenant 존재/규칙을 노출하지 않는다(404 동급, 누설 방지).
        """

        target = await self._repo.get_monitoring_target(target_id)
        if target is None or target.tenant_id != tenant_id:
            return []
        return await self._repo.list_delivery_rules(target_id)

    # ── 내부: audit 합성(5.7 _audit 패턴 — diff_redacted 통과) ─────────────────
    @staticmethod
    def _audit(
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
        return build_admin_audit(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            at=at,
            diff=diff,
            source=source,
            reason=reason,
            result=result,
        )

    # ── 내부: tenant scope 검증(cross-tenant 누출 차단 — 5.7 _scoped_* 패턴) ─────
    async def _scoped_tenant(self, tenant_id: str) -> Tenant:
        return await scoped_tenant(self._repo, tenant_id)

    async def _scoped_subscription(
        self, subscription_id: str, *, tenant_id: str
    ) -> Subscription:
        return await scoped_subscription(self._repo, subscription_id, tenant_id=tenant_id)

    async def _scoped_platform_account(
        self, account_id: str, *, tenant_id: str
    ) -> PlatformAccount:
        return await scoped_platform_account(self._repo, account_id, tenant_id=tenant_id)

    async def _scoped_target(self, target_id: str, *, tenant_id: str) -> MonitoringTarget:
        return await scoped_target(self._repo, target_id, tenant_id=tenant_id)

    async def _scoped_channel(self, channel_id: str, *, tenant_id: str) -> MessengerChannel:
        return await scoped_channel(self._repo, channel_id, tenant_id=tenant_id)

    async def _scoped_rule(
        self, rule_id: str, *, tenant_id: str
    ) -> tuple[DeliveryRule, MonitoringTarget]:
        """DeliveryRule 은 직접 ``tenant_id`` 가 없어 ``target_id``→target.tenant_id 로 scope 도출."""

        return await scoped_rule(self._repo, rule_id, tenant_id=tenant_id)

    # ══════════════════════════════════════════════════════════════════════
    # 고객 Tenant — create/update(루트, 생성 시 새 tenant_id 발급)
    # ══════════════════════════════════════════════════════════════════════
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
        """신규 고객을 생성한다 — ``entity_id`` 가 새 tenant_id(루트, scope 검사 없음)."""

        return await self._tenant_service.create_tenant(
            entity_id=entity_id,
            name=name,
            at=at,
            actor_id=actor_id,
            status=status,
            source=source,
            reason=reason,
        )

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        status: CustomerLifecycleState | None = None,
        telegram_bot_token: str | None = None,
        telegram_webhook_secret: str | None = None,
        sending_enabled: bool | None = None,
        send_test_passed_at: datetime | None = _UNSET_SEND_TEST,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        """고객명/lifecycle 상태 + tenant 별 텔레그램 설정을 편집한다(scope = 자신의 id).

        텔레그램 봇 토큰/webhook secret 은 ``None`` 이면 기존 값을 유지하고(빈 문자열은 명시
        삭제로 취급), 평문 저장한다(0011 선례). audit diff 에는 secret 값을 절대 싣지 않고 변경
        여부(set/cleared/unchanged)만 기록한다. ``sending_enabled`` 는 ``None`` 이면 유지한다.
        ``send_test_passed_at`` 은 sentinel 이면 유지(채널 전송 테스트 통과 시각, 게이트 해제 조건).
        """

        return await self._tenant_service.update_tenant(
            tenant_id,
            name=name,
            status=status,
            telegram_bot_token=telegram_bot_token,
            telegram_webhook_secret=telegram_webhook_secret,
            sending_enabled=sending_enabled,
            send_test_passed_at=send_test_passed_at,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

    async def delete_tenant(
        self,
        tenant_id: str,
        *,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Tenant:
        """고객 물리 삭제 — 연결 데이터가 하나라도 있으면 삭제하지 않는다."""

        return await self._tenant_service.delete_tenant(
            tenant_id,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

    # ══════════════════════════════════════════════════════════════════════
    # 구독 Subscription — create/update(status)
    # ══════════════════════════════════════════════════════════════════════
    async def create_subscription(
        self,
        *,
        entity_id: str,
        tenant_id: str,
        plan: str,
        status: SubscriptionStatus,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Subscription:
        """고객 구독을 생성한다. scheduler 는 이 상태와 tenant lifecycle 을 같이 본다."""

        await self._scoped_tenant(tenant_id)
        normalized_plan = (plan or "").strip() or "basic"
        subscription = Subscription(
            id=entity_id,
            tenant_id=tenant_id,
            plan=normalized_plan,
            status=status,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_CREATE,
            target_type=TARGET_TYPE_SUBSCRIPTION,
            target_id=entity_id,
            at=at,
            diff={
                "op": "create",
                "tenant_id": tenant_id,
                "plan": normalized_plan,
                "to_status": status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.create_subscription(subscription, audit)
        return subscription

    async def update_subscription(
        self,
        subscription_id: str,
        *,
        tenant_id: str,
        status: SubscriptionStatus,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> Subscription:
        """구독 실행 게이트 상태를 편집한다."""

        existing = await self._scoped_subscription(subscription_id, tenant_id=tenant_id)
        updated = replace(existing, status=status)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_UPDATE,
            target_type=TARGET_TYPE_SUBSCRIPTION,
            target_id=subscription_id,
            at=at,
            diff={
                "from_status": existing.status.value,
                "to_status": updated.status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_subscription(updated, audit)
        return updated

    # ══════════════════════════════════════════════════════════════════════
    # 플랫폼 계정 PlatformAccount — create/update(secret성 값은 ref 핸들만 저장)
    # ══════════════════════════════════════════════════════════════════════
    async def create_platform_account(
        self,
        *,
        entity_id: str,
        tenant_id: str,
        platform: Platform,
        label: str,
        username: str = "",
        password: str = "",
        at: datetime,
        actor_id: str | None,
        verification_email_address: str = "",
        verification_email_app_password: str = "",
        verification_email_subject_keyword: str = DEFAULT_VERIFICATION_EMAIL_SUBJECT_KEYWORD,
        verification_email_sender_keyword: str = DEFAULT_VERIFICATION_EMAIL_SENDER_KEYWORD,
        source: str | None = None,
        reason: str | None = None,
    ) -> PlatformAccount:
        """플랫폼 계정을 생성한다 — secret성 값은 ref 핸들만 저장한다."""

        await self._scoped_tenant(tenant_id)  # 부모 tenant 존재/scope 확인
        email_subject_keyword = _keyword_or_default(
            verification_email_subject_keyword,
            DEFAULT_VERIFICATION_EMAIL_SUBJECT_KEYWORD,
        )
        email_sender_keyword = _keyword_or_default(
            verification_email_sender_keyword,
            DEFAULT_VERIFICATION_EMAIL_SENDER_KEYWORD,
        )
        account = PlatformAccount(
            id=entity_id,
            tenant_id=tenant_id,
            platform=platform,
            label=label,
            username=_secret_ref_or_empty(username, "username"),
            password=_secret_ref_or_empty(password, "password"),
            verification_email_address=_secret_ref_or_empty(
                verification_email_address, "verification_email_address"
            ),
            verification_email_app_password=_secret_ref_or_empty(
                verification_email_app_password,
                "verification_email_app_password",
            ),
            verification_email_subject_keyword=email_subject_keyword,
            verification_email_sender_keyword=email_sender_keyword,
            auth_state=BaeminAuthState.UNKNOWN,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_PLATFORM_ACCOUNT_CREATE,
            target_type=TARGET_TYPE_PLATFORM_ACCOUNT,
            target_id=entity_id,
            at=at,
            diff={
                "op": "create",
                "platform": platform.value,
                "label": label,
                "verification_email_subject_keyword": email_subject_keyword,
                "verification_email_sender_keyword": email_sender_keyword,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.create_platform_account(account, audit)
        return account

    async def update_platform_account(
        self,
        account_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        label: str | None = None,
        username: str | None = None,
        password: str | None = None,
        verification_email_address: str | None = None,
        verification_email_app_password: str | None = None,
        verification_email_subject_keyword: str | None = None,
        verification_email_sender_keyword: str | None = None,
        source: str | None = None,
        reason: str | None = None,
    ) -> PlatformAccount:
        """플랫폼 계정 라벨/자격증명을 편집한다(secret성 값은 ref 핸들만 저장)."""

        existing = await self._scoped_platform_account(account_id, tenant_id=tenant_id)
        new_label = label if label is not None and label.strip() else existing.label
        new_username = (
            _secret_ref_or_empty(username, "username")
            if username is not None and username.strip()
            else existing.username
        )
        new_password = (
            _secret_ref_or_empty(password, "password")
            if password is not None and password.strip()
            else existing.password
        )
        new_email_address = (
            _secret_ref_or_empty(
                verification_email_address, "verification_email_address"
            )
            if verification_email_address is not None and verification_email_address.strip()
            else existing.verification_email_address
        )
        new_email_app_password = (
            _secret_ref_or_empty(
                verification_email_app_password,
                "verification_email_app_password",
            )
            if verification_email_app_password is not None
            and verification_email_app_password.strip()
            else existing.verification_email_app_password
        )
        new_email_subject_keyword = (
            _keyword_or_default(
                verification_email_subject_keyword,
                existing.verification_email_subject_keyword,
            )
            if verification_email_subject_keyword is not None
            else existing.verification_email_subject_keyword
        )
        new_email_sender_keyword = (
            _keyword_or_default(
                verification_email_sender_keyword,
                existing.verification_email_sender_keyword,
            )
            if verification_email_sender_keyword is not None
            else existing.verification_email_sender_keyword
        )
        updated = replace(
            existing,
            label=new_label,
            username=new_username,
            password=new_password,
            verification_email_address=new_email_address,
            verification_email_app_password=new_email_app_password,
            verification_email_subject_keyword=new_email_subject_keyword,
            verification_email_sender_keyword=new_email_sender_keyword,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_PLATFORM_ACCOUNT_UPDATE,
            target_type=TARGET_TYPE_PLATFORM_ACCOUNT,
            target_id=account_id,
            at=at,
            diff={
                "from_label": existing.label,
                "to_label": updated.label,
                "password_change": _secret_change_label(existing.password, updated.password),
                "verification_email_app_password_change": _secret_change_label(
                    existing.verification_email_app_password,
                    updated.verification_email_app_password,
                ),
                "verification_email_subject_keyword": updated.verification_email_subject_keyword,
                "verification_email_sender_keyword": updated.verification_email_sender_keyword,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_platform_account(updated, audit)
        return updated

    # ══════════════════════════════════════════════════════════════════════
    # 모니터링 대상 MonitoringTarget — create/update/deactivate(soft delete=INACTIVE)
    # ══════════════════════════════════════════════════════════════════════
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
        schedule_enabled: bool = False,
        start_time: str = "",
        stop_time: str = "",
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> TargetWriteResult:
        """모니터링 대상을 생성한다 — 연결 계정의 플랫폼으로 center_name 위험을 경고한다(차단 아님)."""

        return await self._target_service.create_monitoring_target(
            entity_id=entity_id,
            tenant_id=tenant_id,
            platform_account_id=platform_account_id,
            name=name,
            center_name=center_name,
            external_id=external_id,
            url=url,
            interval_minutes=interval_minutes,
            schedule_enabled=schedule_enabled,
            start_time=start_time,
            stop_time=stop_time,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

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
        schedule_enabled: bool | None = None,
        start_time: str | None = None,
        stop_time: str | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> TargetWriteResult:
        """모니터링 대상 필드를 편집한다(center_name 변경 시 위험 재판정)."""

        return await self._target_service.update_monitoring_target(
            target_id,
            tenant_id=tenant_id,
            name=name,
            center_name=center_name,
            external_id=external_id,
            url=url,
            interval_minutes=interval_minutes,
            schedule_enabled=schedule_enabled,
            start_time=start_time,
            stop_time=stop_time,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

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
        """대상 soft delete — ``status=INACTIVE``(물리 삭제 0, FR-4). 5.11 이 INACTIVE 전이 신규 소유.

        비활성 대상은 자동 재활성화되지 않는다(FR-31 — 재활성화는 명시적 운영자 액션). 이미 INACTIVE
        면 멱등하게 audit 만 남기지 않고 그대로 반환한다(중복 비활성 no-op).
        """

        return await self._target_service.deactivate_monitoring_target(
            target_id,
            tenant_id=tenant_id,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

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
        """INACTIVE soft-delete 상태의 대상을 명시적으로 복구한다."""

        return await self._target_service.reactivate_monitoring_target(
            target_id,
            tenant_id=tenant_id,
            at=at,
            actor_id=actor_id,
            source=source,
            reason=reason,
        )

    # ══════════════════════════════════════════════════════════════════════
    # 메시지 채널 MessengerChannel — create/update(라우팅)/deactivate(INACTIVE)
    # ══════════════════════════════════════════════════════════════════════
    async def create_messenger_channel(
        self,
        *,
        entity_id: str,
        tenant_id: str,
        messenger: Messenger,
        telegram_chat_id: str | None = None,
        thread_id: str | None = None,
        kakao_room_name: str | None = None,
        registration_code: str | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MessengerChannel:
        """신규 채널을 생성한다.

        Telegram 은 register/verify/activate 흐름을 거치므로 ``PENDING`` 으로 만들고, Kakao 는 방명이
        있으면 추가 등록 handshake 가 없어 바로 ``ACTIVE`` 로 만든다.
        ``telegram_chat_id``/``thread_id``/``kakao_room_name`` 은 라우팅 식별자라 secret 아님(ref화 0).
        """

        await self._scoped_tenant(tenant_id)
        initial_state = (
            MessengerChannelState.ACTIVE
            if messenger is Messenger.KAKAO and (kakao_room_name or "").strip()
            else MessengerChannelState.PENDING
        )
        channel = MessengerChannel(
            id=entity_id,
            tenant_id=tenant_id,
            messenger=messenger,
            telegram_chat_id=telegram_chat_id or None,
            thread_id=thread_id or None,
            kakao_room_name=kakao_room_name or None,
            state=initial_state,
        )
        if channel.state is MessengerChannelState.ACTIVE and channel.messenger is Messenger.KAKAO:
            active_channels = [
                existing
                for existing in await self._repo.list_messenger_channels(tenant_id)
                if existing.state is MessengerChannelState.ACTIVE
            ]
            assert_unique_kakao_rooms([*active_channels, channel])
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MESSENGER_CHANNEL_CREATE,
            target_type=TARGET_TYPE_CHANNEL,
            target_id=entity_id,
            at=at,
            diff={
                "op": "create",
                "messenger": messenger.value,
                "to_state": channel.state.value,
                # chat_id/방명은 redact/build_diff_redacted 가 운영 식별자로 마스킹.
                "telegram_chat_id": channel.telegram_chat_id,
                "kakao_room_name": channel.kakao_room_name,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.create_messenger_channel(
            channel, audit, registration_code=registration_code
        )
        return channel

    async def update_messenger_channel(
        self,
        channel_id: str,
        *,
        tenant_id: str,
        telegram_chat_id: str | None = None,
        thread_id: str | None = None,
        kakao_room_name: str | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MessengerChannel:
        """채널 라우팅 필드(chat_id/thread_id/방명)를 편집한다.

        PENDING Kakao 채널은 방명이 채워지면 별도 handshake 없이 ACTIVE 로 전환한다.
        """

        existing = await self._scoped_channel(channel_id, tenant_id=tenant_id)
        next_kakao_room_name = (
            kakao_room_name if kakao_room_name is not None else existing.kakao_room_name
        )
        next_state = (
            MessengerChannelState.ACTIVE
            if (
                existing.messenger is Messenger.KAKAO
                and existing.state is MessengerChannelState.PENDING
                and (next_kakao_room_name or "").strip()
            )
            else existing.state
        )
        updated = replace(
            existing,
            telegram_chat_id=(
                telegram_chat_id if telegram_chat_id is not None else existing.telegram_chat_id
            ),
            thread_id=thread_id if thread_id is not None else existing.thread_id,
            kakao_room_name=next_kakao_room_name,
            state=next_state,
        )
        if updated.state is MessengerChannelState.ACTIVE and updated.messenger is Messenger.KAKAO:
            active_channels = [
                channel
                for channel in await self._repo.list_messenger_channels(tenant_id)
                if channel.state is MessengerChannelState.ACTIVE and channel.id != updated.id
            ]
            assert_unique_kakao_rooms([*active_channels, updated])
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MESSENGER_CHANNEL_UPDATE,
            target_type=TARGET_TYPE_CHANNEL,
            target_id=channel_id,
            at=at,
            diff={
                "messenger": existing.messenger.value,
                "telegram_chat_id": updated.telegram_chat_id,
                "thread_id": updated.thread_id,
                "kakao_room_name": updated.kakao_room_name,
                "from_state": existing.state.value,
                "to_state": updated.state.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_messenger_channel(updated, audit)
        return updated

    async def activate_messenger_channel_manual(
        self,
        channel_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MessengerChannel:
        """운영자가 라우팅을 확인한 PENDING/VERIFIED 채널을 ACTIVE 로 전환한다."""

        existing = await self._scoped_channel(channel_id, tenant_id=tenant_id)
        if existing.state not in (
            MessengerChannelState.PENDING,
            MessengerChannelState.VERIFIED,
        ):
            raise ValueError("PENDING 또는 VERIFIED 채널만 수동 활성화할 수 있습니다")
        if existing.messenger is Messenger.TELEGRAM and not (
            existing.telegram_chat_id or ""
        ).strip():
            raise ValueError("Telegram 채널 활성화에는 telegram_chat_id가 필요합니다")
        if existing.messenger is Messenger.KAKAO and not (
            existing.kakao_room_name or ""
        ).strip():
            raise ValueError("Kakao 채널 활성화에는 kakao_room_name이 필요합니다")

        updated = replace(existing, state=MessengerChannelState.ACTIVE)
        active_channels = [
            channel
            for channel in await self._repo.list_messenger_channels(tenant_id)
            if channel.state is MessengerChannelState.ACTIVE and channel.id != updated.id
        ]
        if updated.messenger is Messenger.TELEGRAM:
            assert_unique_telegram_topics([*active_channels, updated])
        elif updated.messenger is Messenger.KAKAO:
            assert_unique_kakao_rooms([*active_channels, updated])

        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MESSENGER_CHANNEL_ACTIVATE,
            target_type=TARGET_TYPE_CHANNEL,
            target_id=channel_id,
            at=at,
            diff={
                "messenger": existing.messenger.value,
                "telegram_chat_id": updated.telegram_chat_id,
                "thread_id": updated.thread_id,
                "kakao_room_name": updated.kakao_room_name,
                "from_state": existing.state.value,
                "to_state": updated.state.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_messenger_channel(updated, audit)
        return updated

    async def deactivate_messenger_channel(
        self,
        channel_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> MessengerChannel:
        """채널 soft delete — ``state=INACTIVE``. 5.5 상태머신(``assert_channel_transition``) 재사용.

        register/verify/activate 전이표를 재구현하지 않고 5.5 의 전이 허용표를 그대로 통과시킨 뒤
        ``→INACTIVE`` 로 둔다(미정의 전이는 :class:`InvalidChannelTransition`=``ValueError``→400).
        audit 를 같은 트랜잭션으로 묶기 위해 entity repo ``save`` 경유로 영속한다(AC4).
        """

        existing = await self._scoped_channel(channel_id, tenant_id=tenant_id)
        assert_channel_transition(existing.state, MessengerChannelState.INACTIVE)
        updated = replace(existing, state=MessengerChannelState.INACTIVE)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_MESSENGER_CHANNEL_DEACTIVATE,
            target_type=TARGET_TYPE_CHANNEL,
            target_id=channel_id,
            at=at,
            diff={
                "from_state": existing.state.value,
                "to_state": updated.state.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_messenger_channel(updated, audit)
        return updated

    # ══════════════════════════════════════════════════════════════════════
    # 전송 규칙 DeliveryRule — create(1:N fan-out)/update/deactivate(enabled=False)
    # ══════════════════════════════════════════════════════════════════════
    async def create_delivery_rule(
        self,
        *,
        entity_id: str,
        tenant_id: str,
        target_id: str,
        channel_id: str,
        template_id: str = "",
        send_only_on_change: bool = False,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> DeliveryRule:
        """대상→채널 전송 규칙을 생성한다 — 같은 ``target_id`` 에 다른 ``channel_id`` 로 1:N fan-out(FR-9).

        대상·채널 모두 요청 tenant 소유여야 한다(cross-tenant fan-out 차단). 규칙은 ``enabled=True``.
        """

        await self._scoped_target(target_id, tenant_id=tenant_id)
        await self._scoped_channel(channel_id, tenant_id=tenant_id)
        rule = DeliveryRule(
            id=entity_id,
            target_id=target_id,
            channel_id=channel_id,
            template_id=template_id,
            enabled=True,
            send_only_on_change=send_only_on_change,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_DELIVERY_RULE_CREATE,
            target_type=TARGET_TYPE_DELIVERY_RULE,
            target_id=entity_id,
            at=at,
            diff={
                "op": "create",
                "target_id": target_id,
                "channel_id": channel_id,
                "template_id": template_id,
                "send_only_on_change": send_only_on_change,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.create_delivery_rule(rule, audit)
        return rule

    async def update_delivery_rule(
        self,
        rule_id: str,
        *,
        tenant_id: str,
        template_id: str | None = None,
        send_only_on_change: bool | None = None,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> DeliveryRule:
        """전송 규칙 옵션(템플릿/변경시에만 전송)을 편집한다(scope = target→tenant)."""

        rule, _target = await self._scoped_rule(rule_id, tenant_id=tenant_id)
        updated = replace(
            rule,
            template_id=template_id if template_id is not None else rule.template_id,
            send_only_on_change=(
                send_only_on_change if send_only_on_change is not None else rule.send_only_on_change
            ),
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_DELIVERY_RULE_UPDATE,
            target_type=TARGET_TYPE_DELIVERY_RULE,
            target_id=rule_id,
            at=at,
            diff={
                "from_template_id": rule.template_id,
                "to_template_id": updated.template_id,
                "from_send_only_on_change": rule.send_only_on_change,
                "to_send_only_on_change": updated.send_only_on_change,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.save_delivery_rule(updated, audit)
        return updated

    async def deactivate_delivery_rule(
        self,
        rule_id: str,
        *,
        tenant_id: str,
        at: datetime,
        actor_id: str | None,
        source: str | None = None,
        reason: str | None = None,
    ) -> DeliveryRule:
        """전송 규칙 soft delete — ``enabled=False``(물리 삭제 0, FR-4). 멱등(이미 비활성 no-op)."""

        rule, _target = await self._scoped_rule(rule_id, tenant_id=tenant_id)
        if rule.enabled is False:
            return rule  # 멱등 no-op
        updated = replace(rule, enabled=False)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_DELIVERY_RULE_DEACTIVATE,
            target_type=TARGET_TYPE_DELIVERY_RULE,
            target_id=rule_id,
            at=at,
            diff={"from_enabled": rule.enabled, "to_enabled": False, "reason": reason},
            source=source,
            reason=reason,
        )
        await self._repo.save_delivery_rule(updated, audit)
        return updated


# ══════════════════════════════════════════════════════════════════════════
# in-memory 구현(무-DB 기본값 + always-run 테스트 fake — InMemoryAdminActionRepository 선례)
# ══════════════════════════════════════════════════════════════════════════

class InMemoryAdminEntityRepository:
    """프로세스-내 엔티티 CRUD repository(무-DB 기본값 + 테스트 fake).

    create/save 와 audit append 를 한 메서드 안에서 함께 수행해 "같은 트랜잭션"(둘 다 반영 또는
    둘 다 미반영) 의미를 모사한다 — 본 fake 는 예외를 던지지 않으므로 부분 반영이 없다.
    """

    def __init__(self) -> None:
        self._tenants: dict[str, Tenant] = {}
        self._subscriptions: dict[str, Subscription] = {}
        self._accounts: dict[str, PlatformAccount] = {}
        self._targets: dict[str, MonitoringTarget] = {}
        self._channels: dict[str, MessengerChannel] = {}
        self._rules: dict[str, DeliveryRule] = {}
        self._registration_codes: dict[str, str] = {}  # channel_id → code(라우팅 — 비domain)
        self.audits: list[AuditEntry] = []

    # ── read(get) ───────────────────────────────────────────────────────────
    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        return self._tenants.get(tenant_id)

    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        return self._subscriptions.get(subscription_id)

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None:
        return self._accounts.get(account_id)

    async def get_monitoring_target(self, target_id: str) -> MonitoringTarget | None:
        return self._targets.get(target_id)

    async def get_messenger_channel(self, channel_id: str) -> MessengerChannel | None:
        return self._channels.get(channel_id)

    async def get_delivery_rule(self, rule_id: str) -> DeliveryRule | None:
        return self._rules.get(rule_id)

    # ── list ─────────────────────────────────────────────────────────────────
    async def list_tenants(self) -> list[Tenant]:
        return list(self._tenants.values())

    async def list_subscriptions(self, tenant_id: str) -> list[Subscription]:
        return [s for s in self._subscriptions.values() if s.tenant_id == tenant_id]

    async def list_platform_accounts(self, tenant_id: str) -> list[PlatformAccount]:
        return [a for a in self._accounts.values() if a.tenant_id == tenant_id]

    async def list_monitoring_targets(self, tenant_id: str) -> list[MonitoringTarget]:
        return [t for t in self._targets.values() if t.tenant_id == tenant_id]

    async def list_messenger_channels(self, tenant_id: str) -> list[MessengerChannel]:
        return [c for c in self._channels.values() if c.tenant_id == tenant_id]

    async def list_delivery_rules(self, target_id: str) -> list[DeliveryRule]:
        return [r for r in self._rules.values() if r.target_id == target_id]

    async def tenant_has_dependencies(self, tenant_id: str) -> bool:
        return (
            any(s.tenant_id == tenant_id for s in self._subscriptions.values())
            or any(a.tenant_id == tenant_id for a in self._accounts.values())
            or any(t.tenant_id == tenant_id for t in self._targets.values())
            or any(c.tenant_id == tenant_id for c in self._channels.values())
        )

    # ── create + audit(같은 트랜잭션 모사) ──────────────────────────────────────
    async def create_tenant(self, tenant: Tenant, audit: AuditEntry) -> None:
        self._tenants[tenant.id] = tenant
        self.audits.append(audit)

    async def create_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None:
        self._subscriptions[subscription.id] = subscription
        self.audits.append(audit)

    async def create_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None:
        self._accounts[account.id] = account
        self.audits.append(audit)

    async def create_monitoring_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None:
        self._targets[target.id] = target
        self.audits.append(audit)

    async def create_messenger_channel(
        self,
        channel: MessengerChannel,
        audit: AuditEntry,
        *,
        registration_code: str | None = None,
    ) -> None:
        self._channels[channel.id] = channel
        if registration_code is not None:
            self._registration_codes[channel.id] = registration_code
        self.audits.append(audit)

    async def create_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None:
        self._rules[rule.id] = rule
        self.audits.append(audit)

    # ── save(UPDATE) + audit(같은 트랜잭션 모사) ────────────────────────────────
    async def save_tenant(self, tenant: Tenant, audit: AuditEntry) -> None:
        self._tenants[tenant.id] = tenant
        self.audits.append(audit)

    async def save_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None:
        self._subscriptions[subscription.id] = subscription
        self.audits.append(audit)

    async def save_platform_account(
        self, account: PlatformAccount, audit: AuditEntry
    ) -> None:
        self._accounts[account.id] = account
        self.audits.append(audit)

    async def save_monitoring_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None:
        self._targets[target.id] = target
        self.audits.append(audit)

    async def save_messenger_channel(
        self, channel: MessengerChannel, audit: AuditEntry
    ) -> None:
        self._channels[channel.id] = channel
        self.audits.append(audit)

    async def save_delivery_rule(self, rule: DeliveryRule, audit: AuditEntry) -> None:
        self._rules[rule.id] = rule
        self.audits.append(audit)

    # ── delete + audit(같은 트랜잭션 모사) ─────────────────────────────────────
    async def delete_tenant(self, tenant_id: str, audit: AuditEntry) -> None:
        self._tenants.pop(tenant_id, None)
        self.audits.append(audit)

    # ── seed(테스트 전용) ──────────────────────────────────────────────────────
    def seed_tenant(self, tenant: Tenant) -> None:
        self._tenants[tenant.id] = tenant

    def seed_subscription(self, subscription: Subscription) -> None:
        self._subscriptions[subscription.id] = subscription

    def seed_platform_account(self, account: PlatformAccount) -> None:
        self._accounts[account.id] = account

    def seed_monitoring_target(self, target: MonitoringTarget) -> None:
        self._targets[target.id] = target

    def seed_messenger_channel(self, channel: MessengerChannel) -> None:
        self._channels[channel.id] = channel

    def seed_delivery_rule(self, rule: DeliveryRule) -> None:
        self._rules[rule.id] = rule
