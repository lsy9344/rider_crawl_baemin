from __future__ import annotations

import threading
from typing import Callable, Protocol


class StopEvent(Protocol):
    def is_set(self) -> bool:
        ...

    def wait(self, seconds: float) -> bool:
        ...


class BotScheduler:
    def __init__(
        self,
        *,
        run_job: Callable[[], object],
        interval_minutes: int | None = None,
        interval_seconds: int | None = None,
        retry_seconds: int = 5,
    ) -> None:
        if interval_seconds is None:
            if interval_minutes is None:
                raise ValueError("interval_minutes or interval_seconds is required")
            if interval_minutes <= 0:
                raise ValueError("interval_minutes must be greater than zero")
            interval_seconds = interval_minutes * 60
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be greater than zero")
        if retry_seconds <= 0:
            raise ValueError("retry_seconds must be greater than zero")
        self._interval_seconds = interval_seconds
        self._retry_seconds = retry_seconds
        self.run_job = run_job

    @property
    def interval_seconds(self) -> int:
        return self._interval_seconds

    def run_loop(self, *, stop_event: StopEvent | None = None) -> None:
        event = stop_event or threading.Event()
        if event.is_set():
            return

        while True:
            result = self.run_job()
            wait_seconds = self._retry_seconds if result is False else self.interval_seconds
            if event.wait(wait_seconds):
                break
