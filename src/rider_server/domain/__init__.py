"""rider_server 도메인 모델·상태 enum 정본(Story 2.5 / ADD-7·ADD-9, FR-4·FR-30).

``data-api-contract`` 의 8개 핵심 모델(frozen dataclass)과 3개 상태머신(+ 지원 enum)을
순수 정의로 둔다. ``from rider_server.domain import Tenant, CustomerLifecycleState`` 처럼
명시적으로 재노출한다. DB/ORM/Alembic·Pydantic 스키마·wiring은 본 스토리 범위 밖
(Epic 5 / Story 2.7).

Story 3.2/3.3/3.5가 계약 backing record를 additive로 추가했다 — Snapshot(9번째)·
Message(10번째)·DeliveryLog(11번째, 전송 결과·dedup 기록) + DeliveryStatus enum.
Story 3.6이 FailureCategory(error_code 운영 카테고리 7종) + DeliveryStatus 실패/재시도/
보류 멤버(FAILED/RETRYING/HELD)를 additive로 추가했다(새 도메인 레코드는 없음 — enum만).
"""

from __future__ import annotations

from .browser_profile import BrowserProfile
from .delivery_log import DeliveryLog
from .delivery_rule import DeliveryRule
from .message import Message
from .messenger_channel import MessengerChannel
from .monitoring_target import MonitoringTarget
from .platform_account import PlatformAccount
from .secret_ref import SecretRef
from .snapshot import Snapshot
from .states import (
    BaeminAuthState,
    BrowserProfileState,
    CustomerLifecycleState,
    DeliveryStatus,
    FailureCategory,
    Messenger,
    MessengerChannelState,
    MonitoringTargetStatus,
    Platform,
    SecretStorageClass,
    SnapshotQualityState,
    SubscriptionStatus,
)
from .subscription import Subscription
from .tenant import Tenant

__all__ = [
    # 8 핵심 도메인 모델
    "Tenant",
    "Subscription",
    "PlatformAccount",
    "MonitoringTarget",
    "BrowserProfile",
    "MessengerChannel",
    "DeliveryRule",
    "SecretRef",
    # Story 3.2 — 정규화 Snapshot 레코드(9번째)
    "Snapshot",
    # Story 3.3 — Message 렌더 레코드(10번째)
    "Message",
    # Story 3.5 — DeliveryLog 전송 결과·dedup 레코드(11번째)
    "DeliveryLog",
    # 상태머신 enum
    "CustomerLifecycleState",
    "SubscriptionStatus",
    "BaeminAuthState",
    "DeliveryStatus",
    # Story 3.6 — error_code 운영 카테고리(7종, delivery_logs/jobs.error_code 어휘)
    "FailureCategory",
    # 지원 enum
    "Platform",
    "Messenger",
    "SecretStorageClass",
    "MonitoringTargetStatus",
    "MessengerChannelState",
    "BrowserProfileState",
    "SnapshotQualityState",
]
