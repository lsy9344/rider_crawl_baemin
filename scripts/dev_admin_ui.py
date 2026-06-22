"""로컬 전용 Admin UI 미리보기 런처(운영 코드 미변경).

운영 ``main.py`` 의 fail-closed 인증 게이트(``_default_resolve_admin_principal`` → None → 401)를
**로컬에서만** 통과시키기 위해, ``create_app()`` 으로 만든 앱의 ``app.state.resolve_admin_principal``
seam 을 dev principal(SECRET_ADMIN + mfa_verified)로 교체한 뒤 uvicorn 으로 띄운다. 운영 배포는
이 스크립트를 쓰지 않으므로 보안 모델에 영향이 없다.

실행: ``DEV_ADMIN_AUTH_BYPASS=1 DATABASE_URL=... PYTHONPATH=src python scripts/dev_admin_ui.py``
(포트는 ``DEV_ADMIN_PORT``, 기본 8001)
"""

from __future__ import annotations

import os

import uvicorn
from fastapi import Request

from rider_server.main import create_app
from rider_server.security.principal import AdminPrincipal, AdminRole

# audit actor_id 컬럼이 UUID 를 기대하므로 고정 dev UUID 를 쓴다(로그 추적용).
_DEV_ACTOR_ID = "00000000-0000-0000-0000-0000000000de"


def _require_local_bypass_opt_in() -> None:
    app_env = os.environ.get("APP_ENV", "development").lower()
    if app_env == "production":
        raise SystemExit("scripts/dev_admin_ui.py cannot run with APP_ENV=production")
    if os.environ.get("DEV_ADMIN_AUTH_BYPASS") != "1":
        raise SystemExit("Set DEV_ADMIN_AUTH_BYPASS=1 to run the local Admin UI auth bypass")


def _dev_principal(_request: Request) -> AdminPrincipal:
    """로컬 dev 전용 principal — SECRET_ADMIN(OPERATOR+SECRET_ADMIN 게이트 통과) + MFA 통과."""

    return AdminPrincipal(
        actor_id=_DEV_ACTOR_ID,
        role=AdminRole.SECRET_ADMIN,
        mfa_verified=True,
        source="DEV_LOCAL",
    )


def main() -> None:
    _require_local_bypass_opt_in()
    app = create_app()
    # 운영 fail-closed seam 을 로컬에서만 dev principal 로 교체(운영 main.py 미변경).
    app.state.resolve_admin_principal = _dev_principal
    port = int(os.environ.get("DEV_ADMIN_PORT", "8001"))
    print(f"[dev] Admin UI on http://127.0.0.1:{port}/admin (auth bypass: SECRET_ADMIN)")
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
