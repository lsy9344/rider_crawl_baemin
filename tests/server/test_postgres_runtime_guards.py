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


def test_snapshot_fanout_requires_channel_in_locked_job_tenant() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")

    assert "record = _record_scoped_to_locked_job(record, job)" in source
    assert '_required_job_payload_text(job, "tenant_id")' in source
    assert "MessengerChannelRow.tenant_id == _uuid(record.tenant_id)" in source
    assert "DeliveryRuleRow.tenant_id == _uuid(record.tenant_id)" in source
    assert "DeliveryRuleRow.tenant_id == MessengerChannelRow.tenant_id" in source


def test_snapshot_enqueue_only_reserves_telegram_delivery_for_after_commit() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "async def _deliver_telegram_after_commit"
        )
    ]

    assert "return pending_telegram_deliveries" in enqueue_body
    assert "_attempt_telegram_delivery(" not in enqueue_body


def test_queue_complete_marks_auth_required_platform_account() -> None:
    source = _source("src/rider_server/queue/postgres_queue.py")

    assert "update(PlatformAccount)" in source
    assert "BaeminAuthState.AUTH_REQUIRED.value" in source
    assert "BaeminAuthState.USER_ACTION_PENDING.value" in source
    assert "BaeminAuthState.CENTER_MISMATCH.value" in source
    assert "FailureCategory.TARGET_VALIDATION_FAILURE.value" in source
    assert "payload_json.get(\"platform_account_id\")" in source
