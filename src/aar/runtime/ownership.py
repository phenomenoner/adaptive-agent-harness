from __future__ import annotations

import os
from pathlib import Path
from typing import BinaryIO


class RuntimeOwnershipConflict(RuntimeError):
    """Another live MCP host already owns the database runtime generation."""


class RuntimeOwnershipLock:
    """Process-scoped non-blocking ownership for one reference-host database."""

    def __init__(self, database_path: Path) -> None:
        database_path = database_path.resolve()
        self.path = Path(f"{database_path}.runtime.lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._stream: BinaryIO | None = self.path.open("a+b")
        try:
            self._ensure_lock_byte()
            self._lock()
            self._write_owner_marker()
        except BaseException:
            self._stream.close()
            self._stream = None
            raise

    def _ensure_lock_byte(self) -> None:
        assert self._stream is not None
        self._stream.seek(0, os.SEEK_END)
        if self._stream.tell() == 0:
            self._stream.write(b"\0")
            self._stream.flush()

    def _lock(self) -> None:
        assert self._stream is not None
        self._stream.seek(0)
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
            stream.seek(0)
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
