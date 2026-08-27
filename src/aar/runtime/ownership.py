from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


class RuntimeOwnershipConflict(RuntimeError):
    """Another live MCP host already owns the database runtime generation."""


class RuntimeOwnershipLock:
    """Process-scoped non-blocking ownership for one reference-host database."""

    _WINDOWS_MUTEX_PREFIX = "Local\\AAR.RuntimeOwnership."

    def __init__(self, database_path: Path) -> None:
        self.path = database_path.resolve()
        if self.path.exists() and not self.path.is_file():
            raise RuntimeOwnershipConflict(
                f"reference-host database is not a regular file: {self.path}"
            )
        self._stream = None
        self._windows_mutex = None
        if os.name == "nt":
            self._lock_windows()
        else:
            self._lock_posix()

    def _lock_posix(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(self.path, flags, 0o600)
        except OSError as error:
            raise RuntimeOwnershipConflict(
                f"reference-host database is not a safe regular file: {self.path}"
            ) from error
        try:
            observed = os.fstat(descriptor)
            if not stat.S_ISREG(observed.st_mode):
                raise RuntimeOwnershipConflict(
                    f"reference-host database is not a regular file: {self.path}"
                )
            self._stream = os.fdopen(descriptor, "r+b")
        except BaseException:
            os.close(descriptor)
            raise
        try:
            import fcntl

            fcntl.flock(self._stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._stream.close()
            self._stream = None
            raise RuntimeOwnershipConflict(
                f"reference-host database already has a live owner: {self.path}"
            ) from error
        except BaseException:
            self._stream.close()
            self._stream = None
            raise

    def _lock_windows(self) -> None:
        import ctypes
        from ctypes import wintypes

        win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
        get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
        win_error = ctypes.WinError  # type: ignore[attr-defined]
        kernel32 = win_dll("kernel32", use_last_error=True)
        create_mutex = kernel32.CreateMutexW
        create_mutex.argtypes = (ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR)
        create_mutex.restype = wintypes.HANDLE
        wait_for_single_object = kernel32.WaitForSingleObject
        wait_for_single_object.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        wait_for_single_object.restype = wintypes.DWORD
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        identity = os.path.normcase(str(self.path)).encode("utf-8")
        mutex_name = self._WINDOWS_MUTEX_PREFIX + hashlib.sha256(identity).hexdigest()
        handle = create_mutex(None, False, mutex_name)
        if not handle:
            raise win_error(get_last_error())
        wait_result = wait_for_single_object(handle, 0)
        if wait_result in (0x00000000, 0x00000080):
            self._windows_mutex = handle
            return
        error_code = get_last_error()
        close_handle(handle)
        if wait_result == 0x00000102:
            raise RuntimeOwnershipConflict(
                f"reference-host database already has a live owner: {self.path}"
            )
        raise win_error(error_code)

    def close(self) -> None:
        if os.name == "nt":
            self._close_windows()
            return
        stream = self._stream
        if stream is None:
            return
        self._stream = None
        try:
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()

    def _close_windows(self) -> None:
        handle = self._windows_mutex
        if handle is None:
            return
        self._windows_mutex = None
        import ctypes
        from ctypes import wintypes

        win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
        get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
        win_error = ctypes.WinError  # type: ignore[attr-defined]
        kernel32 = win_dll("kernel32", use_last_error=True)
        release_mutex = kernel32.ReleaseMutex
        release_mutex.argtypes = (wintypes.HANDLE,)
        release_mutex.restype = wintypes.BOOL
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        release_error = None
        if not release_mutex(handle):
            release_error = win_error(get_last_error())
        close_handle(handle)
        if release_error is not None:
            raise release_error

    def __enter__(self) -> RuntimeOwnershipLock:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


__all__ = ["RuntimeOwnershipConflict", "RuntimeOwnershipLock"]
