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

from rider_server.admin.dashboard_service import (
    AuthRequiredRow,
    InMemoryDashboardRepository,
    TargetHealthFacts,
)
from rider_server.domain import (
    BaeminAuthState,
    MonitoringTarget,
    MonitoringTargetStatus,
    Platform,
    PlatformAccount,
)
from rider_server.main import create_app
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.security.principal import AdminPrincipal, AdminRole
from rider_server.services.admin_action_service import (
    AdminActionService,
    InMemoryAdminActionRepository,
)
from rider_server.services.agent_registry import InMemoryAgentRegistry
from rider_server.settings import Settings

# audit actor_id 컬럼이 UUID 를 기대하므로 고정 dev UUID 를 쓴다(로그 추적용).
_DEV_ACTOR_ID = "00000000-0000-0000-0000-0000000000de"
_LOCAL_TENANT_ID = "local-tenant"
_LOCAL_TARGET_ID = "local-auth-target"
_LOCAL_ACCOUNT_ID = "local-platform-account"
_LOCAL_AGENT_ID = "00000000-0000-0000-0000-00000000a901"
_LOCAL_REGISTRATION_CODE = "LOCAL-AUTH-AGENT"


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


def _env_text(name: str, default: str) -> str:
    return (os.environ.get(name) or default).strip()


def _local_auth_platform() -> Platform:
    raw = _env_text("DEV_AUTH_PLATFORM", "coupang").casefold()
    return Platform.BAEMIN if raw == "baemin" else Platform.COUPANG


def _seed_local_auth_smoke(
    *,
    action_repo: InMemoryAdminActionRepository,
    dashboard_repo: InMemoryDashboardRepository,
    agent_registry: InMemoryAgentRegistry,
) -> None:
    """로컬 클릭→Agent claim smoke seed.

    실제 계정 비밀은 하드코딩하지 않는다. 필요한 경우 DEV_AUTH_* env 로 주입한다.
    """

    platform = _local_auth_platform()
    platform_name = platform.value.upper()
    target_url = _env_text(
        "DEV_AUTH_TARGET_URL",
        "https://partner.coupangeats.com/page/peak-dashboard"
        if platform is Platform.COUPANG
        else "https://deliverycenter.baemin.com/delivery/report",
    )
    expected_name = _env_text("DEV_AUTH_EXPECTED_NAME", "LOCAL_AUTH_SMOKE")
    account = PlatformAccount(
        id=_LOCAL_ACCOUNT_ID,
        tenant_id=_LOCAL_TENANT_ID,
        platform=platform,
        label=f"local {platform.value} auth smoke",
        username=_env_text("DEV_AUTH_LOGIN_ID", "local-login"),
        password=_env_text("DEV_AUTH_LOGIN_PASSWORD", "local-password"),
        verification_email_address=_env_text(
            "DEV_AUTH_VERIFICATION_EMAIL",
            "local-2fa@example.invalid",
        ),
        verification_email_app_password=_env_text(
            "DEV_AUTH_VERIFICATION_EMAIL_PASSWORD",
            "local-email-password",
        ),
        auth_state=BaeminAuthState.AUTH_REQUIRED,
    )
    target = MonitoringTarget(
        id=_LOCAL_TARGET_ID,
        tenant_id=_LOCAL_TENANT_ID,
        platform_account_id=_LOCAL_ACCOUNT_ID,
        name="로컬 인증 테스트",
        center_name=expected_name,
        url=target_url,
        interval_minutes=10,
        status=MonitoringTargetStatus.ACTIVE,
    )
    action_repo.seed_platform_account(account)
    action_repo.seed_target(target)
    action_repo.seed_target_platform(_LOCAL_TARGET_ID, platform_name)
    dashboard_repo.seed_target(
        TargetHealthFacts(
            target_id=_LOCAL_TARGET_ID,
            tenant_id=_LOCAL_TENANT_ID,
            name=target.name,
            center_name=target.center_name,
            platform=platform_name,
            interval_minutes=target.interval_minutes,
            last_success_at=None,
            last_delivery_at=None,
            last_failure_code=None,
            account_auth_state="AUTH_REQUIRED",
            lifecycle_state="AUTH_REQUIRED",
        )
    )
    dashboard_repo.seed_auth_required(
        AuthRequiredRow(
            tenant_id=_LOCAL_TENANT_ID,
            target_id=_LOCAL_TARGET_ID,
            profile_id=f"profile:{_LOCAL_TARGET_ID}",
            reason="LOCAL_AUTH_SMOKE",
        )
    )
    agent_registry.seed_registration_code(
        _env_text("DEV_AGENT_REGISTRATION_CODE", _LOCAL_REGISTRATION_CODE),
        agent_id=_env_text("DEV_AGENT_ID", _LOCAL_AGENT_ID),
        name="local-auth-agent",
    )


def build_dev_app():
    smoke_enabled = os.environ.get("DEV_AUTH_SMOKE") == "1"
    queue = InMemoryQueueBackend()
    action_repo = InMemoryAdminActionRepository()
    dashboard_repo = InMemoryDashboardRepository()
    agent_registry = InMemoryAgentRegistry()
    if smoke_enabled:
        _seed_local_auth_smoke(
            action_repo=action_repo,
            dashboard_repo=dashboard_repo,
            agent_registry=agent_registry,
        )

    app = create_app(
        Settings(
            app_env=os.environ.get("APP_ENV", "development"),
            app_version=os.environ.get("APP_VERSION", "dev-local"),
            build_sha=None,
            build_time=None,
            database_url=None if smoke_enabled else (os.environ.get("DATABASE_URL") or None),
        ),
        queue_backend=queue,
        dashboard_repository=dashboard_repo,
        admin_action_service=AdminActionService(action_repo, queue),
        agent_registry=agent_registry,
    )
    # 운영 fail-closed seam 을 로컬에서만 dev principal 로 교체(운영 main.py 미변경).
    app.state.resolve_admin_principal = _dev_principal
    return app


def main() -> None:
    _require_local_bypass_opt_in()
    app = build_dev_app()
    port = int(os.environ.get("DEV_ADMIN_PORT", "8001"))
    print(f"[dev] Admin UI on http://127.0.0.1:{port}/admin (auth bypass: SECRET_ADMIN)")
    if os.environ.get("DEV_AUTH_SMOKE") == "1":
        print(
            "[dev] Local auth smoke: "
            f"http://127.0.0.1:{port}/admin?tenant={_LOCAL_TENANT_ID} "
            f"(agent code: {_env_text('DEV_AGENT_REGISTRATION_CODE', _LOCAL_REGISTRATION_CODE)})"
        )
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
