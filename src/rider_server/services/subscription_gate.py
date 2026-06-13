"""구독 실행 게이트 정책(Story 2.6 / FR-6·FR-30, ADD-9) — 순수·결정적 로직.

``SubscriptionGate`` 는 2.5 도메인(``Subscription``/``SubscriptionStatus``)을 **소비만**
하고(도메인 무변경), 다음을 결정론적 순수 함수로 판정한다:

  1. 신규 CrawlJob/DispatchJob 예약·전송 허용 여부(``evaluate``).
  2. 중지 전환 시 미전송 Dispatch의 ``HELD`` 보류(``hold_undelivered``).
  3. 복구 시 ``HELD`` 의 운영자-결정 기반 폐기/재개(``dispose_held``).
  4. 중지/복구 전이 + 사유·시각 기록(``suspend``/``resume`` → ``SubscriptionStateChange``).

**순수·결정적·의존성 0.** FastAPI/SQLAlchemy/async 의존이 없고, 게이트 내부에서
``datetime.now()``/``uuid4()`` 같은 비결정 기본값을 호출하지 않는다 — 전이 시각(``at``)·
사유·식별자는 호출부가 주입한다(테스트 결정성). scheduler 앞단 wiring=Story 5.4,
Admin 상태 전이 UI=Story 5.7, ``jobs``/``audit_logs`` 영속=Epic 5.

**fail-closed 3 불변식(오발송보다 미발송).**
  ① 성공 기록(``SUCCEEDED``)된 Dispatch는 어떤 게이트 함수로도 발송 가능/대기 상태로
     되돌아가지 않는다(재발송 0).
  ② 복구(``resume``)는 구독 상태만 바꾸고 ``HELD`` Dispatch를 자동으로 건드리지 않는다 —
     ``HELD`` → 발송 가능은 오직 운영자 ``HeldDisposition.RESUME`` 입력 시.
  ③ 미매핑/미지의 구독 상태는 허용이 아니라 차단으로 판정한다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from rider_server.domain import Subscription, SubscriptionStatus


@dataclass(frozen=True)
class GateDecision:
    """게이트 판정 결과(불변). scheduler·dispatcher·Admin이 각각 다른 필드를 소비한다.

    ``reason`` 은 ``UPPER_SNAKE`` 기계가독 코드(구독 상태명 재사용).
    """

    allow_new_crawl_job: bool
    allow_new_dispatch_job: bool
    warn_admin: bool
    reason: str


class DispatchJobStatus(str, Enum):
    """게이트-facing **최소 부분집합** — 보류/재발송 금지 판정에 필요한 값만.

    전체 ``jobs`` 테이블 status 정본(CLAIMED/RUNNING/FAILED/RETRY 등)·dedup·DeliveryLog
    "성공 기록" 은 Epic 3/4/5 소유이고, 그때 본 부분집합과 reconcile한다.
    ``(str, Enum)`` + 멤버 이름 == 값(대문자) — 2.5 enum 컨벤션 계승(``StrEnum`` 은 3.11+).
    """

    PENDING = "PENDING"
    HELD = "HELD"
    SUCCEEDED = "SUCCEEDED"
    DISCARDED = "DISCARDED"


class HeldDisposition(str, Enum):
    """복구 시 운영자가 ``HELD`` Dispatch에 내리는 결정(AC3)."""

    DISCARD = "DISCARD"
    RESUME = "RESUME"


@dataclass(frozen=True)
class SubscriptionStateChange:
    """중지/복구 전이 기록(불변 값 객체, AC3·FR-30).

    Epic 5가 ``audit_logs`` 로 영속할 메모리상 정본이다. ``reason`` 에는 평문 secret을
    넣지 않는다 — 운영자 입력 사유만.
    """

    subscription_id: str
    from_status: SubscriptionStatus
    to_status: SubscriptionStatus
    reason: str
    changed_at: datetime


# AC1 정본(data-api-contract Subscription execution gate, 116-119).
_GATE_DECISIONS: dict[SubscriptionStatus, GateDecision] = {
    SubscriptionStatus.PAYMENT_ACTIVE: GateDecision(
        allow_new_crawl_job=True, allow_new_dispatch_job=True, warn_admin=False,
        reason="PAYMENT_ACTIVE",
    ),
    SubscriptionStatus.PAYMENT_FAILED_GRACE: GateDecision(
        allow_new_crawl_job=True, allow_new_dispatch_job=True, warn_admin=True,
        reason="PAYMENT_FAILED_GRACE",
    ),
    SubscriptionStatus.SUSPENDED: GateDecision(
        allow_new_crawl_job=False, allow_new_dispatch_job=False, warn_admin=True,
        reason="SUSPENDED",
    ),
    SubscriptionStatus.CANCELLED: GateDecision(
        allow_new_crawl_job=False, allow_new_dispatch_job=False, warn_admin=True,
        reason="CANCELLED",
    ),
}

# fail-closed 불변식 ③: 미매핑/미지 상태의 기본 판정(허용 아님).
_FAIL_CLOSED_DECISION = GateDecision(
    allow_new_crawl_job=False, allow_new_dispatch_job=False, warn_admin=True, reason="UNKNOWN",
)


class SubscriptionGate:
    """구독 상태 기반 실행 게이트 — 순수 정적 정책 함수 모음."""

    @staticmethod
    def evaluate(subscription: Subscription) -> GateDecision:
        """``Subscription`` 의 구독 상태로 신규 작업 예약/전송 허용 여부를 판정한다.

        게이트는 **구독 상태(``SubscriptionStatus``)** 만 평가한다. Tenant lifecycle
        (예: ``SETUP_PENDING``)과의 합성 필터는 scheduler(Story 5.4)가 이 결정 위에
        얹는다 — 본 게이트는 lifecycle 합성을 강제하지 않는다.
        """
        return SubscriptionGate.evaluate_status(subscription.status)

    @staticmethod
    def evaluate_status(status: SubscriptionStatus) -> GateDecision:
        """구독 상태값만으로 게이트 판정(미매핑 상태는 fail-closed 차단)."""
        return _GATE_DECISIONS.get(status, _FAIL_CLOSED_DECISION)

    @staticmethod
    def suspend(
        subscription: Subscription, *, reason: str, at: datetime
    ) -> tuple[Subscription, SubscriptionStateChange]:
        """``SUSPENDED`` 로 전이한 새 구독 + 전이 기록을 반환한다.

        ``dataclasses.replace`` 로 status만 전이하고 나머지 식별·plan·청구주기·쿼터를
        보존한다. secret/profile 참조는 건드리지 않는다(AC1 보존).
        """
        new_subscription = replace(subscription, status=SubscriptionStatus.SUSPENDED)
        change = SubscriptionStateChange(
            subscription_id=subscription.id,
            from_status=subscription.status,
            to_status=SubscriptionStatus.SUSPENDED,
            reason=reason,
            changed_at=at,
        )
        return new_subscription, change

    @staticmethod
    def resume(
        subscription: Subscription,
        *,
        reason: str,
        at: datetime,
        to_status: SubscriptionStatus = SubscriptionStatus.PAYMENT_ACTIVE,
    ) -> tuple[Subscription, SubscriptionStateChange]:
        """복구 전이(기본 ``PAYMENT_ACTIVE``) + 전이 기록을 반환한다.

        불변식 ②: 복구는 구독 상태만 바꾼다 — ``HELD`` Dispatch를 자동으로 건드리지
        않는다(시그니처에 Dispatch 인자 없음).
        """
        new_subscription = replace(subscription, status=to_status)
        change = SubscriptionStateChange(
            subscription_id=subscription.id,
            from_status=subscription.status,
            to_status=to_status,
            reason=reason,
            changed_at=at,
        )
        return new_subscription, change

    @staticmethod
    def hold_undelivered(status: DispatchJobStatus) -> DispatchJobStatus:
        """중지 전환 시 미전송 Dispatch 보류 판정.

        ``PENDING → HELD``, ``HELD → HELD``(멱등), ``SUCCEEDED → SUCCEEDED``(불변식 ① —
        성공분은 절대 보류·되돌림 없음), ``DISCARDED → DISCARDED``.
        """
        if status == DispatchJobStatus.PENDING:
            return DispatchJobStatus.HELD
        # HELD/SUCCEEDED/DISCARDED는 그대로 — 성공분 재발송 금지 포함(불변식 ①).
        return status

    @staticmethod
    def dispose_held(
        status: DispatchJobStatus, disposition: HeldDisposition
    ) -> DispatchJobStatus:
        """복구 시 운영자 결정으로 ``HELD`` 를 처리한다.

        ``(HELD, DISCARD) → DISCARDED``, ``(HELD, RESUME) → PENDING``(재발송 후보 복귀).
        ``HELD`` 가 아닌 입력(특히 ``SUCCEEDED``)은 재발송/오처리 방지를 위해 ``ValueError``
        (fail-closed 불변식 ①).
        """
        if status != DispatchJobStatus.HELD:
            raise ValueError(f"dispose_held requires HELD status, got {status.value}")
        if disposition == HeldDisposition.DISCARD:
            return DispatchJobStatus.DISCARDED
        return DispatchJobStatus.PENDING
