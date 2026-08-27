from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from aar.admin import build_parser, main
from aar.canonical import canonical_json_bytes
from aar.provider_ready_runtime_models import ActivationReadback
from aar.runtime.operator import (
    _sqlite_read_only,
)
from aar.runtime.registry import OperationRegistry

ROOT = Path(__file__).resolve().parents[1]
VALID = ROOT / "tests" / "fixtures" / "provider-ready" / "valid"


def _snapshot(root: Path) -> dict[str, tuple[int, str]]:
    if not root.exists():
        return {}
    result: dict[str, tuple[int, str]] = {}
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        content = path.read_bytes()
        result[path.relative_to(root).as_posix()] = (
            len(content),
            hashlib.sha256(content).hexdigest(),
        )
    return result


def _v5_runtime_home(tmp_path: Path) -> Path:
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir()
    registry = OperationRegistry(runtime_home / "reference.sqlite3", now_ms=lambda: 1)
    registry.close()
    connection = sqlite3.connect(runtime_home / "reference.sqlite3", isolation_level=None)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        connection.execute("PRAGMA journal_mode=DELETE")
    finally:
        connection.close()
    return runtime_home


def test_read_only_sqlite_canaries_deny_write_and_checkpoint(tmp_path: Path) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    database = runtime_home / "reference.sqlite3"
    before = _snapshot(runtime_home)
    connection = _sqlite_read_only(database)
    try:
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("UPDATE runtime_meta SET generation=9 WHERE singleton=1")
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    assert _snapshot(runtime_home) == before


def test_activation_status_is_strict_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    before = _snapshot(runtime_home)

    assert main(["activation", "status", "--runtime-home", str(runtime_home)]) == 0

    captured = capsys.readouterr()
    readback = ActivationReadback.model_validate_json(captured.out, strict=True)
    assert readback.state == "migration_required"
    assert readback.reason_code == "REGISTRY_VERSION_UNSUPPORTED"
    assert captured.out.encode() == canonical_json_bytes(readback) + b"\n"
    assert captured.err == ""
    assert _snapshot(runtime_home) == before


def test_activation_verify_rejects_noninstalled_runtime_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    before = _snapshot(runtime_home)

    assert (
        main(
            [
                "activation",
                "verify",
                "--runtime-home",
                str(runtime_home),
                "--profile",
                str(VALID / "final-profile.json"),
            ]
        )
        == 2
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "FRESH_INSTALL_READBACK_FAILED" in captured.err
    assert _snapshot(runtime_home) == before


def test_invalid_profile_and_forbidden_transition_commands_fail_before_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    invalid_profile = tmp_path / "invalid-profile.json"
    invalid_profile.write_text("{}")
    before = _snapshot(runtime_home)

    assert (
        main(
            [
                "activation",
                "verify",
                "--runtime-home",
                str(runtime_home),
                "--profile",
                str(invalid_profile),
            ]
        )
        == 2
    )
    assert "ACTIVATION_PROFILE_INVALID" in capsys.readouterr().err

    mutation_commands = (
        ["cutover", "apply", "--plan", "-"],
        ["cutover", "abort", "--plan", str(tmp_path / "plan.json")],
        [
            "cutover",
            "reconcile",
            "--runtime-home",
            str(runtime_home),
            "--epoch",
            "epoch-1",
            "--plan",
            str(tmp_path / "plan.json"),
        ],
        [
            "cutover",
            "restore",
            "--plan",
            str(tmp_path / "plan.json"),
            "--snapshot",
            str(tmp_path / "snapshot.sqlite3"),
        ],
        ["runtime", "initialize", "--runtime-home", str(runtime_home)],
    )
    for command in mutation_commands:
        with pytest.raises(SystemExit) as raised:
            build_parser().parse_args(command)
        assert raised.value.code == 2
        assert "invalid choice" in capsys.readouterr().err

    assert _snapshot(runtime_home) == before


def test_parser_exposes_only_explicit_operator_domains(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "{runtime,activation}" in help_text
    assert "cutover" not in help_text
    with pytest.raises(SystemExit) as raised:
        parser.parse_args(["runtime", "--help"])
    assert raised.value.code == 0
    runtime_help = capsys.readouterr().out
    assert "{install,candidate}" in runtime_help
    assert "initialize" not in runtime_help
