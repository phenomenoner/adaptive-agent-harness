from __future__ import annotations

import os
import stat
from pathlib import Path


class RuntimeOwnershipConflict(RuntimeError):
    """Another live MCP host already owns the database runtime generation."""


class RuntimeOwnershipLock:
    """Process-scoped non-blocking ownership for one reference-host database."""

    _WINDOWS_LOCK_OFFSET = 4_096

    def __init__(self, database_path: Path, *, lock_path: Path | None = None) -> None:
        database_path = database_path.resolve()
        requested_lock = Path(f"{database_path}.runtime.lock") if lock_path is None else lock_path
        requested_lock.parent.mkdir(parents=True, exist_ok=True)
        self.path = requested_lock.parent.resolve() / requested_lock.name
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if os.name != "nt":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags, 0o600)
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise RuntimeOwnershipConflict(
                    f"reference-host runtime lock is not a regular file: {self.path}"
                )
            self._stream = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
        try:
            self._lock()
            self._write_owner_marker()
        except BaseException:
            self._stream.close()
            self._stream = None
            raise

    def _lock(self) -> None:
        assert self._stream is not None
        self._stream.seek(self._WINDOWS_LOCK_OFFSET if os.name == "nt" else 0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self._stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeOwnershipConflict(
                f"reference-host database already has a live owner: {self.path}"
            ) from error

    def _write_owner_marker(self) -> None:
        assert self._stream is not None
        marker = f"pid={os.getpid()}\n".encode("ascii")
        self._stream.seek(0)
        self._stream.truncate()
        self._stream.write(marker)
        self._stream.flush()
        os.fsync(self._stream.fileno())
        self._stream.seek(0)

    def close(self) -> None:
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            stream.seek(self._WINDOWS_LOCK_OFFSET if os.name == "nt" else 0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def __enter__(self) -> RuntimeOwnershipLock:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


__all__ = ["RuntimeOwnershipConflict", "RuntimeOwnershipLock"]
