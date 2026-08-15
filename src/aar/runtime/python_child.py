"""Launch Python module children without a Windows virtualenv redirector PID."""

from __future__ import annotations

import os
import sys
from pathlib import Path


class PythonChildLaunchUnavailable(RuntimeError):
    """The active interpreter environment cannot produce an exact child process."""


def _same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.fspath(left)) == os.path.normcase(os.fspath(right))


def _windows_site_packages(launcher: Path) -> Path:
    candidate = launcher.parent.parent / "Lib" / "site-packages"
    if not candidate.is_dir():
        raise PythonChildLaunchUnavailable(
            "the active Windows Python launcher has no adjacent site-packages directory"
        )
    return candidate.resolve(strict=True)


def exact_module_command(
    module: str,
    arguments: list[str] | tuple[str, ...] = (),
    *,
    isolated: bool,
) -> list[str]:
    """Return argv whose process is the Python module owner, including in Windows venvs."""

    if not module or any(character.isspace() for character in module):
        raise ValueError("module must be a non-empty import path")
    launcher = Path(sys.executable).absolute()
    if not launcher.is_file():
        raise PythonChildLaunchUnavailable("the active Python launcher does not exist")
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)
    if os.name != "nt" or _same_file(launcher, base):
        isolation = ["-I"] if isolated else []
        return [os.fspath(launcher), *isolation, "-m", module, *arguments]

    site_packages = _windows_site_packages(launcher)
    environment_root = launcher.parent.parent.absolute()
    bootstrap = (
        "import runpy,site,sys;"
        f"sys.prefix={os.fspath(environment_root)!r};"
        f"sys.exec_prefix={os.fspath(environment_root)!r};"
        f"site.addsitedir({os.fspath(site_packages)!r});"
        f"sys.executable={os.fspath(launcher)!r};"
        f"sys.argv[0]={module!r};"
        f"runpy.run_module({module!r},run_name='__main__')"
    )
    isolation = ["-I"] if isolated else []
    return [os.fspath(base), *isolation, "-S", "-c", bootstrap, *arguments]


__all__ = ["PythonChildLaunchUnavailable", "exact_module_command"]
