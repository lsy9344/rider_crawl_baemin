"""``Subscription`` 도메인 모델(Story 2.5 / AC1) — 플랜·상태·청구주기·쿼터·실행 게이트.

``status`` 는 실행 게이트 enum(``SubscriptionStatus``) — 본 스토리는 값만 들고, 게이트
평가 로직(ACTIVE 아니면 차단)은 Story 2.6 소유다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .states import SubscriptionStatus


@dataclass(frozen=True)
class Subscription:
    id: str
    tenant_id: str  # → Tenant
    plan: str
    status: SubscriptionStatus
    current_period_end: datetime | None = None
    quotas: dict[str, int] = field(default_factory=dict)
