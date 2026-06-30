from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

try:  # psutil is a project dependency; degrade gracefully if it is unavailable.
    import psutil
except Exception:  # pragma: no cover - a missing psutil must never break locking
    psutil = None


_HOSTNAME = socket.gethostname()


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
                # A lock file already exists. Reclaim it only when it is safe:
                # either the owning process is provably gone (it crashed without
                # releasing the lock), or the lock has not been refreshed within
                # the stale timeout. Otherwise treat it as actively held.
                if not _owner_is_dead(self.path):
                    timestamp = _read_lock_timestamp(self.path)
                    if timestamp is None or now - timestamp < self.stale_timeout_seconds:
                        raise LockAlreadyHeldError(f"run lock is already held: {self.path}")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    continue
                _clear_owner(self.path)
                continue

            self._lock_value = str(now)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(self._lock_value)
            _write_owner(self.path)
            self._held = True
            self._start_refresh_thread()
            return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._stop_refresh_thread()
        with self._value_lock:
            lock_value = self._lock_value
        if self._held and _read_lock_value(self.path) == lock_value:
            self.path.unlink()
            _clear_owner(self.path)
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


# --- owner liveness ---------------------------------------------------------
#
# The lock file body stays a single timestamp line (its refresh marker and
# owner token). Process identity is recorded in a sibling ``<lock>.owner`` file
# so the staleness check can be short-circuited when the owning process has
# already died. This is purely additive: when the owner is unknown, on another
# host, or psutil is unavailable, we fall back to the time-based stale timeout.


def _owner_path(path: Path) -> Path:
    return path.with_name(path.name + ".owner")


def _write_owner(path: Path) -> None:
    try:
        _owner_path(path).write_text(f"{os.getpid()} {_HOSTNAME}", encoding="utf-8")
    except OSError:
        pass


def _clear_owner(path: Path) -> None:
    try:
        _owner_path(path).unlink()
    except OSError:
        pass


def _read_owner(path: Path) -> tuple[int | None, str]:
    try:
        parts = _owner_path(path).read_text(encoding="utf-8").split()
    except OSError:
        return None, ""
    if not parts:
        return None, ""
    try:
        pid = int(parts[0])
    except ValueError:
        return None, ""
    host = parts[1] if len(parts) > 1 else ""
    return pid, host


def _owner_is_dead(path: Path) -> bool:
    """Return True only when the owning process is provably gone.

    Conservative by design: any uncertainty (missing owner file, a different
    host, psutil unavailable, or a lookup error) returns ``False`` so the
    time-based stale check still governs. This must never reclaim a lock that a
    live process is holding.
    """

    pid, host = _read_owner(path)
    if pid is None or pid <= 0:
        return False
    if host and host != _HOSTNAME:
        return False
    if psutil is None:
        return False
    try:
        return not psutil.pid_exists(pid)
    except Exception:
        return False
