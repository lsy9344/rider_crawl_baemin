"""Small file locks for agent-local critical sections."""

from __future__ import annotations

import errno
import os
from pathlib import Path
from typing import BinaryIO


class AgentLock:
    """Advisory lock backed by a file in the agent state directory."""

    def __init__(self, state_dir: Path, name: str) -> None:
        self.path = Path(state_dir) / name
        self._fh: BinaryIO | None = None
        self._using_msvcrt = False
        self._using_fcntl = False

    def acquire(self, *, blocking: bool = True) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+b")
        if fh.tell() == 0:
            fh.write(b"\0")
            fh.flush()
        fh.seek(0)
        try:
            self._lock_file(fh, blocking=blocking)
        except BlockingIOError:
            fh.close()
            return False
        except Exception:
            fh.close()
            raise
        self._fh = fh
        return True

    def release(self) -> None:
        fh = self._fh
        if fh is None:
            return
        try:
            fh.seek(0)
            if self._using_msvcrt:
                import msvcrt

                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            elif self._using_fcntl:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        finally:
            fh.close()
            self._fh = None

    def __enter__(self) -> "AgentLock":
        self.acquire(blocking=True)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()

    def _lock_file(self, fh: BinaryIO, *, blocking: bool) -> None:
        if os.name == "nt":
            import msvcrt

            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            try:
                msvcrt.locking(fh.fileno(), mode, 1)
            except OSError as exc:
                if not blocking and exc.errno in (errno.EACCES, errno.EDEADLK):
                    raise BlockingIOError() from exc
                raise
            self._using_msvcrt = True
            return

        import fcntl

        flags = fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.flock(fh.fileno(), flags)
        except OSError as exc:
            if not blocking and exc.errno in (errno.EACCES, errno.EAGAIN):
                raise BlockingIOError() from exc
            raise
        self._using_fcntl = True


def agent_state_lock(state_dir: Path, name: str) -> AgentLock:
    return AgentLock(Path(state_dir), name)
