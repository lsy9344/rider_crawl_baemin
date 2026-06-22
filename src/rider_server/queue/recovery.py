"""Stale queue lease recovery service."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from inspect import isawaitable
from typing import Any

from .backend import QueueBackend


@dataclass(frozen=True)
class RecoveryResult:
    """One stale-lease recovery run result."""

    recovered_count: int
    ran_at: datetime
    batch_size: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def recover_once(
    queue_backend: QueueBackend,
    *,
    now: datetime | None = None,
    batch_size: int | None = None,
) -> RecoveryResult:
    """Recover expired leases once."""

    ran_at = now or _utc_now()
    recovered = await queue_backend.recover_stale(now=ran_at, batch_size=batch_size)
    return RecoveryResult(
        recovered_count=int(recovered or 0),
        ran_at=ran_at,
        batch_size=batch_size,
    )


async def recover_loop(
    queue_backend: QueueBackend,
    *,
    interval_seconds: float,
    now: Callable[[], datetime] | None = None,
    sleep: Callable[[float], Awaitable[None]] | None = None,
    max_ticks: int | None = None,
    batch_size: int | None = None,
    on_result: Callable[[RecoveryResult], Any] | None = None,
    on_error: Callable[[Exception], Any] | None = None,
) -> None:
    """Run stale-lease recovery repeatedly."""

    get_now = now or _utc_now
    delay = sleep or asyncio.sleep
    ticks = 0
    while max_ticks is None or ticks < max_ticks:
        ticks += 1
        try:
            result = await recover_once(queue_backend, now=get_now(), batch_size=batch_size)
            if on_result is not None:
                maybe_awaitable = on_result(result)
                if isawaitable(maybe_awaitable):
                    await maybe_awaitable
        except Exception as exc:  # noqa: BLE001 - loop boundary; caller decides logging.
            if on_error is None:
                raise
            maybe_awaitable = on_error(exc)
            if isawaitable(maybe_awaitable):
                await maybe_awaitable
        if max_ticks is not None and ticks >= max_ticks:
            break
        await delay(interval_seconds)
