import os
import time

import pytest

from rider_crawl import lock as lock_module
from rider_crawl.lock import LockAlreadyHeldError, RunLock


def test_run_lock_blocks_nested_execution(tmp_path):
    lock_path = tmp_path / "run.lock"

    with RunLock(lock_path, stale_timeout_seconds=900):
        assert lock_path.exists()
        with pytest.raises(LockAlreadyHeldError):
            with RunLock(lock_path, stale_timeout_seconds=900):
                pass

    assert not lock_path.exists()


def test_run_lock_replaces_stale_lock(tmp_path):
    lock_path = tmp_path / "run.lock"
    stale_time = time.time() - 1000
    lock_path.write_text(str(stale_time), encoding="utf-8")

    with RunLock(lock_path, stale_timeout_seconds=10):
        assert lock_path.exists()
        assert float(lock_path.read_text(encoding="utf-8")) > stale_time


def test_run_lock_treats_new_empty_lock_file_as_held(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text("", encoding="utf-8")

    with pytest.raises(LockAlreadyHeldError):
        with RunLock(lock_path, stale_timeout_seconds=900):
            pass


def test_run_lock_does_not_remove_lock_replaced_by_another_run(tmp_path):
    lock_path = tmp_path / "run.lock"
    lock = RunLock(lock_path, stale_timeout_seconds=900)
    lock.__enter__()
    replacement_time = time.time() + 1
    lock_path.write_text(str(replacement_time), encoding="utf-8")

    lock.__exit__(None, None, None)

    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == str(replacement_time)


def test_run_lock_refresh_keeps_live_owner_from_being_treated_as_stale(tmp_path, monkeypatch):
    lock_path = tmp_path / "run.lock"
    current_time = 1000.0
    monkeypatch.setattr(lock_module.time, "time", lambda: current_time)

    with RunLock(lock_path, stale_timeout_seconds=10) as lock:
        current_time = 1005.0
        lock.refresh()

        current_time = 1014.0
        with pytest.raises(LockAlreadyHeldError):
            with RunLock(lock_path, stale_timeout_seconds=10):
                pass


class _FakePsutil:
    def __init__(self, alive):
        self._alive = alive

    def pid_exists(self, pid):
        return self._alive


def test_run_lock_reclaims_fresh_lock_from_dead_owner(tmp_path, monkeypatch):
    # A crawl process crashed without releasing its lock: the timestamp is still
    # fresh (well within the stale timeout) but the owning PID is gone.
    lock_path = tmp_path / "run.lock"
    lock_path.write_text(str(time.time()), encoding="utf-8")
    lock_module._owner_path(lock_path).write_text(
        f"4242 {lock_module._HOSTNAME}", encoding="utf-8"
    )
    monkeypatch.setattr(lock_module, "psutil", _FakePsutil(alive=False))

    # Despite the fresh timestamp, the dead owner lets the next run reclaim it.
    with RunLock(lock_path, stale_timeout_seconds=900) as lock:
        assert lock._held
        assert lock_path.exists()


def test_run_lock_keeps_fresh_lock_for_live_owner(tmp_path, monkeypatch):
    lock_path = tmp_path / "run.lock"
    lock_path.write_text(str(time.time()), encoding="utf-8")
    lock_module._owner_path(lock_path).write_text(
        f"4242 {lock_module._HOSTNAME}", encoding="utf-8"
    )
    monkeypatch.setattr(lock_module, "psutil", _FakePsutil(alive=True))

    with pytest.raises(LockAlreadyHeldError):
        with RunLock(lock_path, stale_timeout_seconds=900):
            pass


def test_run_lock_ignores_liveness_for_other_host(tmp_path, monkeypatch):
    # An owner recorded on a different host must not be judged by local PIDs.
    lock_path = tmp_path / "run.lock"
    lock_path.write_text(str(time.time()), encoding="utf-8")
    lock_module._owner_path(lock_path).write_text("4242 some-other-host", encoding="utf-8")
    monkeypatch.setattr(lock_module, "psutil", _FakePsutil(alive=False))

    with pytest.raises(LockAlreadyHeldError):
        with RunLock(lock_path, stale_timeout_seconds=900):
            pass


def test_run_lock_writes_and_clears_owner_file(tmp_path):
    lock_path = tmp_path / "run.lock"
    owner_path = lock_module._owner_path(lock_path)

    with RunLock(lock_path, stale_timeout_seconds=900):
        assert owner_path.exists()
        pid, host = lock_module._read_owner(lock_path)
        assert pid == os.getpid()
        assert host == lock_module._HOSTNAME

    assert not lock_path.exists()
    assert not owner_path.exists()
