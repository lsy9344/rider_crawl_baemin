"""Scheduler CLI entrypoint seams."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from rider_server.domain import CustomerLifecycleState, SubscriptionStatus
from rider_server.queue.memory_queue import InMemoryQueueBackend
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN
from rider_server.scheduler import policy
from rider_server.scheduler import __main__ as scheduler_main
from rider_server.scheduler.__main__ import _result_payload, run_once
from rider_server.scheduler.service import DueTarget, SchedulerRepository, TenantGate
from rider_server.scheduler.service import TickResult
from rider_server.settings import Settings

_NOW = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)
_ACTIVE_GATE = TenantGate(SubscriptionStatus.PAYMENT_ACTIVE, CustomerLifecycleState.ACTIVE)


class _Repo(SchedulerRepository):
    def __init__(self) -> None:
        self.target = DueTarget(
            target_id="target-1",
            tenant_id="tenant-1",
            platform="BAEMIN",
            interval_minutes=10,
            next_run_at=None,
            platform_account_id="account-1",
            primary_url="https://example.invalid/performance",
            expected_display_name="센터A",
            auth_state="ACTIVE",
        )

    async def due_targets(self, *, now, limit):
        due = [self.target] if policy.is_due(self.target.next_run_at, now) else []
        return due[:limit]

    async def tenant_gate(self, tenant_id):
        return _ACTIVE_GATE

    async def tenant_gates(self, tenant_ids):
        return {tenant_id: _ACTIVE_GATE for tenant_id in tenant_ids}

    async def platform_failure_window(self, platform, *, since, now):
        return (0, 0)

    async def has_active_crawl_job(self, target_id):
        return False

    async def active_crawl_job_target_ids(self, target_ids):
        return set()

    async def capacity_snapshot(self, *, now):
        return policy.CapacityPolicy(
            aggregate_capacity=1,
            aggregate_in_flight=0,
            capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN}),
        )

    async def claim_due_target(self, target_id, *, now, next_run_at):
        self.target = replace(self.target, next_run_at=next_run_at)
        return True

    async def release_due_target(self, target_id, *, claimed_next_run_at, restore_next_run_at):
        if self.target.next_run_at != claimed_next_run_at:
            return False
        self.target = replace(self.target, next_run_at=restore_next_run_at)
        return True


def test_run_once_entrypoint_seam_enqueues_crawl_job() -> None:
    backend = InMemoryQueueBackend()

    result = asyncio.run(run_once(repo=_Repo(), queue_backend=backend, now=_NOW))

    assert result.enqueued_count == 1
    outcome = result.outcomes[0]
    assert outcome.job_type == JOB_TYPE_CRAWL_BAEMIN
    job = backend.job_snapshot(outcome.job_id)
    assert job is not None
    assert job.payload_json["primary_url"] == "https://example.invalid/performance"
    assert _result_payload(result)["enqueued_count"] == 1


def test_run_loop_logs_tick_failure_and_keeps_running(monkeypatch, capsys) -> None:
    calls: list[str] = []
    sleeps: list[float] = []
    batch_sizes: list[int] = []

    class _FlakyService:
        def __init__(self, *, due_batch_size: int = 100) -> None:
            batch_sizes.append(due_batch_size)

        async def run_tick(self, repo, queue_backend, *, now):
            calls.append("tick")
            if len(calls) == 1:
                raise RuntimeError("db temporarily unavailable")
            return TickResult(outcomes=(), enqueued_count=0)

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    monkeypatch.setattr(scheduler_main, "SchedulerService", _FlakyService)

    asyncio.run(
        scheduler_main.run_loop(
            interval_seconds=0.01,
            settings=Settings(
                app_env="test",
                app_version="9.9.9",
                build_sha=None,
                build_time=None,
                scheduler_due_batch_size=3,
            ),
            repo=_Repo(),
            queue_backend=InMemoryQueueBackend(),
            sleep=_sleep,
            max_ticks=2,
        )
    )

    assert calls == ["tick", "tick"]
    assert batch_sizes == [3, 3]
    assert sleeps == [0.01]
    out = capsys.readouterr().out
    assert "scheduler tick failed" in out
    assert "db temporarily unavailable" in out
    assert '{"enqueued_count": 0, "outcomes": []}' in out


def test_run_loop_writes_health_file_via_thread(monkeypatch, tmp_path) -> None:
    calls: list[tuple[object, tuple, dict]] = []

    async def _to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(scheduler_main.asyncio, "to_thread", _to_thread)
    health_file = tmp_path / "scheduler.health"

    asyncio.run(
        scheduler_main.run_loop(
            interval_seconds=0.01,
            repo=_Repo(),
            queue_backend=InMemoryQueueBackend(),
            max_ticks=1,
            health_file=str(health_file),
        )
    )

    assert calls
    assert calls[0][0] is scheduler_main._write_health_file
    assert health_file.exists()


def test_compose_defines_scheduler_service() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  scheduler:\n" in compose
    assert "python -m rider_server.scheduler" in compose
    assert "./env/backend-api.env" in compose
