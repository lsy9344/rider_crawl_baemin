from __future__ import annotations

import time
from pathlib import Path


class LockAlreadyHeldError(RuntimeError):
    """Raised when another run is still active."""


class RunLock:
    def __init__(self, path: Path, *, stale_timeout_seconds: int) -> None:
        self.path = path
        self.stale_timeout_seconds = stale_timeout_seconds
        self._held = False

    def __enter__(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        now = time.time()

        if self.path.exists():
            timestamp = _read_timestamp(self.path)
            if timestamp is not None and now - timestamp < self.stale_timeout_seconds:
                raise LockAlreadyHeldError(f"run lock is already held: {self.path}")
            self.path.unlink()

        self.path.write_text(str(now), encoding="utf-8")
        self._held = True
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if self._held and self.path.exists():
            self.path.unlink()
        self._held = False


def _read_timestamp(path: Path) -> float | None:
    try:
        return float(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None

