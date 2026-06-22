"""DB-less runtime guards for PostgreSQL-only paths."""

from __future__ import annotations

from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_scheduler_due_targets_requires_target_and_account_same_tenant() -> None:
    source = _source("src/rider_server/scheduler/postgres_repository.py")

    assert "PlatformAccount.tenant_id == MonitoringTarget.tenant_id" in source


def test_scheduler_atomic_claim_enqueue_uses_one_postgres_transaction() -> None:
    source = _source("src/rider_server/scheduler/postgres_repository.py")
    body = source[
        source.index("async def claim_due_target_and_enqueue") : source.index(
            "async def release_due_target"
        )
    ]

    assert "async with session.begin():" in body
    assert ".values(next_run_at=next_run_at, last_enqueued_at=now)" in body
    assert "session.add(" in body
    assert "Job(" in body
    assert "queue_backend.enqueue" not in body


def test_scheduler_postgres_jobs_preserve_browser_profile_affinity() -> None:
    source = _source("src/rider_server/scheduler/postgres_repository.py")
    due_body = source[
        source.index("async def due_targets") : source.index("async def tenant_gate")
    ]
    enqueue_body = source[
        source.index("async def claim_due_target_and_enqueue") : source.index(
            "async def release_due_target"
        )
    ]

    assert "BrowserProfile.agent_id" in due_body
    assert "assigned_agent_id=" in due_body
    assert "assigned_agent_id=" in enqueue_body


def test_admin_manual_enqueue_preserves_browser_profile_affinity() -> None:
    source = _source("src/rider_server/services/admin_action_repository_postgres.py")
    body = source[source.index("async def enqueue_manual_job") :]

    assert "BrowserProfileRow.agent_id" in body
    assert "assigned_agent_id=assigned_agent_id" in body


def test_admin_manual_enqueue_active_guard_includes_retry_without_skip_locked() -> None:
    source = _source("src/rider_server/services/admin_action_repository_postgres.py")
    body = source[source.index("async def enqueue_manual_job") :]

    assert "JOB_STATUS_RETRY" in source
    assert "JOB_STATUS_RETRY" in body
    assert "skip_locked=True" not in body


def test_dashboard_target_health_requires_target_and_account_same_tenant() -> None:
    source = _source("src/rider_server/admin/dashboard_repository_postgres.py")

    assert "PlatformAccount.tenant_id == MonitoringTarget.tenant_id" in source


def test_dashboard_target_health_applies_limit_to_base_query() -> None:
    source = _source("src/rider_server/admin/dashboard_repository_postgres.py")
    target_health_body = source[
        source.index("async def target_health") : source.index(
            "async def _last_collect_successes"
        )
    ]

    assert ".order_by(Tenant.name.asc(), MonitoringTarget.name.asc(), MonitoringTarget.id.asc())" in target_health_body
    assert ".offset(max(0, offset))" in target_health_body
    assert "base_stmt = base_stmt.limit(max(0, limit))" in target_health_body


def test_dashboard_latest_failure_uses_failure_timestamp_not_retry_backoff() -> None:
    source = _source("src/rider_server/admin/dashboard_repository_postgres.py")

    assert "Job.last_failed_at" in source
    assert "func.coalesce(Job.claimed_at, Job.run_after)" not in source


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
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "_attempt_telegram_delivery(" not in enqueue_body


def test_snapshot_complete_does_not_deliver_telegram_inline() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    complete_body = source[
        source.index("async def complete_snapshot_job") : source.index(
            "async def _enqueue_dispatch_records"
        )
    ]

    assert "_deliver_telegram_after_commit" not in complete_body
    assert "_attempt_telegram_delivery(" not in complete_body


def test_dispatch_worker_claims_delivery_logs_with_skip_locked() -> None:
    source = _source("src/rider_server/services/dispatch_worker.py")

    assert "with_for_update(skip_locked=True" in source
    assert "DeliveryLogRow.locked_at.is_(None)" in source
    assert "DeliveryLogRow.locked_at <= now - self._lock_timeout" in source
    assert "DeliveryLogRow.available_at" in source
    # apply_update 본문은 SQLAlchemy update() 를 섀도잉하지 않도록 update_values 파라미터를 쓴다
    # (옛 `update` 파라미터 이름은 `update(DeliveryLogRow)` 를 TypeError 로 깨뜨렸다).
    assert "locked_at=update_values.locked_at" in source
    assert "locked_by=update_values.locked_by" in source


def test_default_snapshot_ingest_enqueues_telegram_as_worker_claimable() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "DeliveryStatus.HELD" not in enqueue_body
    assert "FailureCategory.TELEGRAM_FAILURE" not in enqueue_body


def test_telegram_dispatch_worker_has_explicit_cli_and_compose_service() -> None:
    cli_source = _source("src/rider_server/dispatch/__main__.py")
    compose = _source("deploy/docker-compose.yml")
    runtime = _source("src/rider_server/runtime.py")

    assert "TelegramDispatchWorker" in cli_source
    assert "run_loop" in cli_source
    assert "python -m rider_server.dispatch" in compose
    assert "\n  telegram-dispatch:\n" in compose
    assert "dispatch_worker_factory" in runtime


def test_snapshot_delivery_log_dedup_conflict_does_not_abort_complete() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "on_conflict_do_nothing" in enqueue_body
    assert "DeliveryLogRow.dedup_key" in enqueue_body
    assert "if int(result.rowcount or 0) == 0:" in enqueue_body


def test_snapshot_change_only_rules_skip_unchanged_delivery() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "rule.send_only_on_change" in enqueue_body
    assert "_latest_message_hashes_by_channel(" in enqueue_body
    assert "if previous_hashes.get(channel.id) == message.text_hash:" in enqueue_body


def test_snapshot_enqueue_checks_target_send_window_before_delivery_log_insert() -> None:
    source = _source("src/rider_server/services/snapshot_repository_postgres.py")
    enqueue_body = source[
        source.index("async def _enqueue_dispatch_records") : source.index(
            "def _record_scoped_to_locked_job"
        )
    ]

    assert "MonitoringTargetRow" in source
    assert "_send_window_allows_dispatch(" in enqueue_body
    assert enqueue_body.index("_send_window_allows_dispatch(") < enqueue_body.index(
        "pg_insert(DeliveryLogRow)"
    )


def test_queue_complete_marks_auth_required_platform_account() -> None:
    source = _source("src/rider_server/queue/postgres_queue.py")

    assert "update(PlatformAccount)" in source
    assert "BaeminAuthState.AUTH_REQUIRED.value" in source
    assert "BaeminAuthState.USER_ACTION_PENDING.value" in source
    assert "BaeminAuthState.CENTER_MISMATCH.value" in source
    assert "FailureCategory.TARGET_VALIDATION_FAILURE.value" in source
    assert "payload_json.get(\"platform_account_id\")" in source
