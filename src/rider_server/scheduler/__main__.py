"""``python -m rider_server.scheduler`` entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from rider_crawl.redaction import redact
from rider_server.db.base import create_engine, create_session_factory
from rider_server.queue.backend import QueueBackend
from rider_server.queue.postgres_queue import PostgresQueueBackend
from rider_server.scheduler.postgres_repository import PostgresSchedulerRepository
from rider_server.scheduler.service import SchedulerRepository, SchedulerService, TickResult
from rider_server.settings import Settings


def _iso_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _result_payload(result: TickResult) -> dict[str, object]:
    return {
        "enqueued_count": result.enqueued_count,
        "outcomes": [
            {
                "target_id": outcome.target_id,
                "enqueued": outcome.enqueued,
                "reason": outcome.reason,
                "job_id": outcome.job_id,
                "job_type": outcome.job_type,
                "warn_admin": outcome.warn_admin,
            }
            for outcome in result.outcomes
        ],
    }


def _write_health_file(path: str | None, *, now: datetime) -> None:
    if not path:
        return
    health_path = Path(path)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(now.isoformat(), encoding="utf-8")


def _build_postgres_components(settings: Settings) -> tuple[SchedulerRepository, QueueBackend]:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for scheduler")
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    return PostgresSchedulerRepository(session_factory), PostgresQueueBackend(session_factory)


async def run_once(
    *,
    settings: Settings | None = None,
    repo: SchedulerRepository | None = None,
    queue_backend: QueueBackend | None = None,
    now: datetime | None = None,
) -> TickResult:
    settings = settings or Settings.from_env()
    if repo is None or queue_backend is None:
        repo, queue_backend = _build_postgres_components(settings)
    return await SchedulerService().run_tick(
        repo,
        queue_backend,
        now=now or _iso_utc_now(),
    )


async def run_loop(
    *,
    interval_seconds: float,
    settings: Settings | None = None,
    repo: SchedulerRepository | None = None,
    queue_backend: QueueBackend | None = None,
    sleep=None,
    max_ticks: int | None = None,
    health_file: str | None = None,
) -> None:
    settings = settings or Settings.from_env()
    if repo is None or queue_backend is None:
        repo, queue_backend = _build_postgres_components(settings)
    delay = sleep or asyncio.sleep
    health_file = health_file if health_file is not None else os.environ.get("SCHEDULER_HEALTH_FILE")
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        try:
            now = _iso_utc_now()
            result = await SchedulerService().run_tick(repo, queue_backend, now=now)
            print(json.dumps(_result_payload(result), ensure_ascii=False), flush=True)
            _write_health_file(health_file, now=now)
        except Exception as exc:  # noqa: BLE001 - loop boundary; keep scheduler alive.
            print(
                redact(f"scheduler tick failed: {exc.__class__.__name__}: {exc}"),
                flush=True,
            )
        if max_ticks is not None and ticks >= max_ticks:
            break
        await delay(interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rider_server.scheduler")
    parser.add_argument("--once", action="store_true", help="run one scheduler tick and exit")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.environ.get("SCHEDULER_INTERVAL_SECONDS", "30")),
        help="loop interval when --once is not set",
    )
    args = parser.parse_args(argv)

    try:
        if args.once:
            result = asyncio.run(run_once())
            print(json.dumps(_result_payload(result), ensure_ascii=False), flush=True)
            return 0
        asyncio.run(run_loop(interval_seconds=max(1.0, args.interval_seconds)))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary; print safe class/message only.
        print(
            redact(f"scheduler failed: {exc.__class__.__name__}: {exc}"),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
