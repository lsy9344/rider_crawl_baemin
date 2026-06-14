"""rider_server FastAPI 앱 — Story 5.1 (AC1·AC2·AC3).

Epic 5 의 첫 실행 가능한 Cloud 백엔드 스캐폴딩. 운영(operational) 엔드포인트
``/health``·``/version``·``/metrics`` 를 **root-level** 로 제공한다(``/v1/`` 접두는
리소스 엔드포인트 전용 — 5.3+). 모든 핸들러는 async 이며(AC3), 에러 응답은 전역
exception handler 로 ``{"error":{"code","message_redacted"}}`` envelope(ADD-13)로 통일한다.

redaction 은 재구현하지 않고 :func:`rider_crawl.redaction.redacted_error_event` 를
재사용한다(단방향 ``rider_server → rider_crawl`` 의존만 허용). DB/queue/scheduler 등은
5.1 범위 밖이라 여기서 와이어링하지 않는다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from rider_crawl.redaction import redacted_error_event

from .admin import admin_actions_router, admin_router
from .admin.actions_routes import _default_resolve_admin_actor
from .admin.dashboard_repository_postgres import PostgresDashboardRepository
from .admin.dashboard_service import DashboardRepository, InMemoryDashboardRepository
from .admin.routes import _default_require_admin_session
from .api import default_resolve_agent_id, jobs_router, telegram_webhook_router
from .db.base import create_engine, create_session_factory
from .queue.backend import QueueBackend
from .queue.memory_queue import InMemoryQueueBackend
from .queue.postgres_queue import PostgresQueueBackend
from .services.admin_action_repository_postgres import PostgresAdminActionRepository
from .services.admin_action_service import (
    AdminActionRepository,
    AdminActionService,
    InMemoryAdminActionRepository,
)
from .services.channel_registration import ChannelRepository, InMemoryChannelRepository
from .services.channel_repository_postgres import PostgresChannelRepository
from .settings import Settings


def _default_queue_backend(settings: Settings) -> QueueBackend:
    """settings 로 기본 backend 를 고른다 — ``DATABASE_URL`` 있으면 PostgreSQL, 없으면 in-memory.

    엔진 생성은 lazy connect 라 import/기동 시 DB 연결을 강제하지 않는다(미설정 환경 안전).
    테스트는 ``create_app(queue_backend=...)`` 로 backend 를 직접 주입한다.
    """

    if settings.database_url:
        engine = create_engine(settings.database_url)
        return PostgresQueueBackend(create_session_factory(engine))
    return InMemoryQueueBackend()


def _default_channel_repository(settings: Settings) -> ChannelRepository:
    """채널 등록/검증/활성 영속 repository 기본값(``_default_queue_backend`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL, 없으면 in-memory(dev/무-DB 안전). 테스트는
    ``create_app(channel_repository=...)`` 로 in-memory fake 를 직접 주입한다.
    """

    if settings.database_url:
        engine = create_engine(settings.database_url)
        return PostgresChannelRepository(create_session_factory(engine))
    return InMemoryChannelRepository()


def _default_admin_action_repository(settings: Settings) -> AdminActionRepository:
    """Admin 액션 write+audit repository 기본값(``_default_dashboard_repository`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL(전이 UPDATE + audit INSERT 동일 트랜잭션), 없으면
    in-memory(dev/무-DB + always-run 테스트 fake). 테스트는 ``create_app(admin_action_service=...)``
    로 in-memory fake 를 직접 주입한다. 상태 전이/DB write 는 5.7 service 소유다.
    """

    if settings.database_url:
        engine = create_engine(settings.database_url)
        return PostgresAdminActionRepository(create_session_factory(engine))
    return InMemoryAdminActionRepository()


def _default_dashboard_repository(settings: Settings) -> DashboardRepository:
    """읽기 전용 대시보드 repository 기본값(``_default_queue_backend`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL 파생 집계 구현, 없으면 in-memory(dev/무-DB 안전). 테스트는
    ``create_app(dashboard_repository=...)`` 로 in-memory fake 를 직접 주입한다. 대시보드는 읽기
    전용이라 이 repository 에 write 메서드가 없다(상태 전이는 5.7 service 소유).
    """

    if settings.database_url:
        engine = create_engine(settings.database_url)
        return PostgresDashboardRepository(create_session_factory(engine))
    return InMemoryDashboardRepository()


def _default_resolve_telegram_secret(settings: Settings):
    """webhook secret 해석 seam 기본값(평문 store 미배선이라 fail-closed → None).

    ``telegram_webhook_secret_ref`` 는 ``*_ref`` 핸들이라 평문 secret 해석에는 secret store 가
    필요하다(5.8+ 배선). 기본값은 평문을 복원할 수 없어 ``None`` 을 반환해 webhook 을 fail-closed
    로 거부한다. 운영/테스트는 ``app.state.resolve_telegram_secret`` 을 실제 해석기로 교체한다.
    """

    def resolve() -> str | None:
        return None

    return resolve


def _iso_utc_now() -> str:
    """현재 시각을 ISO 8601 UTC(``...Z``) 문자열로 — epoch 정수 혼용 금지(ADD-13)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _error_response(
    status_code: int,
    code: str,
    message: str,
    error: BaseException | None = None,
) -> JSONResponse:
    """공통 에러 envelope 응답을 만든다.

    ``redacted_error_event`` 가 돌려주는 flat dict(``{"code","message_redacted"}``)을
    ``{"error": ...}`` envelope 로만 감싼다(마스킹 로직 중복 구현 금지).
    """
    event = redacted_error_event(code, message, error)
    return JSONResponse(status_code=status_code, content={"error": event})


def create_app(
    settings: Settings | None = None,
    *,
    queue_backend: QueueBackend | None = None,
    channel_repository: ChannelRepository | None = None,
    dashboard_repository: DashboardRepository | None = None,
    admin_action_service: AdminActionService | None = None,
) -> FastAPI:
    """FastAPI 앱 팩토리.

    테스트는 fake ``settings``·``queue_backend``(in-memory/PG)·``channel_repository``·
    ``dashboard_repository``·``admin_action_service`` 를 주입할 수 있다(미지정 시 env 로딩 /
    settings 기반 기본값). webhook secret 해석은 ``app.state.resolve_telegram_secret`` seam,
    admin 세션은 ``app.state.require_admin_session`` seam, admin actor 는
    ``app.state.resolve_admin_actor`` seam 으로 주입한다(5.8 이 MFA/4역할/세션으로 교체).
    """
    app = FastAPI(title="rider_server", version="0.1.0")
    app.state.settings = settings or Settings.from_env()
    # 프로세스 기동 시점(단조 시계) — /metrics uptime 계산 기준.
    app.state.start_monotonic = time.monotonic()
    # Agent API queue backend(주입 가능 seam) + bearer→agent_id 해석 seam(5.8 이 교체).
    app.state.queue_backend = queue_backend or _default_queue_backend(app.state.settings)
    app.state.resolve_agent_id = default_resolve_agent_id
    # Story 5.5: 채널 등록/검증/활성 repository + webhook secret 해석 seam(테스트 주입 가능).
    app.state.channel_repository = channel_repository or _default_channel_repository(
        app.state.settings
    )
    app.state.resolve_telegram_secret = _default_resolve_telegram_secret(
        app.state.settings
    )
    # Story 5.6: 읽기 전용 Admin 대시보드 repository + admin 세션 seam(5.8 이 MFA/4역할으로 교체).
    app.state.dashboard_repository = (
        dashboard_repository or _default_dashboard_repository(app.state.settings)
    )
    app.state.require_admin_session = _default_require_admin_session
    # Story 5.7: 수동 운영 액션 service(상태 전이/액션 write+audit) + admin actor seam(5.8 교체).
    app.state.admin_action_service = admin_action_service or AdminActionService(
        _default_admin_action_repository(app.state.settings),
        app.state.queue_backend,
    )
    app.state.resolve_admin_actor = _default_resolve_admin_actor

    # --- 운영 엔드포인트 (root-level, no /v1/) -----------------------------
    @app.get("/health")
    async def health() -> dict:
        """의존성 없는 liveness probe(DB readiness 분리는 5.2+)."""
        return {"status": "ok"}

    @app.get("/version")
    async def version(request: Request) -> dict:
        s: Settings = request.app.state.settings
        body: dict = {"app_version": s.app_version}
        if s.build_sha:
            body["build_sha"] = s.build_sha
        if s.build_time:
            body["build_time"] = s.build_time
        return body

    @app.get("/metrics")
    async def metrics(request: Request) -> dict:
        """최소·확장 가능한 운영 지표(운영 7지표 실집계는 5.9 + DB/queue)."""
        s: Settings = request.app.state.settings
        uptime = max(0.0, time.monotonic() - request.app.state.start_monotonic)
        return {
            "app_version": s.app_version,
            "uptime_seconds": round(uptime, 3),
            "server_time": _iso_utc_now(),
        }

    # --- 전역 에러 envelope (AC2) ------------------------------------------
    @app.exception_handler(StarletteHTTPException)
    async def _http_exc_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # HTTPStatus.name 은 이미 UPPER_SNAKE(NOT_FOUND/UNAUTHORIZED/...).
        try:
            code = HTTPStatus(exc.status_code).name
        except ValueError:
            code = f"HTTP_{exc.status_code}"
        return _error_response(exc.status_code, code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def _validation_exc_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 입력 값(input)은 secret 형일 수 있어 메시지에 넣지 않는다 — 위치/사유만.
        parts = [
            f"{'.'.join(str(p) for p in err.get('loc', ()))}: {err.get('msg', '')}"
            for err in exc.errors()
        ]
        message = "; ".join(parts) or "request validation failed"
        return _error_response(
            HTTPStatus.UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", message
        )

    @app.exception_handler(Exception)
    async def _unhandled_exc_handler(request: Request, exc: Exception) -> JSONResponse:
        # 처리 안 된 예외 — 본문은 redact 통과(secret/OTP 누출 방지), 일반 메시지만 노출.
        return _error_response(
            HTTPStatus.INTERNAL_SERVER_ERROR,
            "INTERNAL_ERROR",
            "internal server error",
            error=exc,
        )

    # --- 리소스 라우트 (/v1/) -----------------------------------------------
    app.include_router(jobs_router)
    app.include_router(telegram_webhook_router)

    # --- Admin UI (HTML, /admin) — 읽기 전용 관측 대시보드(Story 5.6) ----------
    app.include_router(admin_router)
    # --- Admin 수동 운영 액션 (HTML POST, /admin) — 쓰기 라우트(Story 5.7) -------
    app.include_router(admin_actions_router)

    return app


# uvicorn ``rider_server.main:app`` 진입점(운영 정본). 개발 실행은 ``python -m rider_server``.
app = create_app()
