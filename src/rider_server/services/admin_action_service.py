"""Admin 수동 운영 액션 + 고객/구독 상태 전이 오케스트레이션 — Story 5.7 (AC1·AC2·AC3).

5.6 읽기 전용 대시보드 위에 **쓰기(상태 전이/액션)** 를 얹는다. 핵심 정책은 이미 존재하는
**순수 service** 를 재구현하지 않고 **wiring·persist·audit** 만 한다:

  * 구독 중지/복구/``HELD`` 처리 = :class:`SubscriptionGate`(2.6) — ``suspend``/``resume``/
    ``dispose_held`` 호출만, ``at``(시각)·``reason``·actor 는 본 service 가 주입.
  * job retry 전이 = :func:`queue.states.assert_transition`(5.3) — ``FAILED``/``RETRY`` → ``PENDING``
    만 허용(미정의 전이는 :class:`InvalidJobTransition`).
  * test send/retry 중복 차단 = :class:`IdempotentDeliveryService.deliver_once`(3.5) — ``reserve``
    seam 경유(우회 경로 신설 금지). 단일 채널만(fan-out 금지).
  * test crawl = :meth:`QueueBackend.enqueue`(5.3) — CRAWL job 1회.

**쓰기 경계(architecture #Service-Boundaries):** 상태 전이/DB write 는 **이 service(+repository)
에서만** 일어난다 — 라우트/템플릿은 이 service 만 호출한다. 영속은 :class:`AdminActionRepository`
포트(in-memory fake / PostgreSQL) 가 담당하고, **위험 액션은 액션 write 와 audit 기록을 같은
트랜잭션** 으로 묶는다(AC3 — 액션 성공·audit 누락 불가).

**fail-closed 불변식(게이트가 보장 — service 는 우회하지 않는다):**
  ① ``SUCCEEDED`` 는 발송 가능으로 되돌아가지 않는다(``dispose_held`` 가 비-HELD 거부).
  ② 복구(``resume``)는 구독 상태만 바꾸고 ``HELD`` 를 자동 발송하지 않는다 — ``HELD`` → 발송
     가능은 오직 운영자 ``HeldDisposition.RESUME`` 입력 시(별도 ``dispose_held_dispatch`` 액션).
  ③ 미매핑/모호 입력은 차단(전이표·게이트 ``ValueError``).

**결정성:** 내부에서 ``datetime.now()`` 를 호출하지 않는다 — 전이 시각 ``at`` 은 호출부 주입
(라우트=실 ``now()``, 테스트=고정 시각). 단방향 import: ``rider_server`` → ``rider_crawl`` 만.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Protocol

from rider_crawl.redaction import redact, redact_mapping
from rider_server.domain import (
    AuditResult,
    DeliveryLog,
    DeliveryStatus,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
    Subscription,
    SubscriptionStatus,
)
from rider_server.queue.backend import QueueBackend
from rider_server.queue.states import (
    JOB_TYPE_AUTH_CHECK,
    JOB_TYPE_AUTH_COUPANG_2FA,
    JOB_TYPE_CRAWL_BAEMIN,
    JOB_TYPE_CRAWL_COUPANG,
    JOB_TYPE_OPEN_AUTH_BROWSER,
    JOB_STATUS_PENDING,
    JOB_TYPES,
    assert_transition,
)
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.idempotency import IdempotentDeliveryService
from rider_server.services.recovery import effective_send_enabled
from rider_server.services.subscription_gate import (
    DispatchJobStatus,
    HeldDisposition,
    SubscriptionGate,
)
from rider_crawl.config import (
    DEFAULT_EMAIL_2FA_SENDER_KEYWORD,
    DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD,
)

#: 미해결(미인증/익명) actor 의 명시적 sentinel — 5.8 이 MFA/실 사용자/역할로 교체(AC3).
UNAUTHENTICATED_ACTOR = "UNAUTHENTICATED_ADMIN"

#: ``OPEN_AUTH_BROWSER`` 는 operator intent 가 짧게 살아 있는 interactive job 이다 — 운영자가
#: '인증 시작'을 누른 시점 이후 짧은 TTL 안에서만 유효하다. 서버 재시작 뒤 오래된 인증 브라우저
#: job 이 무제한 재실행되지 않도록, payload 에 ``requested_at``/``expires_at`` 를 실어 stale 판정의
#: 단일 시간 기준을 준다(10~15분 사이). [근거 문서: queue-backlog-handling-policy.md]
OPEN_AUTH_BROWSER_TTL = timedelta(minutes=12)

#: 쿠팡 email 2FA 자동복구 mode 식별자(crawl payload 가 아니라 auth job payload 에만 둔다 —
#: crawl-coupang-auth-separation Decision 6). ``AUTH_COUPANG_2FA`` job payload 의 ``recovery_mode``.
RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA = "coupang_auto_email_2fa"


def _iso_utc(dt: datetime) -> str:
    """timezone-aware datetime 을 ISO 8601 UTC(``…Z``)로 — epoch 혼용 금지(ADD-13)."""

    from datetime import timezone as _tz

    return (
        dt.astimezone(_tz.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )

# ── audit action 코드(UPPER_SNAKE 기계가독) ──────────────────────────────────────
ACTION_TARGET_ACTIVATE = "TARGET_ACTIVATE"
ACTION_TARGET_PAUSE = "TARGET_PAUSE"
ACTION_AGENT_ASSIGN = "AGENT_ASSIGN"
ACTION_JOB_RETRY = "JOB_RETRY"
ACTION_TEST_CRAWL = "TEST_CRAWL"
ACTION_AUTH_CHECK = "AUTH_CHECK"
ACTION_AUTH_START = "AUTH_START"
ACTION_DRY_RUN_RENDER = "DRY_RUN_RENDER"
ACTION_TEST_SEND = "TEST_SEND"
ACTION_SUBSCRIPTION_SUSPEND = "SUBSCRIPTION_SUSPEND"
ACTION_SUBSCRIPTION_RESUME = "SUBSCRIPTION_RESUME"
ACTION_HELD_DISPATCH_DISCARD = "HELD_DISPATCH_DISCARD"
ACTION_HELD_DISPATCH_RESUME = "HELD_DISPATCH_RESUME"
# ── 5.8 audit action 코드(token revoke/rotate + 접근 거부/break-glass) ─────────────
ACTION_AGENT_TOKEN_REVOKE = "AGENT_TOKEN_REVOKE"
ACTION_AGENT_TOKEN_ROTATE = "AGENT_TOKEN_ROTATE"
ACTION_EXTERNAL_TOKEN_ROTATE = "EXTERNAL_TOKEN_ROTATE"
ACTION_ACCESS_DENIED = "ACCESS_DENIED"
ACTION_BREAK_GLASS_OVERRIDE = "BREAK_GLASS_OVERRIDE"

# ── target_type 코드(audit_logs.target_type) ─────────────────────────────────────
TARGET_TYPE_TARGET = "monitoring_target"
TARGET_TYPE_PLATFORM_ACCOUNT = "platform_account"
TARGET_TYPE_JOB = "job"
TARGET_TYPE_SUBSCRIPTION = "subscription"
TARGET_TYPE_DISPATCH = "dispatch"
TARGET_TYPE_AGENT = "agent"
TARGET_TYPE_CHANNEL = "messenger_channel"
TARGET_TYPE_ACCESS = "admin_access"

# 수동 crawl 액션(test_crawl)이 enqueue 할 수 있는 job type — CRAWL 만 명시 허용(fail-closed).
# CAPTURE_DIAGNOSTIC 같은 미구현 artifact job 이 service 경계에서 생성되지 않게 한다. 이 가드는
# payload 생성 단계의 암묵적 _platform_for_crawl_job 실패에 기대지 않고 정책을 명시한다.
# (CAPTURE_DIAGNOSTIC 은 JOB_TYPES/DEFAULT_CAPABILITIES 에는 그대로 남는다 — vocabulary 불변.)
_MANUAL_CRAWL_JOB_TYPES = frozenset({JOB_TYPE_CRAWL_BAEMIN, JOB_TYPE_CRAWL_COUPANG})


# ══════════════════════════════════════════════════════════════════════════
# 중립 값 객체(repository 입출력 — ORM Row/SQL 누출 금지)
# ══════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class JobRef:
    """retry 대상 job 의 중립 표현(``queue`` 상태머신값 ``status`` + tenant scope 입력)."""

    job_id: str
    type: str
    target_id: str | None
    status: str
    tenant_id: str | None


@dataclass(frozen=True)
class HeldDispatchRef:
    """중지 시 보류(``HELD``)된 Dispatch 한 건의 중립 표현(열린 질문 #1 — 영속은 보수적 매핑).

    ``status`` 는 :class:`DispatchJobStatus` 값(``PENDING``/``HELD``/``SUCCEEDED``/``DISCARDED``).
    """

    dispatch_id: str
    tenant_id: str
    subscription_id: str
    status: str


@dataclass(frozen=True)
class AuditEntry:
    """``audit_logs`` INSERT 1건(5.7 AC3 + 5.8 AC1). ``diff_redacted`` 는 redaction 통과 dict(secret 0).

    ``actor_id`` 는 seam 이 준 식별자(UUID 문자열 또는 미인증 sentinel) — PG 는 UUID 면 컬럼에,
    아니면 컬럼은 NULL 로 두고 ``diff_redacted`` 에 보존한다(미인증도 추적 가능).

    5.8 이 readiness gate 7필드를 채우려 ``source``(변경 출처/역할/IP), ``reason``(운영자 자유
    텍스트), ``result``(:class:`AuditResult` 값 — 성공/실패/거부)를 **first-class 필드** 로 둔다.
    ``source``/``reason`` 은 redaction 통과값(평문 secret 0)이고 ``result`` 기본값은 ``SUCCESS``
    (거부 경로는 ``DENIED`` 를 명시). 기존 6필드 positional 생성과 호환되도록 default 를 둔다.
    """

    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    diff_redacted: dict
    created_at: datetime
    source: str | None = None
    reason: str | None = None
    result: str = AuditResult.SUCCESS.value


# ══════════════════════════════════════════════════════════════════════════
# service 예외(라우트가 HTTP 상태로 매핑)
# ══════════════════════════════════════════════════════════════════════════

class AdminActionNotFound(LookupError):
    """액션 대상 엔티티가 repository 에 없을 때(``entity_id`` 는 불투명 id — secret 아님)."""

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(f"{entity_type} not found: {entity_id}")
        self.entity_type = entity_type
        self.entity_id = entity_id


class TenantScopeViolation(AdminActionNotFound):
    """대상이 요청 tenant 소유가 아님(cross-tenant 누출 차단 — 존재 누설 방지로 not-found 동급).

    :class:`AdminActionNotFound` 하위라 라우트가 둘 다 404 로 매핑해 다른 tenant 의 리소스
    존재 여부를 노출하지 않는다(fail-closed, architecture #Data-Boundaries).
    """

    def __init__(self, entity_type: str, entity_id: str) -> None:
        super().__init__(entity_type, entity_id)


# ══════════════════════════════════════════════════════════════════════════
# repository 포트(읽기 + 액션 write+audit 동일 트랜잭션)
# ══════════════════════════════════════════════════════════════════════════

class AdminActionRepository(Protocol):
    """Admin 액션 영속 포트 — 상태 전이 결과 + audit 를 **같은 트랜잭션** 으로 영속한다.

    PG 구현은 :class:`rider_server.services.admin_action_repository_postgres.
    PostgresAdminActionRepository`, 테스트/무-DB 기본값은 :class:`InMemoryAdminActionRepository`.
    상태 전이 **결정** 은 :class:`AdminActionService`(+순수 게이트)가 하고, 포트는 그 결과를
    영속만 한다(``transition_*`` 는 결정된 새 상태를 받는다 — 자체 전이 판정 금지).
    """

    async def get_subscription(self, subscription_id: str) -> Subscription | None: ...

    async def get_target(self, target_id: str) -> MonitoringTarget | None: ...

    async def get_target_platform(self, target_id: str) -> str | None: ...

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None: ...

    async def get_job(self, job_id: str) -> JobRef | None: ...

    async def get_held_dispatch(self, dispatch_id: str) -> HeldDispatchRef | None: ...

    async def transition_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None: ...

    async def transition_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None: ...

    async def transition_job(
        self, job_id: str, *, status: str, audit: AuditEntry
    ) -> None: ...

    async def transition_dispatch(
        self, dispatch_id: str, *, status: str, audit: AuditEntry
    ) -> None: ...

    async def assign_agent(
        self, *, target_id: str, agent_id: str, audit: AuditEntry
    ) -> None: ...

    async def record_audit(self, audit: AuditEntry) -> None: ...

    async def enqueue_manual_job(
        self,
        *,
        job_id: str,
        job_type: str,
        target_id: str,
        payload_json: dict,
        audit: AuditEntry,
        now: datetime,
    ) -> str: ...


# ══════════════════════════════════════════════════════════════════════════
# audit diff 합성(secret 위생 — redaction 통과)
# ══════════════════════════════════════════════════════════════════════════

def build_diff_redacted(payload: dict) -> dict:
    """액션 diff 를 ``redact_mapping`` 으로 통과시켜 ``diff_redacted`` 로 만든다(AC3).

    token/OTP/password/chat_id 원문 평문 0, 운영 식별자(고객/센터/방명)도 진단 산출물 기준으로
    ``mask_operational_ids=True`` 마스킹한다(defense-in-depth). 호출부는 secret 을 애초에 넣지
    않되, 자유 텍스트 ``reason`` 에 우발적으로 섞여도 본 함수가 가린다.
    """

    return redact_mapping(payload, mask_operational_ids=True)


def _test_crawl_payload(
    target: MonitoringTarget,
    job_type: str,
    account: PlatformAccount | None = None,
) -> dict[str, object]:
    platform = _platform_for_crawl_job(job_type)
    payload = _target_job_payload(target, job_type=job_type, platform=platform)
    # 쿠팡 crawl 은 로그인/이메일 2FA ref 가 있어야 세션 만료 시 inline 자동복구가 동작한다.
    # scheduled crawl(_crawl_job_payload)은 이 ref 들을 싣는데 수동 test-crawl 은 빠져 있어,
    # '지금 수집(재검증)'이 자격증명 없이 돌다 브라우저도 못 띄우고 빠르게 실패·재시도했다.
    # 같은 ref 를 실어 scheduled crawl 과 동일하게 동작시킨다(secret 은 ref 핸들만, 평문 0).
    if platform == "coupang" and account is not None:
        auto_2fa_complete = bool(
            account.username
            and account.password
            and account.verification_email_address
            and account.verification_email_app_password
        )
        payload.update(
            {
                "coupang_login_id_ref": account.username,
                "coupang_login_password_ref": account.password,
                "verification_email_address_ref": account.verification_email_address,
                "verification_email_app_password_ref": account.verification_email_app_password,
                "verification_email_subject_keyword": (
                    account.verification_email_subject_keyword
                    or DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
                ),
                "verification_email_sender_keyword": (
                    account.verification_email_sender_keyword
                    or DEFAULT_EMAIL_2FA_SENDER_KEYWORD
                ),
                "coupang_auto_email_2fa_enabled": auto_2fa_complete,
            }
        )
    return payload


def _auth_check_payload(target: MonitoringTarget, *, platform: str) -> dict[str, object]:
    return _target_job_payload(
        target,
        job_type=JOB_TYPE_AUTH_CHECK,
        platform=_platform_registry_key(platform),
    )


def _open_auth_browser_ttl_fields(at: datetime) -> dict[str, object]:
    """``OPEN_AUTH_BROWSER`` payload 에 실을 operator-intent TTL 필드(짧은 수명).

    ``requested_at`` 은 운영자가 '인증 시작'을 누른 시점(주입 ``at``), ``expires_at`` 은 그로부터
    :data:`OPEN_AUTH_BROWSER_TTL`(10~15분) 뒤다. server/Agent 가 이 두 ISO 시각으로 stale 여부를
    같은 기준으로 판단한다. secret 0(시각만).
    """

    return {
        "requested_at": _iso_utc(at),
        "expires_at": _iso_utc(at + OPEN_AUTH_BROWSER_TTL),
    }


def _auth_start_payload(
    target: MonitoringTarget,
    account: PlatformAccount,
    *,
    at: datetime,
    force_manual_browser: bool = False,
) -> tuple[str, dict[str, object]]:
    platform = _platform_registry_key(account.platform.value)
    ttl_fields = _open_auth_browser_ttl_fields(at)
    base_payload = {
        **_target_job_payload(target, job_type=JOB_TYPE_OPEN_AUTH_BROWSER, platform=platform),
        **ttl_fields,
    }
    if platform == "baemin":
        login_id_ref = str(account.username or "").strip()
        login_password_ref = str(account.password or "").strip()
        if not login_id_ref or not login_password_ref:
            raise ValueError("배민 로그인 ID/PW가 필요합니다")
        return (
            JOB_TYPE_OPEN_AUTH_BROWSER,
            {
                **base_payload,
                "login_id_ref": login_id_ref,
                "login_password_ref": login_password_ref,
            },
        )

    # ── 쿠팡 ──────────────────────────────────────────────────────────────────────
    # crawl-coupang-auth-separation Task 5 / Decision 1:
    #   * 자동 email 2FA 정보(로그인 ID/PW + 이메일 주소/앱비번)가 모두 있으면 전용 인증 job
    #     ``AUTH_COUPANG_2FA`` 를 만든다(자동 OTP 입력). ``OPEN_AUTH_BROWSER`` 가 아니다.
    #   * 로그인 ID/PW 는 있으나 email 2FA 정보가 부족하면 **수동 브라우저 열기**
    #     (``OPEN_AUTH_BROWSER``)로 폴백한다(사람이 직접 조치 — 자동 2FA 미동작).
    #   * 로그인 ID/PW 자체가 없으면 인증 자체가 불가하므로 거부한다.
    login_id_ref = str(account.username or "").strip()
    login_password_ref = str(account.password or "").strip()
    verification_email_address_ref = str(
        account.verification_email_address or ""
    ).strip()
    verification_email_app_password_ref = str(
        account.verification_email_app_password or ""
    ).strip()
    if not login_id_ref or not login_password_ref:
        raise ValueError("쿠팡 로그인 ID/PW가 필요합니다")

    coupang_login_refs = {
        "coupang_login_id_ref": login_id_ref,
        "coupang_login_password_ref": login_password_ref,
    }
    auto_2fa_complete = bool(
        verification_email_address_ref and verification_email_app_password_ref
    )
    if force_manual_browser or not auto_2fa_complete:
        # 수동 폴백: 브라우저만 열고 사람이 조치(자동 OTP 0). 자동 2FA 플래그/recovery_mode 미포함.
        return (
            JOB_TYPE_OPEN_AUTH_BROWSER,
            {
                **base_payload,
                **coupang_login_refs,
            },
        )

    email_refs = {
        "verification_email_address_ref": verification_email_address_ref,
        "verification_email_app_password_ref": verification_email_app_password_ref,
        "verification_email_subject_keyword": (
            str(account.verification_email_subject_keyword or "").strip()
            or DEFAULT_EMAIL_2FA_SUBJECT_KEYWORD
        ),
        "verification_email_sender_keyword": (
            str(account.verification_email_sender_keyword or "").strip()
            or DEFAULT_EMAIL_2FA_SENDER_KEYWORD
        ),
    }
    # 자동복구: 전용 인증 job. base_payload 의 job_type(OPEN_AUTH_BROWSER)을 덮어쓴다.
    return (
        JOB_TYPE_AUTH_COUPANG_2FA,
        {
            **base_payload,
            "job_type": JOB_TYPE_AUTH_COUPANG_2FA,
            **coupang_login_refs,
            **email_refs,
            "recovery_mode": RECOVERY_MODE_COUPANG_AUTO_EMAIL_2FA,
        },
    )


def _target_job_payload(
    target: MonitoringTarget, *, job_type: str, platform: str
) -> dict[str, object]:
    return {
        "target_id": target.id,
        "tenant_id": target.tenant_id,
        "platform": platform,
        "platform_account_id": target.platform_account_id,
        "primary_url": target.url,
        "expected_display_name": target.center_name,
        "browser_profile_ref": f"profile:{target.id}",
        "timeout_seconds": 60,
        "parser_version": f"{platform}-v1",
        "job_type": job_type,
    }


def _platform_for_crawl_job(job_type: str) -> str:
    if job_type == JOB_TYPE_CRAWL_BAEMIN:
        return "baemin"
    if job_type == JOB_TYPE_CRAWL_COUPANG:
        return "coupang"
    raise ValueError(f"unknown crawl job type: {job_type}")


def _platform_registry_key(platform: str) -> str:
    normalized = str(platform or "").strip().casefold()
    if normalized in {"baemin", "coupang"}:
        return normalized
    raise ValueError(f"unknown platform: {platform}")


# ══════════════════════════════════════════════════════════════════════════
# 액션 service(상태 전이/액션 단일 소유처 — 라우트는 이것만 호출)
# ══════════════════════════════════════════════════════════════════════════

class AdminActionService:
    """수동 운영 액션 + 구독/Dispatch 상태 전이 오케스트레이션(순수 게이트 compose + persist+audit)."""

    def __init__(
        self, repository: AdminActionRepository, queue_backend: QueueBackend
    ) -> None:
        self._repo = repository
        self._queue = queue_backend

    # ── 내부: tenant scope 검증(cross-tenant 누출 차단) ──────────────────────────
    @staticmethod
    def _audit(
        *,
        actor_id: str | None,
        action: str,
        target_type: str | None,
        target_id: str | None,
        at: datetime,
        diff: dict,
        source: str | None = None,
        reason: str | None = None,
        result: str = AuditResult.SUCCESS.value,
    ) -> AuditEntry:
        # source/reason 은 자유 텍스트라 free-text redact 통과(평문 secret 0 — 게이트레일 #5).
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

    # ── 5.8 AC1·AC2: 접근 거부/break-glass 도 audit(보안 audit — 시도 자체를 남긴다) ──
    async def record_denied(
        self,
        *,
        actor_id: str | None,
        action: str,
        source: str | None,
        reason: str | None,
        at: datetime,
        target_type: str | None = TARGET_TYPE_ACCESS,
        target_id: str | None = None,
    ) -> None:
        """권한·MFA·IP·fail-closed 거부를 ``result=DENIED`` 로 기록한다(AC1·AC2).

        보안 audit 의 핵심은 성공뿐 아니라 **거부된 시도** 도 남기는 것이다 — security 레이어
        (``require_role``)가 거부 직전 이 메서드를 호출한다. routes.py(읽기 전용)가 아니라
        service 경유라 read-only 가드(audit-on-deny 는 service 에서)와 정합(게이트레일 #1).
        """

        audit = self._audit(
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            at=at,
            diff={"reason": reason} if reason else {},
            source=source,
            reason=reason,
            result=AuditResult.DENIED.value,
        )
        await self._repo.record_audit(audit)

    async def record_break_glass(
        self,
        *,
        actor_id: str | None,
        source: str | None,
        reason: str | None,
        at: datetime,
        target_type: str | None = TARGET_TYPE_ACCESS,
        target_id: str | None = None,
    ) -> None:
        """break-glass(긴급 override) 사용을 강하게 audit 한다(AC2 — 모든 break-glass 기록)."""

        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_BREAK_GLASS_OVERRIDE,
            target_type=target_type,
            target_id=target_id,
            at=at,
            diff={"reason": reason} if reason else {},
            source=source,
            reason=reason,
            result=AuditResult.SUCCESS.value,
        )
        await self._repo.record_audit(audit)

    async def _scoped_target(self, target_id: str, *, tenant_id: str) -> MonitoringTarget:
        target = await self._repo.get_target(target_id)
        if target is None:
            raise AdminActionNotFound(TARGET_TYPE_TARGET, target_id)
        if target.tenant_id != tenant_id:
            raise TenantScopeViolation(TARGET_TYPE_TARGET, target_id)
        return target

    async def _scoped_platform_account(
        self, account_id: str, *, tenant_id: str
    ) -> PlatformAccount:
        account = await self._repo.get_platform_account(account_id)
        if account is None:
            raise AdminActionNotFound(TARGET_TYPE_PLATFORM_ACCOUNT, account_id)
        if account.tenant_id != tenant_id:
            raise TenantScopeViolation(TARGET_TYPE_PLATFORM_ACCOUNT, account_id)
        return account

    async def _scoped_subscription(
        self, subscription_id: str, *, tenant_id: str
    ) -> Subscription:
        sub = await self._repo.get_subscription(subscription_id)
        if sub is None:
            raise AdminActionNotFound(TARGET_TYPE_SUBSCRIPTION, subscription_id)
        if sub.tenant_id != tenant_id:
            raise TenantScopeViolation(TARGET_TYPE_SUBSCRIPTION, subscription_id)
        return sub

    # ── AC1: 대상 활성/비활성(ACTIVE↔PAUSED) ─────────────────────────────────────
    async def set_target_status(
        self,
        target_id: str,
        *,
        active: bool,
        tenant_id: str,
        actor_id: str | None,
        reason: str,
        at: datetime,
        source: str | None = None,
    ) -> MonitoringTarget:
        """운영 활성/비활성 토글 — ``ACTIVE``↔``PAUSED`` 만(``INACTIVE`` soft delete 는 5.11).

        현재 상태가 ``INACTIVE`` 면 운영 토글 대상이 아니므로 거부한다(fail-closed — 삭제 복구는
        5.11 CRUD). 상태 전이 결과 + audit 를 같은 트랜잭션으로 영속한다.
        """

        target = await self._scoped_target(target_id, tenant_id=tenant_id)
        if target.status is MonitoringTargetStatus.INACTIVE:
            raise ValueError("INACTIVE 대상은 운영 토글 대상이 아니다(soft delete 복구는 5.11)")
        new_status = (
            MonitoringTargetStatus.ACTIVE if active else MonitoringTargetStatus.PAUSED
        )
        updated = replace(target, status=new_status)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TARGET_ACTIVATE if active else ACTION_TARGET_PAUSE,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={
                "from_status": target.status.value,
                "to_status": new_status.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.transition_target(updated, audit)
        return updated

    # ── AC1: Agent 배정(target↔agent affinity) ──────────────────────────────────
    async def assign_agent(
        self,
        *,
        target_id: str,
        agent_id: str,
        tenant_id: str,
        actor_id: str | None,
        reason: str,
        at: datetime,
        source: str | None = None,
    ) -> None:
        """대상에 Agent 를 배정한다(affinity). 대상 tenant scope 검증 후 audit 와 함께 영속."""

        await self._scoped_target(target_id, tenant_id=tenant_id)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_AGENT_ASSIGN,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={"agent_id": agent_id, "reason": reason},
            source=source,
            reason=reason,
        )
        await self._repo.assign_agent(target_id=target_id, agent_id=agent_id, audit=audit)

    # ── AC1: job retry(FAILED/RETRY → PENDING) ──────────────────────────────────
    async def retry_job(
        self,
        job_id: str,
        *,
        tenant_id: str,
        actor_id: str | None,
        reason: str,
        at: datetime,
        source: str | None = None,
    ) -> str:
        """job 을 ``PENDING`` 재진입시킨다 — ``assert_transition`` 통과 시에만(우회 금지).

        ``FAILED``/``RETRY`` → ``PENDING`` 만 허용된다(``SUCCEEDED`` 등 다른 status retry 는
        :class:`InvalidJobTransition`). 새 job 강제 생성·``SUCCEEDED`` 되돌림 0(불변식 ①).
        attempts/backoff 는 queue 구현 소유.
        """

        job = await self._repo.get_job(job_id)
        if job is None:
            raise AdminActionNotFound(TARGET_TYPE_JOB, job_id)
        if job.tenant_id is not None and job.tenant_id != tenant_id:
            raise TenantScopeViolation(TARGET_TYPE_JOB, job_id)
        assert_transition(job.status, JOB_STATUS_PENDING)  # 미허용 전이는 거부(우회 금지)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_JOB_RETRY,
            target_type=TARGET_TYPE_JOB,
            target_id=job.target_id,
            at=at,
            diff={
                "job_id": job_id,
                "from_status": job.status,
                "to_status": JOB_STATUS_PENDING,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.transition_job(job_id, status=JOB_STATUS_PENDING, audit=audit)
        return JOB_STATUS_PENDING

    # ── AC1: test crawl(CRAWL job 1회 enqueue) ──────────────────────────────────
    async def test_crawl(
        self,
        *,
        target_id: str,
        job_type: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
    ) -> str:
        """대상에 대해 CRAWL job 1건만 enqueue 한다(``QueueBackend.enqueue`` 재사용).

        ``job_type`` 은 정본 job type(``CRAWL_BAEMIN``/``CRAWL_COUPANG`` 등) 이어야 한다 —
        미정의 type 은 거부(fail-closed). audit 는 enqueue 직후 기록한다.
        """

        target = await self._scoped_target(target_id, tenant_id=tenant_id)
        if job_type not in JOB_TYPES:
            raise ValueError(f"unknown job type: {job_type}")
        # 수동 crawl 액션은 CRAWL_BAEMIN/CRAWL_COUPANG 만 허용한다(명시 fail-closed). 알려진
        # job type 이라도 CRAWL 이 아니면(예: 미구현 CAPTURE_DIAGNOSTIC) payload 생성 전에
        # 거부해 큐에 애매한 UNSUPPORTED_JOB_TYPE 으로 남지 않게 한다.
        if job_type not in _MANUAL_CRAWL_JOB_TYPES:
            raise ValueError(f"unsupported manual crawl job type: {job_type}")
        # 쿠팡 crawl 은 계정의 로그인/2FA ref 가 payload 에 있어야 세션 만료 시 자동복구가 된다
        # (scheduled crawl 과 동일). 계정을 tenant scope 로 불러와 enrich 한다. 미연결/조회 실패
        # 시엔 account 없이 진행(배민이거나 ref 없는 계정은 기존 동작 유지).
        account: PlatformAccount | None = None
        if job_type == JOB_TYPE_CRAWL_COUPANG and target.platform_account_id:
            try:
                account = await self._scoped_platform_account(
                    target.platform_account_id, tenant_id=tenant_id
                )
            except AdminActionNotFound:
                account = None
        payload = _test_crawl_payload(target, job_type, account)
        enqueue_manual_job = getattr(self._repo, "enqueue_manual_job", None)
        if callable(enqueue_manual_job):
            job_id = str(uuid.uuid4())
            audit = self._audit(
                actor_id=actor_id,
                action=ACTION_TEST_CRAWL,
                target_type=TARGET_TYPE_TARGET,
                target_id=target_id,
                at=at,
                diff={"job_id": job_id, "job_type": job_type},
                source=source,
            )
            await enqueue_manual_job(
                job_id=job_id,
                job_type=job_type,
                target_id=target_id,
                payload_json=payload,
                audit=audit,
                now=at,
            )
            return job_id
        job_id = await self._queue.enqueue(
            job_type=job_type,
            target_id=target_id,
            payload_json=payload,
            now=at,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TEST_CRAWL,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={"job_id": job_id, "job_type": job_type},
            source=source,
        )
        await self._repo.record_audit(audit)
        return job_id

    # ── AC1: 인증 필요 확인(AUTH_CHECK 트리거) ───────────────────────────────────
    async def auth_check(
        self,
        *,
        target_id: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
    ) -> str:
        """대상에 대해 ``AUTH_CHECK`` job 1건을 트리거한다(인증 상태 재확인)."""

        target = await self._scoped_target(target_id, tenant_id=tenant_id)
        platform = await self._repo.get_target_platform(target_id)
        payload = _auth_check_payload(target, platform=platform or "")
        enqueue_manual_job = getattr(self._repo, "enqueue_manual_job", None)
        if callable(enqueue_manual_job):
            job_id = str(uuid.uuid4())
            audit = self._audit(
                actor_id=actor_id,
                action=ACTION_AUTH_CHECK,
                target_type=TARGET_TYPE_TARGET,
                target_id=target_id,
                at=at,
                diff={"job_id": job_id},
                source=source,
            )
            await enqueue_manual_job(
                job_id=job_id,
                job_type=JOB_TYPE_AUTH_CHECK,
                target_id=target_id,
                payload_json=payload,
                audit=audit,
                now=at,
            )
            return job_id
        job_id = await self._queue.enqueue(
            job_type=JOB_TYPE_AUTH_CHECK,
            target_id=target_id,
            payload_json=payload,
            now=at,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_AUTH_CHECK,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={"job_id": job_id},
            source=source,
        )
        await self._repo.record_audit(audit)
        return job_id

    async def start_auth(
        self,
        *,
        target_id: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
        force_manual_browser: bool = False,
    ) -> str:
        """대상 인증을 시작한다.

        Baemin 은 ``OPEN_AUTH_BROWSER`` job 을 쓴다. Coupang 은 로그인 ID/PW 와 email 2FA
        정보가 모두 있으면 ``AUTH_COUPANG_2FA`` 를 쓰고, email 2FA 정보가 없으면
        ``OPEN_AUTH_BROWSER`` 로 폴백한다.
        """

        target = await self._scoped_target(target_id, tenant_id=tenant_id)
        account = await self._scoped_platform_account(target.platform_account_id, tenant_id=tenant_id)
        job_type, payload = _auth_start_payload(
            target,
            account,
            at=at,
            force_manual_browser=force_manual_browser,
        )
        enqueue_manual_job = getattr(self._repo, "enqueue_manual_job", None)
        if callable(enqueue_manual_job):
            job_id = str(uuid.uuid4())
            audit = self._audit(
                actor_id=actor_id,
                action=ACTION_AUTH_START,
                target_type=TARGET_TYPE_TARGET,
                target_id=target_id,
                at=at,
                diff={"job_id": job_id, "job_type": job_type, "platform": account.platform.value},
                source=source,
            )
            await enqueue_manual_job(
                job_id=job_id,
                job_type=job_type,
                target_id=target_id,
                payload_json=payload,
                audit=audit,
                now=at,
            )
            return job_id
        job_id = await self._queue.enqueue(
            job_type=job_type,
            target_id=target_id,
            payload_json=payload,
            now=at,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_AUTH_START,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={"job_id": job_id, "job_type": job_type, "platform": account.platform.value},
            source=source,
        )
        await self._repo.record_audit(audit)
        return job_id

    # ── AC1: dry-run render(실발송 없이 렌더만 — FR-3) ──────────────────────────
    async def dry_run_render(
        self,
        render,
        *,
        target_id: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
    ) -> str:
        """주입된 ``render()`` 로 메시지 텍스트만 만든다 — 실발송·``DeliveryLog`` 0(FR-3 dry-run).

        send/queue/deliver_once 를 **호출하지 않는다**(구조적으로 미발송 보장). 반환 텍스트는
        호출부가 redaction 통과 후 표시한다. audit ``diff_redacted`` 에는 redact 통과 미리보기만.
        """

        await self._scoped_target(target_id, tenant_id=tenant_id)
        text = render()
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_DRY_RUN_RENDER,
            target_type=TARGET_TYPE_TARGET,
            target_id=target_id,
            at=at,
            diff={"preview": redact(text)[:200], "sent": False},
            source=source,
        )
        await self._repo.record_audit(audit)
        return text

    # ── AC1: test send(운영자 지정 단일 테스트 채널로만 — fan-out 금지) ──────────
    async def test_send(
        self,
        job: DispatchJob,
        *,
        collected_at: datetime,
        reserve,
        send,
        log_id_for,
        sent_at: datetime,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
        sending_enabled: bool = True,
    ):
        """단일 ``job`` 1건만 ``deliver_once`` 로 멱등 전송한다(fan-out 0, dedup 우회 0).

        ``IdempotentDeliveryService.deliver_once`` 의 ``reserve`` seam 을 그대로 통과하므로 같은
        dedup key 재시도는 ``DUPLICATE_BLOCKED`` 로 차단된다(우회 경로 신설 금지, AC1). 실 고객
        fan-out 은 호출하지 않는다 — 운영자가 지정한 **테스트 채널 1건** 만 받는다.

        **전역 dispatch kill switch(5.10/AC3).** 실전송 = ``send_enabled``(운영자가 지정한 단일
        테스트 채널이므로 True) **AND** ``sending_enabled``(환경 전역 복구 플래그). 새 차단 로직을
        만들지 않고 :func:`recovery.effective_send_enabled` 를 재사용한다. ``sending_enabled``
        가 False(복구/신규 환경 기본 OFF)면 주입 ``send`` 를 **호출하지 않고** 미발송 결과
        (``DeliveryStatus.HELD``, ``sent_at=None``) + ``result=DENIED`` audit 를 남긴다 —
        ``deliver_once`` 본문·시그니처·``reserve→send`` 순서·crash-after-send 안전을 건드리지
        않는다(게이트는 실 ``send`` 호출부인 이 service 에서 분기). 미래 중앙 dispatch 런타임
        루프가 도입되면 그 실 ``send`` 호출부에도 동일 게이트(``effective_send_enabled``)를
        compose해야 한다(우회 금지).
        """

        if not effective_send_enabled(send_enabled=True, sending_enabled=sending_enabled):
            blocked = DeliveryLog(
                id=log_id_for(job),
                message_id=job.message_id,
                channel_id=job.channel_id,
                status=DeliveryStatus.HELD,
                dedup_key=IdempotentDeliveryService.build_dedup_key(
                    target_id=job.target_id,
                    channel_id=job.channel_id,
                    collected_at=collected_at,
                    template_version=job.template_version,
                    message_hash=job.message_hash,
                ),
                error_code=None,
                sent_at=None,
            )
            audit = self._audit(
                actor_id=actor_id,
                action=ACTION_TEST_SEND,
                target_type=TARGET_TYPE_TARGET,
                target_id=job.target_id,
                at=at,
                diff={
                    "channel_id": job.channel_id,
                    "status": blocked.status.value,
                    "sending_enabled": False,
                },
                source=source,
                result=AuditResult.DENIED.value,
            )
            await self._repo.record_audit(audit)
            return blocked

        result = IdempotentDeliveryService.deliver_once(
            job,
            collected_at=collected_at,
            reserve=reserve,
            send=send,
            log_id_for=log_id_for,
            sent_at=sent_at,
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_TEST_SEND,
            target_type=TARGET_TYPE_TARGET,
            target_id=job.target_id,
            at=at,
            diff={
                "channel_id": job.channel_id,
                "status": result.status.value,
            },
            source=source,
        )
        await self._repo.record_audit(audit)
        return result

    # ── AC2: 구독 중지/복구(게이트 호출 — 가공 재구현 금지) ──────────────────────
    async def suspend_subscription(
        self,
        subscription_id: str,
        *,
        reason: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        source: str | None = None,
    ) -> Subscription:
        """``SubscriptionGate.suspend`` 결과(새 ``Subscription`` + ``SubscriptionStateChange``)를 persist."""

        sub = await self._scoped_subscription(subscription_id, tenant_id=tenant_id)
        new_sub, change = SubscriptionGate.suspend(sub, reason=reason, at=at)
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_SUSPEND,
            target_type=TARGET_TYPE_SUBSCRIPTION,
            target_id=subscription_id,
            at=at,
            diff={
                "from_status": change.from_status.value,
                "to_status": change.to_status.value,
                "reason": change.reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.transition_subscription(new_sub, audit)
        return new_sub

    async def resume_subscription(
        self,
        subscription_id: str,
        *,
        reason: str,
        tenant_id: str,
        actor_id: str | None,
        at: datetime,
        to_status: SubscriptionStatus = SubscriptionStatus.PAYMENT_ACTIVE,
        source: str | None = None,
    ) -> Subscription:
        """``SubscriptionGate.resume`` 결과를 persist — 복구는 구독 상태만 바꾼다(불변식 ②).

        ``HELD`` Dispatch 는 건드리지 않는다 — 재개/폐기는 별도 운영자 액션
        (:meth:`dispose_held_dispatch`)이다(자동 발송 금지).
        """

        sub = await self._scoped_subscription(subscription_id, tenant_id=tenant_id)
        new_sub, change = SubscriptionGate.resume(
            sub, reason=reason, at=at, to_status=to_status
        )
        audit = self._audit(
            actor_id=actor_id,
            action=ACTION_SUBSCRIPTION_RESUME,
            target_type=TARGET_TYPE_SUBSCRIPTION,
            target_id=subscription_id,
            at=at,
            diff={
                "from_status": change.from_status.value,
                "to_status": change.to_status.value,
                "reason": change.reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.transition_subscription(new_sub, audit)
        return new_sub

    # ── AC2: HELD Dispatch 폐기/재개(운영자 결정 — 게이트 dispose_held) ──────────
    async def dispose_held_dispatch(
        self,
        dispatch_id: str,
        disposition: HeldDisposition,
        *,
        tenant_id: str,
        actor_id: str | None,
        reason: str,
        at: datetime,
        source: str | None = None,
    ) -> str:
        """복구 시 보류된 Dispatch 를 운영자 결정으로 처리한다 — ``SubscriptionGate.dispose_held``.

        ``(HELD, DISCARD)`` → ``DISCARDED``, ``(HELD, RESUME)`` → ``PENDING``(재발송 후보). 비-HELD
        입력(특히 ``SUCCEEDED``)은 게이트가 ``ValueError`` 로 거부한다(불변식 ① — 성공분 재발송 0).
        **복구가 자동 발송하지 않음** 을 이 분리된 명시적 액션으로 보장한다(불변식 ②).
        """

        ref = await self._repo.get_held_dispatch(dispatch_id)
        if ref is None:
            raise AdminActionNotFound(TARGET_TYPE_DISPATCH, dispatch_id)
        if ref.tenant_id != tenant_id:
            raise TenantScopeViolation(TARGET_TYPE_DISPATCH, dispatch_id)
        new_status = SubscriptionGate.dispose_held(
            DispatchJobStatus(ref.status), disposition
        )  # 비-HELD 는 ValueError(fail-closed)
        action = (
            ACTION_HELD_DISPATCH_DISCARD
            if disposition is HeldDisposition.DISCARD
            else ACTION_HELD_DISPATCH_RESUME
        )
        audit = self._audit(
            actor_id=actor_id,
            action=action,
            target_type=TARGET_TYPE_DISPATCH,
            target_id=dispatch_id,
            at=at,
            diff={
                "from_status": ref.status,
                "to_status": new_status.value,
                "disposition": disposition.value,
                "reason": reason,
            },
            source=source,
            reason=reason,
        )
        await self._repo.transition_dispatch(
            dispatch_id, status=new_status.value, audit=audit
        )
        return new_status.value


# ══════════════════════════════════════════════════════════════════════════
# in-memory 구현(무-DB 기본값 + always-run 테스트 fake — InMemoryChannelRepository 선례)
# ══════════════════════════════════════════════════════════════════════════

class InMemoryAdminActionRepository:
    """프로세스-내 액션 repository(무-DB 기본값 + 테스트 fake).

    write+audit 를 ``threading`` 없이 단일 dict 갱신 + audit append 로 묶어 "같은 트랜잭션"
    의미(둘 다 성공 또는 둘 다 미반영)를 모사한다 — 본 fake 는 예외를 던지지 않으므로 부분
    반영이 발생하지 않는다. ``seed_*`` 는 테스트 주입용이다.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, Subscription] = {}
        self._targets: dict[str, MonitoringTarget] = {}
        self._platform_accounts: dict[str, PlatformAccount] = {}
        self._target_platforms: dict[str, str] = {}
        self._jobs: dict[str, JobRef] = {}
        self._held: dict[str, HeldDispatchRef] = {}
        self._assignments: dict[str, str] = {}  # target_id → agent_id
        self.audits: list[AuditEntry] = []

    # ── seed(테스트 전용) ──────────────────────────────────────────────────────
    def seed_subscription(self, subscription: Subscription) -> None:
        self._subscriptions[subscription.id] = subscription

    def seed_target(self, target: MonitoringTarget) -> None:
        self._targets[target.id] = target
        self._target_platforms.setdefault(target.id, "BAEMIN")

    def seed_platform_account(self, account: PlatformAccount) -> None:
        self._platform_accounts[account.id] = account

    def seed_target_platform(self, target_id: str, platform: str) -> None:
        self._target_platforms[target_id] = platform

    def seed_job(self, job: JobRef) -> None:
        self._jobs[job.job_id] = job

    def seed_held_dispatch(self, ref: HeldDispatchRef) -> None:
        self._held[ref.dispatch_id] = ref

    def agent_for(self, target_id: str) -> str | None:
        return self._assignments.get(target_id)

    # ── read ───────────────────────────────────────────────────────────────────
    async def get_subscription(self, subscription_id: str) -> Subscription | None:
        return self._subscriptions.get(subscription_id)

    async def get_target(self, target_id: str) -> MonitoringTarget | None:
        return self._targets.get(target_id)

    async def get_target_platform(self, target_id: str) -> str | None:
        if target_id not in self._targets:
            return None
        return self._target_platforms.get(target_id, "BAEMIN")

    async def get_platform_account(self, account_id: str) -> PlatformAccount | None:
        return self._platform_accounts.get(account_id)

    async def get_job(self, job_id: str) -> JobRef | None:
        return self._jobs.get(job_id)

    async def get_held_dispatch(self, dispatch_id: str) -> HeldDispatchRef | None:
        return self._held.get(dispatch_id)

    # ── write + audit(같은 트랜잭션 모사) ───────────────────────────────────────
    async def transition_subscription(
        self, subscription: Subscription, audit: AuditEntry
    ) -> None:
        self._subscriptions[subscription.id] = subscription
        self.audits.append(audit)

    async def transition_target(
        self, target: MonitoringTarget, audit: AuditEntry
    ) -> None:
        self._targets[target.id] = target
        self.audits.append(audit)

    async def transition_job(
        self, job_id: str, *, status: str, audit: AuditEntry
    ) -> None:
        existing = self._jobs[job_id]
        self._jobs[job_id] = replace(existing, status=status)
        self.audits.append(audit)

    async def transition_dispatch(
        self, dispatch_id: str, *, status: str, audit: AuditEntry
    ) -> None:
        existing = self._held[dispatch_id]
        self._held[dispatch_id] = replace(existing, status=status)
        self.audits.append(audit)

    async def assign_agent(
        self, *, target_id: str, agent_id: str, audit: AuditEntry
    ) -> None:
        self._assignments[target_id] = agent_id
        self.audits.append(audit)

    async def record_audit(self, audit: AuditEntry) -> None:
        self.audits.append(audit)
