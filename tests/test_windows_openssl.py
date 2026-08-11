from __future__ import annotations

from pathlib import Path

import pytest

from aar._windows_openssl import _load_runtime_openssl


def test_runtime_openssl_loader_is_inert_off_windows(tmp_path: Path) -> None:
    calls: list[tuple[str, int]] = []

    def loader(path: str, *, winmode: int) -> object:
        calls.append((path, winmode))
        return object()

    assert (
        _load_runtime_openssl(
            os_name="posix", base_prefix=tmp_path, loader=loader
        )
        == ()
    )
    assert calls == []


def test_runtime_openssl_loader_requires_a_complete_runtime_pair(
    tmp_path: Path,
) -> None:
    dll_dir = tmp_path / "DLLs"
    dll_dir.mkdir()
    (dll_dir / "libcrypto-3-x64.dll").touch()
    calls: list[str] = []

    def loader(path: str, *, winmode: int) -> object:
        calls.append(path)
        return winmode

    assert (
        _load_runtime_openssl(os_name="nt", base_prefix=tmp_path, loader=loader)
        == ()
    )
    assert calls == []


def test_runtime_openssl_loader_uses_runtime_paths_in_dependency_order(
    tmp_path: Path,
) -> None:
    dll_dir = tmp_path / "DLLs"
    dll_dir.mkdir()
    expected_paths = [
        dll_dir / "libcrypto-3-x64.dll",
        dll_dir / "libssl-3-x64.dll",
    ]
    for path in expected_paths:
        path.touch()
    calls: list[tuple[str, int]] = []

    def loader(path: str, *, winmode: int) -> object:
        calls.append((path, winmode))
        return path

    handles = _load_runtime_openssl(
        os_name="nt", base_prefix=tmp_path, loader=loader
    )

    assert handles == tuple(str(path) for path in expected_paths)
    assert calls == [(str(path), 0x00001100) for path in expected_paths]


def test_runtime_openssl_loader_reports_the_failing_runtime_dll(
    tmp_path: Path,
) -> None:
    dll_dir = tmp_path / "DLLs"
    dll_dir.mkdir()
    for name in ("libcrypto-3-x64.dll", "libssl-3-x64.dll"):
        (dll_dir / name).touch()

    def loader(path: str, *, winmode: int) -> object:
        del winmode
        if path.endswith("libssl-3-x64.dll"):
            raise OSError(127, "procedure not found")
        return path

    with pytest.raises(RuntimeError, match=r"libssl-3-x64\.dll"):
        _load_runtime_openssl(os_name="nt", base_prefix=tmp_path, loader=loader)