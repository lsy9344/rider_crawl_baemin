"""rider_server admin 패키지 — Story 5.6 (Admin 운영 대시보드, 읽기 전용 관측).

JSON Agent API(``api/``)와 분리된 **HTML Admin UI** 레이어다(architecture #API-Boundaries:
"Admin API/UI 는 Agent API 와 인증 경계 분리"). 본 패키지는 **상태를 바꾸지 않는다** — 읽기
전용 집계/렌더만 한다(상태 전이·수동 액션은 5.7, MFA/4역할/audit 는 5.8 소유).

``admin_router`` 를 재노출해 ``create_app`` 이 ``app.include_router(admin_router)`` 로 등록한다.
"""

from __future__ import annotations

from .actions_routes import router as admin_actions_router
from .routes import router as admin_router

__all__ = ["admin_router", "admin_actions_router"]
