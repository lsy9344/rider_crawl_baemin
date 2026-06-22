"""rider_server api 패키지 — Story 5.3.

Pydantic v2 경계의 Agent API 라우트(claim/complete/events). 내부(domain/queue)는 중립
dataclass, api 경계는 Pydantic 으로 교차 시 명시적 변환한다(레이어 분리).
"""

from __future__ import annotations

from .agents import router as agents_router
from .jobs import default_resolve_agent_id, router as jobs_router
from .telegram_webhook import router as telegram_webhook_router

__all__ = [
    "agents_router",
    "jobs_router",
    "default_resolve_agent_id",
    "telegram_webhook_router",
]
