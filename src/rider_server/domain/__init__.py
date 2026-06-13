"""rider_server 도메인 모델·상태 enum 정본(Story 2.5 / ADD-7·ADD-9, FR-4·FR-30).

``data-api-contract`` 의 8개 핵심 모델(frozen dataclass)과 3개 상태머신(+ 지원 enum)을
순수 정의로 둔다. ``from rider_server.domain import Tenant, CustomerLifecycleState`` 처럼
명시적으로 재노출한다. DB/ORM/Alembic·Pydantic 스키마·wiring은 본 스토리 범위 밖
(Epic 5 / Story 2.7).
"""

from __future__ import annotations

from .browser_profile import BrowserProfile
from .delivery_rule import DeliveryRule
from .messenger_channel import MessengerChannel
from .monitoring_target import MonitoringTarget
from .platform_account import PlatformAccount
from .secret_ref import SecretRef
from .snapshot import Snapshot
from .states import (
    BaeminAuthState,
    BrowserProfileState,
    CustomerLifecycleState,
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
    # 상태머신 enum
    "CustomerLifecycleState",
    "SubscriptionStatus",
    "BaeminAuthState",
    # 지원 enum
    "Platform",
    "Messenger",
    "SecretStorageClass",
    "MonitoringTargetStatus",
    "MessengerChannelState",
    "BrowserProfileState",
    "SnapshotQualityState",
]
