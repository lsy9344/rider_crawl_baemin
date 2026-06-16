"""DB-less runtime guards for PostgreSQL-only paths."""

from __future__ import annotations

from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_scheduler_due_targets_requires_target_and_account_same_tenant() -> None:
    source = _source("src/rider_server/scheduler/postgres_repository.py")

    assert "PlatformAccount.tenant_id == MonitoringTarget.tenant_id" in source


def test_dashboard_target_health_requires_target_and_account_same_tenant() -> None:
    source = _source("src/rider_server/admin/dashboard_repository_postgres.py")

    assert "PlatformAccount.tenant_id == MonitoringTarget.tenant_id" in source


def test_snapshot_fanout_requires_channel_in_snapshot_tenant() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")

    assert "MessengerChannelRow.tenant_id == _uuid(record.tenant_id)" in source


def test_queue_complete_marks_auth_required_platform_account() -> None:
    source = _source("src/rider_server/queue/postgres_queue.py")

    assert "update(PlatformAccount)" in source
    assert "BaeminAuthState.AUTH_REQUIRED.value" in source
    assert "payload_json.get(\"platform_account_id\")" in source
