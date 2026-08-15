from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from aar.runtime.python_child import exact_module_command


@pytest.mark.skipif(os.name != "nt", reason="Windows virtualenv redirector contract")
def test_windows_redirector_child_excludes_base_site_packages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    launcher = tmp_path / "venv" / "Scripts" / "python.exe"
    site_packages = tmp_path / "venv" / "Lib" / "site-packages"
    base = tmp_path / "base" / "python.exe"
    launcher.parent.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    base.parent.mkdir(parents=True)
    launcher.write_bytes(b"launcher")
    base.write_bytes(b"base")
    monkeypatch.setattr(sys, "executable", str(launcher))
    monkeypatch.setattr(sys, "_base_executable", str(base), raising=False)

    command = exact_module_command(
        "aar.runtime.ipython_worker",
        ["--example"],
        isolated=True,
    )

    assert command[:3] == [str(base.resolve()), "-I", "-S"]
    assert command[-1] == "--example"
    bootstrap = command[4]
    assert f"sys.executable={str(launcher.absolute())!r}" in bootstrap
    assert f"sys.prefix={str(launcher.parent.parent.absolute())!r}" in bootstrap
    assert f"sys.exec_prefix={str(launcher.parent.parent.absolute())!r}" in bootstrap
    assert f"site.addsitedir({str(site_packages.resolve())!r})" in bootstrap
