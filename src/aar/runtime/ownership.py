from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


class RuntimeOwnershipConflict(RuntimeError):
    """Another live MCP host already owns the database runtime generation."""


class RuntimeOwnershipLock:
    """Process-scoped non-blocking ownership for one reference-host database."""

    _WINDOWS_PIPE_PREFIX = r"\\.\pipe\AAR.RuntimeOwnership."

    def __init__(self, database_path: Path) -> None:
        self.path = database_path.resolve()
        if self.path.exists() and not self.path.is_file():
            raise RuntimeOwnershipConflict(
                f"reference-host database is not a regular file: {self.path}"
            )
        self._stream = None
        self._windows_pipe = None
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

        class SecurityAttributes(ctypes.Structure):
            _fields_ = (
                ("length", wintypes.DWORD),
                ("security_descriptor", ctypes.c_void_p),
                ("inherit_handle", wintypes.BOOL),
            )

        class SidAndAttributes(ctypes.Structure):
            _fields_ = (
                ("sid", ctypes.c_void_p),
                ("attributes", wintypes.DWORD),
            )

        class TokenUser(ctypes.Structure):
            _fields_ = (("user", SidAndAttributes),)

        win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
        get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
        win_error = ctypes.WinError  # type: ignore[attr-defined]
        kernel32 = win_dll("kernel32", use_last_error=True)
        advapi32 = win_dll("advapi32", use_last_error=True)

        get_current_process = kernel32.GetCurrentProcess
        get_current_process.argtypes = ()
        get_current_process.restype = wintypes.HANDLE
        open_process_token = advapi32.OpenProcessToken
        open_process_token.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        )
        open_process_token.restype = wintypes.BOOL
        get_token_information = advapi32.GetTokenInformation
        get_token_information.argtypes = (
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        get_token_information.restype = wintypes.BOOL
        convert_sid_to_string = advapi32.ConvertSidToStringSidW
        convert_sid_to_string.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        convert_sid_to_string.restype = wintypes.BOOL
        convert_sddl = advapi32.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert_sddl.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        )
        convert_sddl.restype = wintypes.BOOL
        local_free = kernel32.LocalFree
        local_free.argtypes = (ctypes.c_void_p,)
        local_free.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        if not open_process_token(get_current_process(), 0x0008, ctypes.byref(token)):
            raise win_error(get_last_error())
        sid_text = wintypes.LPWSTR()
        try:
            required = wintypes.DWORD()
            get_token_information(token, 1, None, 0, ctypes.byref(required))
            error_code = get_last_error()
            if required.value == 0 or error_code != 122:
                raise win_error(error_code)
            token_buffer = ctypes.create_string_buffer(required.value)
            if not get_token_information(
                token,
                1,
                token_buffer,
                required.value,
                ctypes.byref(required),
            ):
                raise win_error(get_last_error())
            token_user = ctypes.cast(
                token_buffer, ctypes.POINTER(TokenUser)
            ).contents
            if not convert_sid_to_string(token_user.user.sid, ctypes.byref(sid_text)):
                raise win_error(get_last_error())
            account_sid = sid_text.value
            if not account_sid:
                raise RuntimeError("Windows account SID was empty")
        finally:
            if sid_text:
                local_free(ctypes.cast(sid_text, ctypes.c_void_p))
            close_handle(token)

        security_descriptor = ctypes.c_void_p()
        sddl = f"D:P(A;;0x0012008d;;;{account_sid})"
        if not convert_sddl(sddl, 1, ctypes.byref(security_descriptor), None):
            raise win_error(get_last_error())
        security_attributes = SecurityAttributes(
            ctypes.sizeof(SecurityAttributes),
            security_descriptor,
            False,
        )
        create_named_pipe = kernel32.CreateNamedPipeW
        create_named_pipe.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(SecurityAttributes),
        )
        create_named_pipe.restype = wintypes.HANDLE

        identity = os.path.normcase(str(self.path)).encode("utf-8")
        pipe_name = self._WINDOWS_PIPE_PREFIX + hashlib.sha256(identity).hexdigest()
        try:
            handle = create_named_pipe(
                pipe_name,
                0x00080001,
                0x00000009,
                1,
                0,
                1,
                0,
                ctypes.byref(security_attributes),
            )
            invalid_handle = ctypes.c_void_p(-1).value
            error_code = get_last_error() if handle == invalid_handle else 0
        finally:
            local_free(security_descriptor)
        if handle == invalid_handle:
            if error_code in (5, 183, 231):
                raise RuntimeOwnershipConflict(
                    f"reference-host database already has a live owner: {self.path}"
                )
            raise win_error(error_code)
        if not handle:
            raise win_error(get_last_error())
        self._windows_pipe = handle

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
        handle = self._windows_pipe
        if handle is None:
            return
        self._windows_pipe = None
        import ctypes
        from ctypes import wintypes

        win_dll = ctypes.WinDLL  # type: ignore[attr-defined]
        get_last_error = ctypes.get_last_error  # type: ignore[attr-defined]
        win_error = ctypes.WinError  # type: ignore[attr-defined]
        kernel32 = win_dll("kernel32", use_last_error=True)
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = (wintypes.HANDLE,)
        close_handle.restype = wintypes.BOOL
        if not close_handle(handle):
            raise win_error(get_last_error())

    def __enter__(self) -> RuntimeOwnershipLock:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.close()


__all__ = ["RuntimeOwnershipConflict", "RuntimeOwnershipLock"]
