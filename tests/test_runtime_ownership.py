from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from aar.mcp.server import build_server
from aar.runtime.ownership import RuntimeOwnershipConflict, RuntimeOwnershipLock


def test_runtime_ownership_lock_is_mode_0600_under_permissive_umask(tmp_path: Path) -> None:
    database = tmp_path / "mode.sqlite3"
    canonical_lock = tmp_path / "supervisor" / "runtime-owner.lock"
    previous = os.umask(0)
    try:
        ownership = RuntimeOwnershipLock(database)
    finally:
        os.umask(previous)
    try:
        assert stat.S_IMODE(ownership.path.stat().st_mode) == 0o600
        assert stat.S_IMODE(ownership.path.parent.stat().st_mode) == 0o700
        assert ownership.path == canonical_lock
        assert not Path(f"{database}.runtime.lock").exists()
    finally:
        ownership.close()


@pytest.mark.parametrize("variant", ("directory-mode", "directory-symlink", "lock-mode"))
def test_runtime_ownership_rejects_unsafe_private_state(tmp_path: Path, variant: str) -> None:
    database = tmp_path / "runtime.sqlite3"
    private_directory = tmp_path / "supervisor"
    if variant == "directory-symlink":
        if os.name == "nt":
            pytest.skip("Windows symlink creation is privilege-dependent")
        foreign = tmp_path / "foreign"
        foreign.mkdir(mode=0o700)
        private_directory.symlink_to(foreign, target_is_directory=True)
    else:
        private_directory.mkdir(mode=0o700)
        if variant == "directory-mode":
            private_directory.chmod(0o755)
        else:
            lock = private_directory / "runtime-owner.lock"
            lock.write_text("pid=1\n", encoding="ascii")
            lock.chmod(0o644)

    with pytest.raises(RuntimeOwnershipConflict, match=r"owner-only|real directory"):
        RuntimeOwnershipLock(database)


def current_generation(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        row = connection.execute(
            "SELECT generation FROM runtime_meta WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def test_mcp_composition_root_rejects_a_second_live_database_owner(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    first = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        generation = first.host.runtime_generation
        assert current_generation(database) == generation
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            build_server(
                database,
                programmable_backend="plain",
                enable_durable_dispatch=False,
            )
        assert current_generation(database) == generation
    finally:
        first.close()

    replacement = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        assert replacement.host.runtime_generation == generation + 1
    finally:
        replacement.close()


def test_runtime_ownership_is_released_by_hard_process_exit(tmp_path: Path) -> None:
    database = tmp_path / "subprocess.sqlite3"
    script = (
        "from pathlib import Path; import time; "
        "from aar.runtime.ownership import RuntimeOwnershipLock; "
        f"lock=RuntimeOwnershipLock(Path({str(database)!r})); "
        "print('locked', flush=True); time.sleep(60)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            RuntimeOwnershipLock(database)
    finally:
        process.kill()
        process.wait(timeout=10)

    with RuntimeOwnershipLock(database) as ownership:
        assert ownership.path.read_text(encoding="ascii") == f"pid={os.getpid()}\n"
