"""Load the active Windows Python runtime's OpenSSL DLLs deterministically."""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from pathlib import Path

_WINDOWS_OPENSSL_DLLS = ("libcrypto-3-x64.dll", "libssl-3-x64.dll")
_LOAD_LIBRARY_SEARCH_FLAGS = 0x00001100
_WINDOWS_OPENSSL_HANDLES: tuple[object, ...] = ()


def _load_runtime_openssl(
    *,
    os_name: str,
    base_prefix: Path,
    loader: Callable[..., object],
) -> tuple[object, ...]:
    """Load a complete runtime-local OpenSSL pair in dependency order."""

    if os_name != "nt":
        return ()

    dll_dir = base_prefix / "DLLs"
    dll_paths = tuple(dll_dir / name for name in _WINDOWS_OPENSSL_DLLS)
    if not all(path.is_file() for path in dll_paths):
        return ()

    handles: list[object] = []
    for path in dll_paths:
        try:
            handles.append(
                loader(str(path), winmode=_LOAD_LIBRARY_SEARCH_FLAGS)
            )
        except OSError as exc:
            raise RuntimeError(
                f"failed to preload the active Windows Python runtime DLL: {path}"
            ) from exc
    return tuple(handles)


def preload_windows_openssl() -> None:
    """Prefer trusted runtime DLLs over colliding global Windows DLL names."""

    global _WINDOWS_OPENSSL_HANDLES
    if os.name != "nt" or _WINDOWS_OPENSSL_HANDLES:
        return

    loader = getattr(ctypes, "WinDLL", None)
    if loader is None:
        return
    _WINDOWS_OPENSSL_HANDLES = _load_runtime_openssl(
        os_name=os.name,
        base_prefix=Path(sys.base_prefix),
        loader=loader,
    )