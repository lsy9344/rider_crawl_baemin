"""Always-run guards for PostgreSQL admin action repository helpers."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rider_server.queue.states import JOB_TYPE_CRAWL_COUPANG
from rider_server.services import admin_action_repository_postgres as repository

_NOW = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
_TARGET = uuid.UUID("22222222-2222-2222-2222-222222222222")
_AGENT = uuid.UUID("33333333-3333-3333-3333-333333333333")
_OTHER_AGENT = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _agent_row(
    agent_id: uuid.UUID,
    *,
    heartbeat_age: timedelta = timedelta(seconds=15),
    capacity_json: dict,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=agent_id,
        last_heartbeat_at=_NOW - heartbeat_age,
        capacity_json=capacity_json,
    )


def _assignment_helper():
    assert hasattr(repository, "_assigned_agent_id_from_agent_capacity")
    return repository._assigned_agent_id_from_agent_capacity


def test_manual_crawl_uses_heartbeat_browser_profile_affinity() -> None:
    assigned = _assignment_helper()(
        [
            _agent_row(
                _AGENT,
                capacity_json={
                    "capabilities": [JOB_TYPE_CRAWL_COUPANG],
                    "browser_profiles": [
                        {
                            "target_id": str(_TARGET),
                            "agent_id": str(_AGENT),
                            "state": "READY",
                        }
                    ],
                },
            )
        ],
        target_id=_TARGET,
        job_type=JOB_TYPE_CRAWL_COUPANG,
        now=_NOW,
    )

    assert assigned == _AGENT


def test_manual_crawl_ignores_stale_heartbeat_browser_profile() -> None:
    assigned = _assignment_helper()(
        [
            _agent_row(
                _AGENT,
                heartbeat_age=timedelta(minutes=3),
                capacity_json={
                    "capabilities": [JOB_TYPE_CRAWL_COUPANG],
                    "browser_profiles": [
                        {"target_id": str(_TARGET), "agent_id": str(_AGENT)}
                    ],
                },
            )
        ],
        target_id=_TARGET,
        job_type=JOB_TYPE_CRAWL_COUPANG,
        now=_NOW,
    )

    assert assigned is None


def test_manual_crawl_falls_back_to_unique_online_capable_agent() -> None:
    assigned = _assignment_helper()(
        [
            _agent_row(
                _AGENT,
                capacity_json={"capabilities": [JOB_TYPE_CRAWL_COUPANG]},
            )
        ],
        target_id=_TARGET,
        job_type=JOB_TYPE_CRAWL_COUPANG,
        now=_NOW,
    )

    assert assigned == _AGENT


def test_manual_crawl_does_not_guess_when_multiple_agents_can_run_job() -> None:
    assigned = _assignment_helper()(
        [
            _agent_row(
                _AGENT,
                capacity_json={"capabilities": [JOB_TYPE_CRAWL_COUPANG]},
            ),
            _agent_row(
                _OTHER_AGENT,
                capacity_json={"capabilities": [JOB_TYPE_CRAWL_COUPANG]},
            ),
        ],
        target_id=_TARGET,
        job_type=JOB_TYPE_CRAWL_COUPANG,
        now=_NOW,
    )

    assert assigned is None
