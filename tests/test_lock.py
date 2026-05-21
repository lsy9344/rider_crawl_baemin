import time

import pytest

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

