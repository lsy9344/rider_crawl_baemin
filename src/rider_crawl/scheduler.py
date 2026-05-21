from __future__ import annotations

import threading
from typing import Callable, Protocol


class StopEvent(Protocol):
    def is_set(self) -> bool:
        ...

    def wait(self, seconds: float) -> bool:
        ...


class BotScheduler:
    def __init__(self, *, interval_minutes: int, run_job: Callable[[], None]) -> None:
        if interval_minutes <= 0:
            raise ValueError("interval_minutes must be greater than zero")
        self.interval_minutes = interval_minutes
        self.run_job = run_job

    @property
    def interval_seconds(self) -> int:
        return self.interval_minutes * 60

    def run_loop(self, *, stop_event: StopEvent | None = None) -> None:
        event = stop_event or threading.Event()
        if event.is_set():
            return

        while True:
            self.run_job()
            if event.wait(self.interval_seconds):
                break
