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

import asyncio
import os
import time
from datetime import datetime, timezone
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.request import urlopen

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from rider_crawl.redaction import redacted_error_event

_ADMIN_STATIC_DIR = Path(__file__).parent / "admin" / "static"

from .admin import admin_actions_router, admin_crud_router, admin_router
from .admin.actions_routes import _default_resolve_admin_actor
from .admin.dashboard_repository_postgres import PostgresDashboardRepository
from .admin.dashboard_service import DashboardRepository, InMemoryDashboardRepository
from .admin.routes import _default_require_admin_session
from .api import (
    agents_router,
    jobs_router,
    telegram_webhook_router,
)
from .db.base import create_engine, create_session_factory
from .domain import MessengerChannel
from .metrics.policy import evaluate_alerts
from .metrics.repository_postgres import PostgresMetricsRepository
from .metrics.service import (
    InMemoryMetricsRepository,
    MetricsRepository,
    MetricsService,
)
from .queue.backend import QueueBackend
from .queue.memory_queue import InMemoryQueueBackend
from .queue.postgres_queue import PostgresQueueBackend
from .runtime import RuntimeDeps
from .security.access import _default_resolve_admin_principal
from .security.principal import AdminPrincipal, AdminRole
from .services.admin_action_repository_postgres import PostgresAdminActionRepository
from .services.admin_action_service import (
    AdminActionRepository,
    AdminActionService,
    InMemoryAdminActionRepository,
)
from .services.agent_token_repository_postgres import PostgresAgentTokenRepository
from .services.agent_token_service import (
    AgentTokenRepository,
    AgentTokenService,
    InMemoryAgentTokenRepository,
)
from .services.agent_registry import AgentRegistry, InMemoryAgentRegistry
from .services.agent_registry_postgres import PostgresAgentRegistry
from .services.admin_entity_repository_postgres import PostgresAdminEntityRepository
from .services.admin_entity_service import (
    AdminEntityRepository,
    AdminEntityService,
    InMemoryAdminEntityRepository,
)
from .services.channel_registration import ChannelRepository, InMemoryChannelRepository
from .services.channel_repository_postgres import PostgresChannelRepository
from .services.dispatch_fanout_service import DispatchJob
from .services.dispatch_worker import TelegramDispatchWorker
from .services.job_completion_service import JobCompletionService
from .services.job_result_ingest_service import JobResultIngestService
from .services.snapshot_repository_postgres import PostgresSnapshotIngestRepository
from .services.telegram_central_dispatch import CentralTelegramSender
from .services.tenant_telegram_config import TenantTelegramConfigProvider
from .settings import Settings


def _build_postgres_runtime(settings: Settings) -> tuple[Any | None, Any | None]:
    """Create one lazy PostgreSQL engine/session factory for the app process."""

    if not settings.database_url:
        return None, None
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    return engine, create_session_factory(engine)


def _postgres_session_factory(
    settings: Settings,
    session_factory: Any | None,
) -> Any | None:
    if not settings.database_url:
        return None
    if session_factory is not None:
        return session_factory
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    return create_session_factory(engine)


def _default_queue_backend(
    settings: Settings,
    session_factory: Any | None = None,
) -> QueueBackend:
    """settings 로 기본 backend 를 고른다 — ``DATABASE_URL`` 있으면 PostgreSQL, 없으면 in-memory.

    엔진 생성은 lazy connect 라 import/기동 시 DB 연결을 강제하지 않는다(미설정 환경 안전).
    테스트는 ``create_app(queue_backend=...)`` 로 backend 를 직접 주입한다.
    """

    if settings.database_url:
        return PostgresQueueBackend(_postgres_session_factory(settings, session_factory))
    return InMemoryQueueBackend()


def _default_channel_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> ChannelRepository:
    """채널 등록/검증/활성 영속 repository 기본값(``_default_queue_backend`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL, 없으면 in-memory(dev/무-DB 안전). 테스트는
    ``create_app(channel_repository=...)`` 로 in-memory fake 를 직접 주입한다.
    """

    if settings.database_url:
        return PostgresChannelRepository(_postgres_session_factory(settings, session_factory))
    return InMemoryChannelRepository()


def _default_admin_action_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> AdminActionRepository:
    """Admin 액션 write+audit repository 기본값(``_default_dashboard_repository`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL(전이 UPDATE + audit INSERT 동일 트랜잭션), 없으면
    in-memory(dev/무-DB + always-run 테스트 fake). 테스트는 ``create_app(admin_action_service=...)``
    로 in-memory fake 를 직접 주입한다. 상태 전이/DB write 는 5.7 service 소유다.
    """

    if settings.database_url:
        return PostgresAdminActionRepository(
            _postgres_session_factory(settings, session_factory)
        )
    return InMemoryAdminActionRepository()


def _default_admin_entity_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> AdminEntityRepository:
    """Admin 엔티티 CRUD write+audit repository 기본값(``_default_admin_action_repository`` 와 동형).

    ``DATABASE_URL`` 있으면 PostgreSQL(신규 INSERT/UPDATE + audit INSERT 동일 트랜잭션), 없으면
    in-memory(dev/무-DB + always-run 테스트 fake). 테스트는 ``create_app(admin_entity_service=...)``
    로 in-memory fake 를 직접 주입한다. 엔티티 write 는 5.11 service 소유다(라우트 직접 write 0).
    """

    if settings.database_url:
        return PostgresAdminEntityRepository(
            _postgres_session_factory(settings, session_factory)
        )
    return InMemoryAdminEntityRepository()


def _default_agent_token_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> AgentTokenRepository:
    """Agent token revoke/rotate repository 기본값(``_default_admin_action_repository`` 와 동형).

    ``DATABASE_URL`` 있으면 PostgreSQL(``agents.token_revoked_at`` UPDATE + audit INSERT 동일
    트랜잭션), 없으면 in-memory(dev/무-DB + always-run fake). 테스트는
    ``create_app(agent_token_service=...)`` 로 in-memory fake 를 직접 주입한다.
    """

    if settings.database_url:
        return PostgresAgentTokenRepository(
            _postgres_session_factory(settings, session_factory)
        )
    return InMemoryAgentTokenRepository()


def _default_agent_registry(
    settings: Settings,
    session_factory: Any | None = None,
) -> AgentRegistry:
    """Agent register/heartbeat registry 기본값(``DATABASE_URL`` 있으면 PostgreSQL)."""

    if settings.database_url:
        return PostgresAgentRegistry(_postgres_session_factory(settings, session_factory))
    return InMemoryAgentRegistry()


def _default_job_result_ingest_service(
    settings: Settings,
    session_factory: Any | None = None,
    provider: TenantTelegramConfigProvider | None = None,
) -> JobResultIngestService | None:
    """Agent complete snapshot ingest 기본값.

    ``DATABASE_URL`` 이 있으면 complete 성공 결과를 실제 ``snapshots`` 테이블에 저장한다.
    DB가 없는 개발/테스트 기본값은 no-op(None)으로 둔다.
    """

    if settings.database_url:
        factory = _postgres_session_factory(settings, session_factory)
        return PostgresSnapshotIngestRepository(factory)
    return None


def _default_dashboard_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> DashboardRepository:
    """읽기 전용 대시보드 repository 기본값(``_default_queue_backend`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL 파생 집계 구현, 없으면 in-memory(dev/무-DB 안전). 테스트는
    ``create_app(dashboard_repository=...)`` 로 in-memory fake 를 직접 주입한다. 대시보드는 읽기
    전용이라 이 repository 에 write 메서드가 없다(상태 전이는 5.7 service 소유).
    """

    if settings.database_url:
        return PostgresDashboardRepository(
            _postgres_session_factory(settings, session_factory)
        )
    return InMemoryDashboardRepository()


def _default_metrics_repository(
    settings: Settings,
    session_factory: Any | None = None,
) -> MetricsRepository:
    """읽기 전용 운영 지표 repository 기본값(``_default_dashboard_repository`` 와 동형 선택).

    ``DATABASE_URL`` 있으면 PostgreSQL 파생 집계 구현, 없으면 in-memory(dev/무-DB 안전). 테스트는
    ``create_app(metrics_repository=...)`` 로 in-memory fake 를 직접 주입한다. 지표 레이어는 읽기
    전용이라 이 repository 에 write 메서드가 없다(상태를 바꾸지 않음).
    """

    if settings.database_url:
        return PostgresMetricsRepository(_postgres_session_factory(settings, session_factory))
    return InMemoryMetricsRepository()


def _resolve_env_secret_ref(ref: str | None) -> str | None:
    """Resolve ``env:NAME`` secret refs without storing plaintext in settings."""

    if not ref:
        return None
    prefix, _, name = ref.partition(":")
    if prefix != "env" or not name:
        return None
    return os.environ.get(name) or None


def _default_tenant_telegram_provider(
    settings: Settings,
    session_factory: Any | None = None,
) -> TenantTelegramConfigProvider | None:
    """tenant 별 텔레그램 설정 provider 기본값 — ``DATABASE_URL`` 있으면 PostgreSQL, 없으면 None.

    None 이면 런타임 경로는 env ref(전역)로만 동작한다(무-DB 개발/테스트 안전). 0012 이후 운영
    경로는 이 provider 로 tenant 별 봇 토큰/webhook secret/send 게이트를 읽는다.
    """

    if settings.database_url:
        return TenantTelegramConfigProvider(
            _postgres_session_factory(settings, session_factory)
        )
    return None


def _default_resolve_telegram_secret(
    settings: Settings, provider: TenantTelegramConfigProvider | None = None
):
    """webhook secret 해석 seam 기본값 — tenant DB secret(0012) ∪ env ref(전역 호환).

    단일 webhook 엔드포인트가 본문 파싱 **이전** 에 secret 을 검증해야 하므로(보안 불변식), 검증
    함수는 "유효 secret 집합"을 돌려준다: 모든 tenant 의 ``telegram_webhook_secret`` + env ref
    (``telegram_webhook_secret_ref``). 라우트는 들어온 헤더를 이 집합과 상수시간 비교한다(하나라도
    일치 시 통과, 모두 없으면 fail-closed). 평문 secret 은 반환 외 로그/응답에 싣지 않는다.
    """

    def resolve():
        secrets_set: list[str] = []
        env_secret = _resolve_env_secret_ref(settings.telegram_webhook_secret_ref)
        if env_secret:
            secrets_set.append(env_secret)
        if provider is not None:
            async def _resolve_with_provider() -> list[str]:
                try:
                    return [
                        *secrets_set,
                        *(await provider.list_active_webhook_secrets()),
                    ]
                except Exception:  # noqa: BLE001 - DB 장애가 webhook 처리를 깨지 않도록(env fallback 유지)
                    return secrets_set

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                return asyncio.run(_resolve_with_provider())
            return _resolve_with_provider()
        return secrets_set

    return resolve


def _default_resolve_telegram_token(
    settings: Settings, provider: TenantTelegramConfigProvider | None = None
):
    """bot token 해석 seam 기본값 — tenant DB 토큰(0012) 우선, 없으면 env ref(전역 호환).

    ``resolve(channel)`` 은 ``channel.tenant_id`` 로 tenant 의 ``telegram_bot_token`` 을 조회한다.
    중앙 전송은 ``asyncio.to_thread`` 워커(러닝 루프 없음)에서 호출되므로 ``asyncio.run`` 으로 async
    조회를 안전하게 구동한다. tenant 토큰이 비어 있으면 env ref 로 폴백하고, 둘 다 없으면 fail-closed.
    """

    def resolve(channel: MessengerChannel) -> str:
        if provider is not None:
            try:
                cfg = asyncio.run(provider.get(channel.tenant_id))
            except Exception:  # noqa: BLE001 - DB 조회 실패는 env 폴백으로 넘긴다
                cfg = None
            if cfg is not None and cfg.telegram_bot_token:
                return cfg.telegram_bot_token
        token = _resolve_env_secret_ref(settings.telegram_bot_token_ref)
        if not token:
            raise RuntimeError("telegram bot token is not resolvable (tenant or env)")
        return token

    return resolve


def _tenant_sending_enabled(
    settings: Settings, provider: TenantTelegramConfigProvider | None, tenant_id: str
) -> bool:
    """실발송 게이트(tenant 별, 0012) — tenant ``sending_enabled`` 이 정본, 없으면 env 전역 폴백.

    fail-closed: provider/tenant 미해결이면 env 전역 ``settings.sending_enabled`` 로만 판단한다.
    """

    if provider is not None and tenant_id:
        try:
            cfg = asyncio.run(provider.get(tenant_id))
        except Exception:  # noqa: BLE001 - DB 실패는 전역 폴백
            cfg = None
        if cfg is not None:
            return cfg.sending_enabled
    return settings.sending_enabled


def _default_telegram_sender(
    settings: Settings, provider: TenantTelegramConfigProvider | None = None
):
    # tenant DB provider 가 있으면(운영) tenant 별 게이트/토큰으로 동작하므로 전역 env 게이트가
    # 꺼져 있어도 sender 를 구성한다. provider 가 없으면(무-DB) 기존 env 전역 게이트를 그대로 쓴다.
    if provider is None and (not settings.sending_enabled or not settings.telegram_bot_token_ref):
        return None

    resolve_token = _default_resolve_telegram_token(settings, provider)

    def send(channel: MessengerChannel, job: DispatchJob, text: str) -> None:
        # 발송 직전 tenant 게이트 확인(fail-closed) — OFF 면 실 send 호출 자체를 막는다.
        if not _tenant_sending_enabled(settings, provider, channel.tenant_id):
            raise RuntimeError(
                "sending disabled for tenant (sending_enabled=False) — fail-closed"
            )
        CentralTelegramSender(
            channels={channel.id: channel},
            resolve_token=resolve_token,
            urlopen=urlopen,
        ).send(job, text)

    return send


def _default_dispatch_worker_factory(
    settings: Settings,
    session_factory: Any | None = None,
    provider: TenantTelegramConfigProvider | None = None,
):
    """Return an explicit Telegram dispatch worker factory for CLI/process owners."""

    if not settings.database_url:
        return None
    factory = _postgres_session_factory(settings, session_factory)

    def build() -> TelegramDispatchWorker:
        sender = _default_telegram_sender(settings, provider)
        if sender is None:
            raise RuntimeError("telegram sender is not configured")
        return TelegramDispatchWorker(
            telegram_sender=sender,
            session_factory=factory,
            batch_size=settings.dispatch_batch_size,
            max_attempts=settings.dispatch_max_attempts,
            lock_timeout_seconds=settings.dispatch_lock_timeout_seconds,
        )

    return build


def _iso_utc_now() -> str:
    """현재 시각을 ISO 8601 UTC(``...Z``) 문자열로 — epoch 정수 혼용 금지(ADD-13)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _require_database_for_production(settings: Settings) -> None:
    """운영 환경에서 DB 없는 in-memory fallback 을 막는다."""

    if settings.app_env.strip().lower() == "production" and not settings.database_url:
        raise RuntimeError("DATABASE_URL is required when APP_ENV=production")


def _require_secure_admin_for_production(settings: Settings) -> None:
    """운영 환경에서 IP 제한 없는 public admin 모드를 막는다."""

    if settings.app_env.strip().lower() != "production" or not settings.admin_public_access:
        return
    if settings.admin_ip_allowlist:
        return
    raise RuntimeError(
        "RIDER_ADMIN_PUBLIC_ACCESS requires RIDER_ADMIN_IP_ALLOWLIST when APP_ENV=production"
    )


def _resolve_public_admin_principal(request: Request) -> AdminPrincipal:
    return AdminPrincipal(
        actor_id="00000000-0000-0000-0000-000000000001",
        role=AdminRole.SECRET_ADMIN,
        mfa_verified=True,
        source="ADMIN_PUBLIC_ACCESS",
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
    metrics_repository: MetricsRepository | None = None,
    admin_action_service: AdminActionService | None = None,
    admin_entity_service: AdminEntityService | None = None,
    agent_token_service: AgentTokenService | None = None,
    agent_registry: AgentRegistry | None = None,
    job_result_ingest_service: Any = None,
) -> FastAPI:
    """FastAPI 앱 팩토리.

    테스트는 fake ``settings``·``queue_backend``(in-memory/PG)·``channel_repository``·
    ``dashboard_repository``·``admin_action_service``·``agent_token_service``·``agent_registry`` 를 주입할 수 있다
    (미지정 시 env 로딩 / settings 기반 기본값). webhook secret 해석은
    ``app.state.resolve_telegram_secret`` seam. **Story 5.8 보안 seam**: principal 해석은
    ``app.state.resolve_admin_principal``(기본 fail-closed deny), IP allowlist 는
    ``app.state.admin_ip_allowlist``, Admin POST 추가 허용 Origin 은
    ``app.state.admin_allowed_origins``, MFA 강제는 ``app.state.admin_mfa_required``, 복구
    non-sending 은 ``app.state.sending_enabled``, server-side token revoke/rotate 는
    ``app.state.agent_token_service`` 로 주입·설정한다(``require_admin_session``/
    ``resolve_admin_actor`` 는 principal 위에서 동작).
    """
    app = FastAPI(title="rider_server", version="0.1.0")
    app.state.settings = settings or Settings.from_env()
    _require_database_for_production(app.state.settings)
    _require_secure_admin_for_production(app.state.settings)
    db_engine, db_session_factory = _build_postgres_runtime(app.state.settings)
    app.state.db_engine = db_engine
    app.state.db_session_factory = db_session_factory
    if db_engine is not None:
        async def _dispose_db_engine() -> None:
            await db_engine.dispose()

        app.router.on_shutdown.append(_dispose_db_engine)
    # 프로세스 기동 시점(단조 시계) — /metrics uptime 계산 기준.
    app.state.start_monotonic = time.monotonic()
    # Agent API queue backend(주입 가능 seam) + bearer→agent_id 해석 seam(5.8 이 교체).
    app.state.queue_backend = queue_backend or _default_queue_backend(
        app.state.settings,
        db_session_factory,
    )
    # Story 5.5: 채널 등록/검증/활성 repository + webhook secret 해석 seam(테스트 주입 가능).
    app.state.channel_repository = channel_repository or _default_channel_repository(
        app.state.settings,
        db_session_factory,
    )
    # 0012: tenant 별 텔레그램 설정 provider(DB 있으면 PostgreSQL) — 토큰/webhook secret/send
    # 게이트를 tenant 행에서 읽는다. 무-DB(개발/테스트)면 None → env ref 전역으로만 동작.
    app.state.tenant_telegram_provider = _default_tenant_telegram_provider(
        app.state.settings,
        db_session_factory,
    )
    app.state.resolve_telegram_secret = _default_resolve_telegram_secret(
        app.state.settings, app.state.tenant_telegram_provider
    )
    app.state.resolve_telegram_token = _default_resolve_telegram_token(
        app.state.settings, app.state.tenant_telegram_provider
    )
    # Story 5.6: 읽기 전용 Admin 대시보드 repository + admin 세션 seam(5.8 이 MFA/4역할으로 교체).
    app.state.dashboard_repository = (
        dashboard_repository
        or _default_dashboard_repository(app.state.settings, db_session_factory)
    )
    app.state.require_admin_session = _default_require_admin_session
    # Story 5.9: 읽기 전용 운영 지표 repository(7지표 비식별 fleet 집계) — 테스트 주입 가능 seam.
    app.state.metrics_repository = (
        metrics_repository
        or _default_metrics_repository(app.state.settings, db_session_factory)
    )
    # Story 5.7: 수동 운영 액션 service(상태 전이/액션 write+audit) + admin actor seam(5.8 교체).
    app.state.admin_action_service = admin_action_service or AdminActionService(
        _default_admin_action_repository(app.state.settings, db_session_factory),
        app.state.queue_backend,
    )
    app.state.resolve_admin_actor = _default_resolve_admin_actor
    # Story 5.11: 엔티티 CRUD service(생성/편집/비활성화 write+audit 동일 트랜잭션) — 테스트 주입 가능.
    app.state.admin_entity_service = admin_entity_service or AdminEntityService(
        _default_admin_entity_repository(app.state.settings, db_session_factory)
    )
    # Story 5.8: Admin 접근 보안 — principal 해석 seam(기본 fail-closed deny) + IP allowlist + MFA
    # 강제 토글. server-side token revoke/rotate service + 복구 non-sending 게이트 플래그.
    app.state.resolve_admin_principal = (
        _resolve_public_admin_principal
        if app.state.settings.admin_public_access
        else _default_resolve_admin_principal
    )
    app.state.admin_ip_allowlist = app.state.settings.admin_ip_allowlist
    app.state.admin_allowed_origins = app.state.settings.admin_allowed_origins
    app.state.trusted_proxy_cidrs = app.state.settings.admin_trusted_proxy_cidrs
    app.state.admin_mfa_required = app.state.settings.admin_mfa_required
    app.state.sending_enabled = app.state.settings.sending_enabled
    app.state.agent_token_service = agent_token_service or AgentTokenService(
        _default_agent_token_repository(app.state.settings, db_session_factory)
    )
    app.state.agent_registry = agent_registry or _default_agent_registry(
        app.state.settings,
        db_session_factory,
    )
    app.state.resolve_agent_id = app.state.agent_registry.resolve_agent_id
    app.state.job_result_ingest_service = (
        job_result_ingest_service
        if job_result_ingest_service is not None
        else _default_job_result_ingest_service(
            app.state.settings,
            db_session_factory,
            app.state.tenant_telegram_provider,
        )
    )
    app.state.job_completion_service = JobCompletionService(
        queue_backend=app.state.queue_backend,
        ingest_service=app.state.job_result_ingest_service,
    )
    app.state.dispatch_worker_factory = _default_dispatch_worker_factory(
        app.state.settings,
        db_session_factory,
        app.state.tenant_telegram_provider,
    )
    app.state.container = RuntimeDeps(
        settings=app.state.settings,
        db_engine=app.state.db_engine,
        db_session_factory=app.state.db_session_factory,
        queue_backend=app.state.queue_backend,
        channel_repository=app.state.channel_repository,
        tenant_telegram_provider=app.state.tenant_telegram_provider,
        dashboard_repository=app.state.dashboard_repository,
        metrics_repository=app.state.metrics_repository,
        admin_action_service=app.state.admin_action_service,
        admin_entity_service=app.state.admin_entity_service,
        agent_token_service=app.state.agent_token_service,
        agent_registry=app.state.agent_registry,
        job_result_ingest_service=app.state.job_result_ingest_service,
        job_completion_service=app.state.job_completion_service,
        dispatch_worker_factory=app.state.dispatch_worker_factory,
    )

    # --- 운영 엔드포인트 (root-level, no /v1/) -----------------------------
    @app.get("/health")
    async def health() -> dict:
        """의존성 없는 liveness probe(DB readiness 분리는 5.2+)."""
        return {"status": "ok"}

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon() -> Response:
        """브라우저 기본 favicon 요청을 조용히 처리한다."""
        return Response(status_code=HTTPStatus.NO_CONTENT)

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

    @app.get("/metrics/operational")
    async def metrics_operational(request: Request) -> dict:
        """운영 7지표 비식별 fleet 집계 + 발화 알림(Story 5.9, AC1·AC2).

        DB 의존이라 dependency-free 인 ``/metrics``·``/health`` 와 **별도 엔드포인트**로 둔다
        (DB 장애가 liveness 를 깨지 않게 분리). payload 는 집계 수치(count/rate/gauge)만 —
        tenant_id·고객명·센터/상점명·target 식별 텍스트를 노출하지 않는다(unauthenticated
        scrape 안전). 시각은 실 ``now`` 사용(주입 아님 — 5.6/5.7 라우트 선례); 시간 의존 단정은
        순수 policy/service 레이어가 잠근다.
        """
        repo: MetricsRepository = request.app.state.metrics_repository
        now = datetime.now(timezone.utc)
        snapshot = await MetricsService().snapshot(repo, now=now)
        alerts = evaluate_alerts(snapshot, now=now)
        return {
            "server_time": _iso_utc_now(),
            "metrics": snapshot.to_payload(),
            "alerts": [{"code": a.code, "severity": a.severity} for a in alerts],
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
    app.include_router(agents_router)
    app.include_router(jobs_router)
    app.include_router(telegram_webhook_router)

    # --- Admin UI static assets ---------------------------------------------
    app.mount(
        "/admin/static",
        StaticFiles(directory=str(_ADMIN_STATIC_DIR)),
        name="admin-static",
    )
    # --- Admin UI (HTML, /admin) — 읽기 전용 관측 대시보드(Story 5.6) ----------
    app.include_router(admin_router)
    # --- Admin 수동 운영 액션 (HTML POST, /admin) — 쓰기 라우트(Story 5.7) -------
    app.include_router(admin_actions_router)
    # --- Admin 엔티티 CRUD (HTML GET/POST, /admin) — 생성/편집/비활성화(Story 5.11) -----
    app.include_router(admin_crud_router)

    return app


# uvicorn ``rider_server.main:app`` 진입점(운영 정본). 개발 실행은 ``python -m rider_server``.
app = create_app()
