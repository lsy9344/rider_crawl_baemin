"""도메인 상태/지원 enum 정본(Story 2.5 / ADD-9, FR-30).

모든 enum은 ``(str, Enum)`` + **멤버 이름 == 값(대문자 문자열)** 으로 둔다. 이렇게 하면
``CustomerLifecycleState.ACTIVE == "ACTIVE"`` 이고 ``json.dumps`` 가 ``"ACTIVE"`` 로
직렬화돼 architecture의 "Python Enum ↔ DB 문자열 일치" 정본과 맞는다. ``StrEnum`` 은
3.11+ 라 ``>=3.10`` 호환을 위해 ``(str, Enum)`` 을 쓴다.
"""

from __future__ import annotations

from enum import Enum


class CustomerLifecycleState(str, Enum):
    """고객 lifecycle 상태머신(data-api-contract). 계약 순서대로 11 멤버.

    ``ACTIVE`` / ``AUTH_REQUIRED`` / ``DEGRADED`` / ``SUSPENDED`` 4개는 MVP에서 서로
    구별되는 별개 멤버다(AC5).
    """

    LEAD = "LEAD"
    SIGNED_UP = "SIGNED_UP"
    PAYMENT_ACTIVE = "PAYMENT_ACTIVE"
    SETUP_PENDING = "SETUP_PENDING"
    PLATFORM_AUTH_PENDING = "PLATFORM_AUTH_PENDING"
    MESSENGER_VERIFY_PENDING = "MESSENGER_VERIFY_PENDING"
    TEST_RUNNING = "TEST_RUNNING"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    SUSPENDED = "SUSPENDED"


class SubscriptionStatus(str, Enum):
    """구독 실행 게이트 상태(data-api-contract). 본 스토리는 **값만** 정의한다 —
    "ACTIVE가 아니면 job 차단" 같은 게이트 평가 로직은 Story 2.6(FR-6) 소유다.
    """

    PAYMENT_ACTIVE = "PAYMENT_ACTIVE"
    PAYMENT_FAILED_GRACE = "PAYMENT_FAILED_GRACE"
    SUSPENDED = "SUSPENDED"
    CANCELLED = "CANCELLED"


class BaeminAuthState(str, Enum):
    """배민 auth state 상태머신(data-api-contract). 7 멤버.

    이 enum의 ``ACTIVE`` 는 ``CustomerLifecycleState.ACTIVE`` 와 **다른 타입의 동명 멤버**다
    (계정 인증 상태 vs 고객 lifecycle). 필드 타입으로 구별한다
    (``PlatformAccount.auth_state`` = ``BaeminAuthState``).
    """

    UNKNOWN = "UNKNOWN"
    ACTIVE = "ACTIVE"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    USER_ACTION_PENDING = "USER_ACTION_PENDING"
    AUTH_VERIFIED = "AUTH_VERIFIED"
    CENTER_MISMATCH = "CENTER_MISMATCH"
    BLOCKED_OR_CAPTCHA = "BLOCKED_OR_CAPTCHA"


class Platform(str, Enum):
    """플랫폼 도메인 enum(대문자 정본).

    **주의:** 이 enum은 ``rider_crawl.platforms`` registry의 소문자 plugin 키
    (``"baemin"``/``"coupang"``)와는 **별개 레이어**(도메인/DB-facing enum vs 실행 registry
    키)다. 본 스토리는 registry를 건드리지 않는다.
    """

    BAEMIN = "BAEMIN"
    COUPANG = "COUPANG"


class Messenger(str, Enum):
    """메신저 도메인 enum(대문자 정본). ``rider_crawl.messengers`` registry 소문자 키와
    별개 레이어 — registry 무변경.
    """

    TELEGRAM = "TELEGRAM"
    KAKAO = "KAKAO"


class SecretStorageClass(str, Enum):
    """secret 저장 위치 분류 도메인 enum(대문자 정본).

    Story 2.4 ``secret_store.py`` 의 소문자 ``central``/``agent_local``/``not_stored`` 와
    **1:1 대응**하지만 **다른 레이어**(도메인/DB-facing enum vs 설정-직렬화 seam)다.
    본 스토리는 enum만 새로 정의하고 2.4 seam을 이 enum으로 갈아끼우지 **않는다** —
    reconcile는 Epic 5 DB/secret 레이어 소유.
    """

    CENTRAL = "CENTRAL"
    AGENT_LOCAL = "AGENT_LOCAL"
    NOT_STORED = "NOT_STORED"


class MonitoringTargetStatus(str, Enum):
    """모니터링 대상 상태. ``INACTIVE`` 는 soft delete(물리 삭제 금지, FR-4) 표현값이다."""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    INACTIVE = "INACTIVE"


class MessengerChannelState(str, Enum):
    """메신저 채널 상태. ``INACTIVE`` 는 soft delete(물리 삭제 금지, FR-4) 표현값이다."""

    PENDING = "PENDING"
    VERIFIED = "VERIFIED"
    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class BrowserProfileState(str, Enum):
    """브라우저 프로필 상태."""

    UNKNOWN = "UNKNOWN"
    READY = "READY"
    IN_USE = "IN_USE"
    INACTIVE = "INACTIVE"


class SnapshotQualityState(str, Enum):
    """Snapshot 데이터 품질 상태(Story 3.2 / P2-02, data-api-contract ``snapshots.quality_state``).

    **값 정의 vs 로직 경계(2.5 ``SubscriptionStatus`` 선례와 동형):** 본 스토리의
    fail-closed 정규화는 필수데이터 누락 시 ``MISSING_REQUIRED`` Snapshot을 **반환하지
    않고 예외(``MissingSnapshotDataError``)를 raise** 한다(AC2). 따라서 ``MISSING_REQUIRED``
    는 정규화 성공 경로에서는 쓰이지 않고, **실패를 기록(persist)할 Epic 5 DB 레이어용
    어휘**로 값만 미리 둔다(2.5가 ``SubscriptionStatus`` 값만 정의하고 게이트 평가는
    2.6에 둔 것과 동일). 정규화 성공 → ``OK``.
    """

    OK = "OK"
    MISSING_REQUIRED = "MISSING_REQUIRED"


class DeliveryStatus(str, Enum):
    """전송 결과 상태(Story 3.5 / P2-05, FR-10, data-api-contract ``delivery_logs.status``).

    **값 정의 vs 로직 경계(2.5 ``SubscriptionStatus``·3.2 ``SnapshotQualityState`` 선례와
    동형):** 본 스토리는 dedup 결과 어휘 **2개만** 정의한다 — ``SENT``(insert-then-send로
    유니크 제약을 먼저 확보한 뒤 성공 전송)과 ``DUPLICATE_BLOCKED``(이미 성공 확보된 dedup
    key라 재전송 안 함, audit 기록). 채널별 실패 카테고리(``telegram_failure``/
    ``kakao_failure``)·``AUTH_REQUIRED``·재시도/보류 같은 **실패 운영 상태는 Story 3.6/
    Epic 5 소유**라 여기에 추가하지 않는다(미래 어휘 선점 금지). ``DUPLICATE_BLOCKED`` 는
    architecture 324-325의 운영 카테고리·359의 ``DUPLICATE_BLOCKED`` 와 정합(대문자 정본).
    """

    SENT = "SENT"
    DUPLICATE_BLOCKED = "DUPLICATE_BLOCKED"
