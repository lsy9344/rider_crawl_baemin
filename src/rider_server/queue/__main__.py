"""``python -m rider_server.queue`` entrypoint."""

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
from rider_server.queue.recovery import RecoveryResult, recover_loop, recover_once
from rider_server.settings import Settings


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _result_payload(result: RecoveryResult) -> dict[str, object]:
    return {
        "recovered_count": result.recovered_count,
        "ran_at": _iso_utc(result.ran_at),
        "batch_size": result.batch_size,
    }


def _write_health_file(path: str | None, *, now: datetime) -> None:
    if not path:
        return
    health_path = Path(path)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(now.isoformat(), encoding="utf-8")


def _build_queue_backend(settings: Settings) -> QueueBackend:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for queue recovery")
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    return PostgresQueueBackend(create_session_factory(engine))


async def run_once(
    *,
    settings: Settings | None = None,
    queue_backend: QueueBackend | None = None,
    now: datetime | None = None,
) -> RecoveryResult:
    settings = settings or Settings.from_env()
    if queue_backend is None:
        queue_backend = _build_queue_backend(settings)
    return await recover_once(
        queue_backend,
        now=now,
        batch_size=settings.job_recovery_batch_size,
    )


async def run_loop(
    *,
    interval_seconds: float,
    settings: Settings | None = None,
    queue_backend: QueueBackend | None = None,
    sleep=None,
    max_ticks: int | None = None,
    health_file: str | None = None,
) -> None:
    settings = settings or Settings.from_env()
    if queue_backend is None:
        queue_backend = _build_queue_backend(settings)
    health_file = health_file if health_file is not None else os.environ.get("QUEUE_RECOVERY_HEALTH_FILE")

    async def _on_result(result: RecoveryResult) -> None:
        print(json.dumps(_result_payload(result), ensure_ascii=False), flush=True)
        await asyncio.to_thread(_write_health_file, health_file, now=result.ran_at)

    async def _on_error(exc: Exception) -> None:
        print(
            redact(f"queue recovery failed: {exc.__class__.__name__}: {exc}"),
            flush=True,
        )

    await recover_loop(
        queue_backend,
        interval_seconds=interval_seconds,
        sleep=sleep,
        max_ticks=max_ticks,
        batch_size=settings.job_recovery_batch_size,
        on_result=_on_result,
        on_error=_on_error,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rider_server.queue")
    parser.add_argument("--once", action="store_true", help="run one stale recovery tick and exit")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.environ.get("QUEUE_RECOVERY_INTERVAL_SECONDS", "30")),
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
            redact(f"queue recovery failed: {exc.__class__.__name__}: {exc}"),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
