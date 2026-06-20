"""Queue stale lease recovery service contract."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from rider_server.queue import (
    JOB_STATUS_CLAIMED,
    JOB_STATUS_PENDING,
    InMemoryQueueBackend,
)
from rider_server.queue import __main__ as queue_main
from rider_server.queue.recovery import recover_once
from rider_server.queue.states import JOB_TYPE_CRAWL_BAEMIN

_T0 = datetime(2025, 1, 1, 0, 0, tzinfo=timezone.utc)


def test_recover_once_recovers_expired_leases_without_claim_route():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=30,
            now=_T0,
        )

        result = await recover_once(backend, now=_T0 + timedelta(seconds=31))

        assert result.recovered_count == 1
        assert result.ran_at == _T0 + timedelta(seconds=31)
        assert backend.job_status(job_id) == JOB_STATUS_PENDING

    asyncio.run(_run())


def test_queue_recovery_run_loop_logs_failure_and_keeps_running(capsys):
    sleeps: list[float] = []

    class _FlakyBackend(InMemoryQueueBackend):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("db temporarily unavailable")
            return 2

    async def _sleep(seconds: float) -> None:
        sleeps.append(seconds)

    backend = _FlakyBackend()
    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=backend,
            sleep=_sleep,
            max_ticks=2,
        )
    )

    assert backend.calls == 2
    assert sleeps == [0.01]
    out = capsys.readouterr().out
    assert "queue recovery failed" in out
    assert "db temporarily unavailable" in out
    assert '"recovered_count": 2' in out


def test_queue_recovery_run_loop_writes_health_file_via_thread(monkeypatch, tmp_path):
    calls: list[tuple[object, tuple, dict]] = []

    async def _to_thread(func, *args, **kwargs):
        calls.append((func, args, kwargs))
        return func(*args, **kwargs)

    monkeypatch.setattr(queue_main.asyncio, "to_thread", _to_thread)
    health_file = tmp_path / "queue-recovery.health"

    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=InMemoryQueueBackend(),
            max_ticks=1,
            health_file=str(health_file),
        )
    )

    assert calls
    assert calls[0][0] is queue_main._write_health_file
    assert health_file.exists()


def test_compose_defines_queue_recovery_service():
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert "\n  queue-recovery:\n" in compose
    assert "python -m rider_server.queue" in compose
    assert "QUEUE_RECOVERY_HEALTH_FILE" in compose


def test_recover_once_leaves_active_leases_claimed():
    async def _run():
        backend = InMemoryQueueBackend()
        job_id = await backend.enqueue(job_type=JOB_TYPE_CRAWL_BAEMIN, now=_T0)
        await backend.claim(
            agent_id="agent-1",
            capabilities=[JOB_TYPE_CRAWL_BAEMIN],
            max_jobs=1,
            lease_seconds=120,
            now=_T0,
        )

        result = await recover_once(backend, now=_T0 + timedelta(seconds=31))

        assert result.recovered_count == 0
        assert backend.job_status(job_id) == JOB_STATUS_CLAIMED

    asyncio.run(_run())


def test_queue_recovery_loop_uses_configured_batch_size() -> None:
    class _RecordingBackend(InMemoryQueueBackend):
        def __init__(self) -> None:
            super().__init__()
            self.batch_sizes: list[int | None] = []

        async def recover_stale(self, *, now: datetime, batch_size: int | None = None) -> int:
            self.batch_sizes.append(batch_size)
            return await super().recover_stale(now=now, batch_size=batch_size)

    backend = _RecordingBackend()

    asyncio.run(
        queue_main.run_loop(
            interval_seconds=0.01,
            queue_backend=backend,
            max_ticks=1,
            settings=queue_main.Settings(
                app_env="test",
                app_version="9.9.9",
                build_sha=None,
                build_time=None,
                job_recovery_batch_size=37,
            ),
        )
    )

    assert backend.batch_sizes == [37]
