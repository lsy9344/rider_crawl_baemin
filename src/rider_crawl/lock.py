from __future__ import annotations

import os
import threading
import time
from pathlib import Path


class LockAlreadyHeldError(RuntimeError):
    """Raised when another run is still active."""


class RunLock:
    def __init__(self, path: Path, *, stale_timeout_seconds: int) -> None:
        self.path = path
        self.stale_timeout_seconds = stale_timeout_seconds
        self._held = False
        self._lock_value = ""
        self._value_lock = threading.Lock()
        self._refresh_stop = threading.Event()
        self._refresh_thread: threading.Thread | None = None

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            now = time.time()
            try:
                descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
            except FileExistsError:
                timestamp = _read_lock_timestamp(self.path)
                if timestamp is None or now - timestamp < self.stale_timeout_seconds:
                    raise LockAlreadyHeldError(f"run lock is already held: {self.path}")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                continue

            self._lock_value = str(now)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(self._lock_value)
            self._held = True
            self._start_refresh_thread()
            return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop_refresh_thread()
        with self._value_lock:
            lock_value = self._lock_value
        if self._held and _read_lock_value(self.path) == lock_value:
            self.path.unlink()
        self._held = False
        with self._value_lock:
            self._lock_value = ""

    def refresh(self) -> None:
        with self._value_lock:
            lock_value = self._lock_value
        if not self._held or not lock_value:
            return
        if _read_lock_value(self.path) != lock_value:
            return

        next_value = str(time.time())
        try:
            self.path.write_text(next_value, encoding="utf-8")
        except OSError:
            return
        with self._value_lock:
            if self._lock_value == lock_value:
                self._lock_value = next_value

    def _start_refresh_thread(self) -> None:
        self._refresh_stop.clear()
        interval_seconds = min(30.0, max(0.05, self.stale_timeout_seconds / 3))
        self._refresh_thread = threading.Thread(
            target=self._refresh_loop,
            args=(interval_seconds,),
            daemon=True,
        )
        self._refresh_thread.start()

    def _stop_refresh_thread(self) -> None:
        self._refresh_stop.set()
        if self._refresh_thread is not None:
            self._refresh_thread.join(timeout=1)
        self._refresh_thread = None

    def _refresh_loop(self, interval_seconds: float) -> None:
        while not self._refresh_stop.wait(interval_seconds):
            self.refresh()


def _read_timestamp(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_lock_timestamp(path: Path) -> float | None:
    timestamp = _read_timestamp(path)
    if timestamp is not None:
        return timestamp
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _read_lock_value(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

