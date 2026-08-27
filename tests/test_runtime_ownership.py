from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from threading import Thread

import pytest

from aar.mcp.server import build_server
from aar.runtime.ownership import RuntimeOwnershipConflict, RuntimeOwnershipLock


def test_windows_runtime_ownership_uses_system_wide_first_instance_pipe() -> None:
    assert RuntimeOwnershipLock._WINDOWS_PIPE_PREFIX == r"\\.\pipe\AAR.RuntimeOwnership."
    assert not hasattr(RuntimeOwnershipLock, "_WINDOWS_MUTEX_PREFIX")


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows pipe semantics")
def test_windows_runtime_ownership_is_not_same_thread_recursive(tmp_path: Path) -> None:
    database = tmp_path / "reference.sqlite3"
    first = RuntimeOwnershipLock(database)
    try:
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            RuntimeOwnershipLock(database)
    finally:
        first.close()


@pytest.mark.skipif(os.name != "nt", reason="requires native Windows pipe semantics")
def test_windows_runtime_ownership_can_close_from_another_thread(tmp_path: Path) -> None:
    database = tmp_path / "reference.sqlite3"
    first = RuntimeOwnershipLock(database)
    errors: list[BaseException] = []

    def close_owner() -> None:
        try:
            first.close()
        except BaseException as error:
            errors.append(error)

    thread = Thread(target=close_owner)
    thread.start()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert errors == []
    with RuntimeOwnershipLock(database):
        pass


def test_runtime_ownership_uses_database_inode_without_auxiliary_artifact(
    tmp_path: Path,
) -> None:
    database = tmp_path / "mode.sqlite3"
    previous = os.umask(0)
    try:
        ownership = RuntimeOwnershipLock(database)
    finally:
        os.umask(previous)
    try:
        assert stat.S_IMODE(ownership.path.stat().st_mode) == 0o600
        assert ownership.path == database.resolve()
        assert not Path(f"{database}.runtime.lock").exists()
        assert not (tmp_path / "supervisor").exists()
        with sqlite3.connect(database) as connection:
            connection.execute("CREATE TABLE probe(value INTEGER NOT NULL)")
            connection.execute("INSERT INTO probe(value) VALUES (42)")
            connection.commit()
            assert connection.execute("SELECT value FROM probe").fetchone() == (42,)
    finally:
        ownership.close()


def test_runtime_ownership_rejects_non_file_database_path(tmp_path: Path) -> None:
    database = tmp_path / "runtime.sqlite3"
    database.mkdir()

    with pytest.raises(RuntimeOwnershipConflict, match="not a regular file"):
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
        assert ownership.path == database.resolve()
        assert ownership.path.is_file()
        assert not Path(f"{database}.runtime.lock").exists()
        assert not (tmp_path / "supervisor").exists()
