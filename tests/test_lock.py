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
