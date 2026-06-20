"""``python -m rider_server.dispatch`` entrypoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.request import urlopen

from rider_crawl.redaction import redact
from rider_server.db.base import create_engine, create_session_factory
from rider_server.domain import MessengerChannel
from rider_server.services.dispatch_fanout_service import DispatchJob
from rider_server.services.dispatch_worker import TelegramDispatchWorker
from rider_server.services.telegram_central_dispatch import CentralTelegramSender
from rider_server.services.tenant_telegram_config import TenantTelegramConfigProvider
from rider_server.settings import Settings


def _iso_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_health_file(path: str | None, *, now: datetime) -> None:
    if not path:
        return
    health_path = Path(path)
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(now.isoformat(), encoding="utf-8")


def _resolve_env_secret_ref(ref: str | None) -> str | None:
    if not ref:
        return None
    prefix, _, name = ref.partition(":")
    if prefix != "env" or not name:
        return None
    return os.environ.get(name) or None


def _build_worker(settings: Settings) -> TelegramDispatchWorker:
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is required for Telegram dispatch")
    engine = create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    session_factory = create_session_factory(engine)
    provider = TenantTelegramConfigProvider(session_factory)

    def resolve_token(channel: MessengerChannel) -> str:
        try:
            cfg = asyncio.run(provider.get(channel.tenant_id))
        except Exception:  # noqa: BLE001 - dispatch falls back to env ref if tenant lookup fails.
            cfg = None
        if cfg is not None and cfg.telegram_bot_token:
            return cfg.telegram_bot_token
        token = _resolve_env_secret_ref(settings.telegram_bot_token_ref)
        if not token:
            raise RuntimeError("telegram bot token is not resolvable")
        return token

    def send(channel: MessengerChannel, job: DispatchJob, text: str) -> None:
        try:
            cfg = asyncio.run(provider.get(channel.tenant_id))
        except Exception:  # noqa: BLE001 - DB failure should fail closed unless global gate allows.
            cfg = None
        sending_enabled = cfg.sending_enabled if cfg is not None else settings.sending_enabled
        if not sending_enabled:
            raise RuntimeError("sending disabled for tenant")
        CentralTelegramSender(
            channels={channel.id: channel},
            resolve_token=resolve_token,
            urlopen=urlopen,
        ).send(job, text)

    return TelegramDispatchWorker(
        telegram_sender=send,
        session_factory=session_factory,
        batch_size=settings.dispatch_batch_size,
        max_attempts=settings.dispatch_max_attempts,
        lock_timeout_seconds=settings.dispatch_lock_timeout_seconds,
    )


async def run_once(
    *,
    settings: Settings | None = None,
    worker: TelegramDispatchWorker | None = None,
    now: datetime | None = None,
) -> int:
    settings = settings or Settings.from_env()
    worker = worker or _build_worker(settings)
    return await worker.run_once(now=now or _iso_utc_now())


async def run_loop(
    *,
    interval_seconds: float,
    settings: Settings | None = None,
    worker: TelegramDispatchWorker | None = None,
    sleep=None,
    max_ticks: int | None = None,
    health_file: str | None = None,
) -> None:
    settings = settings or Settings.from_env()
    worker = worker or _build_worker(settings)
    delay = sleep or asyncio.sleep
    health_file = (
        health_file
        if health_file is not None
        else os.environ.get("TELEGRAM_DISPATCH_HEALTH_FILE")
    )
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        try:
            now = _iso_utc_now()
            processed = await worker.run_once(now=now)
            print(
                json.dumps(
                    {
                        "processed_count": processed,
                        "ran_at": now.isoformat().replace("+00:00", "Z"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            await asyncio.to_thread(_write_health_file, health_file, now=now)
        except Exception as exc:  # noqa: BLE001 - keep the worker loop alive.
            print(
                redact(f"telegram dispatch failed: {exc.__class__.__name__}: {exc}"),
                flush=True,
            )
        if max_ticks is not None and ticks >= max_ticks:
            break
        await delay(interval_seconds)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rider_server.dispatch")
    parser.add_argument("--once", action="store_true", help="run one dispatch tick and exit")
    parser.add_argument(
        "--interval-seconds",
        type=float,
        default=float(os.environ.get("TELEGRAM_DISPATCH_INTERVAL_SECONDS", "5")),
        help="loop interval when --once is not set",
    )
    args = parser.parse_args(argv)

    try:
        if args.once:
            processed = asyncio.run(run_once())
            print(json.dumps({"processed_count": processed}, ensure_ascii=False), flush=True)
            return 0
        asyncio.run(run_loop(interval_seconds=max(1.0, args.interval_seconds)))
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary; print safe class/message only.
        print(
            redact(f"telegram dispatch failed: {exc.__class__.__name__}: {exc}"),
            flush=True,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
