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
from rider_server.scheduler.__main__ import _result_payload, run_once
from rider_server.scheduler.service import DueTarget, SchedulerRepository, TenantGate

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
        )

    async def due_targets(self, *, now):
        return [self.target] if policy.is_due(self.target.next_run_at, now) else []

    async def tenant_gate(self, tenant_id):
        return _ACTIVE_GATE

    async def platform_failure_window(self, platform, *, since, now):
        return (0, 0)

    async def has_active_crawl_job(self, target_id):
        return False

    async def capacity_snapshot(self):
        return policy.CapacityPolicy(
            aggregate_capacity=1,
            aggregate_in_flight=0,
            capabilities=frozenset({JOB_TYPE_CRAWL_BAEMIN}),
        )

    async def claim_due_target(self, target_id, *, now, next_run_at):
        self.target = replace(self.target, next_run_at=next_run_at)
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


def test_compose_defines_scheduler_service() -> None:
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  scheduler:\n" in compose
    assert "python -m rider_server.scheduler" in compose
    assert "./env/backend-api.env" in compose
