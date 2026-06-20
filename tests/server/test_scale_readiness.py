"""Scale-readiness guardrails for crawl fleet operations."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.pool import NullPool

from rider_server.db import base as db_base
from rider_server.db.connection_budget import connection_budget, validate_connection_budget
from rider_server.settings import Settings


def test_settings_reads_database_pool_controls() -> None:
    settings = Settings.from_env(
        {
            "RIDER_DB_POOL_SIZE": "12",
            "RIDER_DB_MAX_OVERFLOW": "8",
            "RIDER_UVICORN_WORKERS": "3",
            "SCHEDULER_DUE_BATCH_SIZE": "250",
        }
    )

    assert settings.db_pool_size == 12
    assert settings.db_max_overflow == 8
    assert settings.uvicorn_workers == 3
    assert settings.scheduler_due_batch_size == 250

    defaults = Settings.from_env({})
    assert defaults.db_pool_size == 5
    assert defaults.db_max_overflow == 10
    assert defaults.uvicorn_workers == 1
    assert defaults.scheduler_due_batch_size == 100


def test_settings_reads_job_lease_seconds_control() -> None:
    settings = Settings.from_env({"RIDER_JOB_LEASE_SECONDS": "180"})

    assert settings.job_lease_seconds == 180
    assert Settings.from_env({}).job_lease_seconds == 120


def test_heartbeat_active_job_dedupe_uses_set_membership_guard() -> None:
    source = Path("src/rider_server/api/agents.py").read_text(encoding="utf-8")

    assert "seen_job_ids: set[str]" in source
    assert "job_id in seen_job_ids" in source


def test_create_engine_passes_pool_kwargs_when_configured(monkeypatch) -> None:
    captured: dict[str, object] = {}
    engine = object()

    def _fake_create_async_engine(database_url: str, **kwargs: object) -> object:
        captured["database_url"] = database_url
        captured["kwargs"] = kwargs
        return engine

    monkeypatch.setattr(db_base, "create_async_engine", _fake_create_async_engine)

    result = db_base.create_engine(
        "postgresql+asyncpg://user:pass@db:5432/rider",
        pool_size=12,
        max_overflow=8,
    )

    assert result is engine
    assert captured == {
        "database_url": "postgresql+asyncpg://user:pass@db:5432/rider",
        "kwargs": {
            "echo": False,
            "future": True,
            "pool_size": 12,
            "max_overflow": 8,
        },
    }


def test_create_engine_keeps_custom_poolclass_paths_clean(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_create_async_engine(database_url: str, **kwargs: object) -> object:
        captured["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(db_base, "create_async_engine", _fake_create_async_engine)

    db_base.create_engine(
        "postgresql+asyncpg://user:pass@db:5432/rider",
        poolclass=NullPool,
        pool_size=12,
        max_overflow=8,
    )

    assert captured["kwargs"] == {
        "echo": False,
        "future": True,
        "poolclass": NullPool,
    }


def test_compose_exposes_scale_runtime_knobs() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    for needle in (
        "RIDER_DB_POOL_SIZE: ${RIDER_DB_POOL_SIZE:-5}",
        "RIDER_DB_MAX_OVERFLOW: ${RIDER_DB_MAX_OVERFLOW:-10}",
        "RIDER_UVICORN_WORKERS: ${RIDER_UVICORN_WORKERS:-1}",
        "RIDER_JOB_LEASE_SECONDS: ${RIDER_JOB_LEASE_SECONDS:-120}",
        "SCHEDULER_INTERVAL_SECONDS: ${SCHEDULER_INTERVAL_SECONDS:-30}",
        "SCHEDULER_DUE_BATCH_SIZE: ${SCHEDULER_DUE_BATCH_SIZE:-100}",
        "QUEUE_RECOVERY_INTERVAL_SECONDS: ${QUEUE_RECOVERY_INTERVAL_SECONDS:-30}",
        "RIDER_JOB_RECOVERY_BATCH_SIZE: ${RIDER_JOB_RECOVERY_BATCH_SIZE:-100}",
        "--workers $${RIDER_UVICORN_WORKERS:-1}",
    ):
        assert needle in compose


def test_compose_exposes_recovery_batch_size() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "RIDER_JOB_RECOVERY_BATCH_SIZE: ${RIDER_JOB_RECOVERY_BATCH_SIZE:-100}" in compose


def test_db_connection_budget_rejects_oversubscription() -> None:
    budget = connection_budget(
        uvicorn_workers=8,
        db_pool_size=15,
        db_max_overflow=10,
        scheduler_processes=1,
        queue_recovery_processes=1,
        dispatch_processes=1,
    )

    assert budget.total_requested == 275
    assert not validate_connection_budget(budget, postgres_max_connections=100).ok


def test_runbook_documents_crawl_scale_operating_model() -> None:
    runbook = Path("docs/runbooks/crawl-scale-runbook.md").read_text(encoding="utf-8")

    for needle in (
        "Agent 기본 동시 처리 1",
        "SCHEDULER_DUE_BATCH_SIZE",
        "lease_seconds",
        "RIDER_JOB_LEASE_SECONDS",
        "heartbeat interval",
        "RIDER_DB_POOL_SIZE",
        "max_connections",
        "CPU/RAM/profile count",
        "rollback",
        "scale smoke commands",
    ):
        assert needle in runbook
