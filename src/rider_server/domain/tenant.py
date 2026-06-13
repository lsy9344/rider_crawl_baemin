"""``Tenant`` 도메인 모델(Story 2.5 / AC1) — 구독 고객 조직."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .states import CustomerLifecycleState


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    status: CustomerLifecycleState
    created_at: datetime  # 자동 now() 기본값 금지 — 순수·결정적, 호출부가 주입
