"""Typed runtime dependency container for the FastAPI app."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from rider_server.queue.backend import QueueBackend
from rider_server.settings import Settings


@dataclass(frozen=True)
class RuntimeDeps:
    settings: Settings
    db_engine: Any
    db_session_factory: Any
    queue_backend: QueueBackend
    channel_repository: Any
    tenant_telegram_provider: Any
    dashboard_repository: Any
    metrics_repository: Any
    admin_action_service: Any
    admin_entity_service: Any
    agent_token_service: Any
    agent_registry: Any
    job_result_ingest_service: Any
    job_completion_service: Any
    dispatch_worker_factory: Any
