from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from aar.admin import build_parser, main
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_models import ActivationRuntimeBinding, HostActivationIntent
from aar.provider_ready_operator_models import CutoverPlan
from aar.provider_ready_runtime_models import ActivationReadback
from aar.runtime.operator import (
    RuntimeHomePaths,
    _row_set_digest,
    _sqlite_read_only,
    _table_names,
)
from aar.runtime.ownership import RuntimeOwnershipLock
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


def _bound_intent(runtime_home: Path, tmp_path: Path) -> Path:
    source = HostActivationIntent.model_validate_json(
        (VALID / "activation-intent.json").read_bytes(), strict=True
    )
    paths = RuntimeHomePaths.resolve(runtime_home)
    stat_result = paths.database.stat()
    runtime = ActivationRuntimeBinding(
        runtime_home_digest=canonical_sha256(
            {"database": str(paths.database), "runtime_home": str(paths.runtime_home)}
        ),
        database_identity=f"dev-{stat_result.st_dev}-ino-{stat_result.st_ino}",
        required_registry_version=6,
        programmable_backend=source.runtime.programmable_backend,
        security_profile=source.runtime.security_profile,
    )
    bound = HostActivationIntent.issue(
        schema_version=source.schema_version,
        profile_id=source.profile_id,
        activation_generation=source.activation_generation,
        previous_activation_authority_digest=source.previous_activation_authority_digest,
        candidate=source.candidate,
        runtime=runtime,
        planner=source.planner,
        adapters=source.adapters,
        routes=source.routes,
        grant_policy=source.grant_policy,
        cutover_authority_store_id=source.cutover_authority_store_id,
        recovery_compatibility_digest=source.recovery_compatibility_digest,
    )
    intent_path = tmp_path / "intent.json"
    intent_path.write_bytes(canonical_json_bytes(bound))
    return intent_path


def test_cutover_plan_emits_one_canonical_document_and_writes_nothing(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    intent = _bound_intent(runtime_home, tmp_path)
    profile_output = tmp_path / "operator-output" / "final-profile.json"
    before = _snapshot(runtime_home)

    assert (
        main(
            [
                "cutover",
                "plan",
                "--runtime-home",
                str(runtime_home),
                "--intent",
                str(intent),
                "--profile-output",
                str(profile_output),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
    raw = captured.out.encode("utf-8")
    assert raw.endswith(b"\n") and raw.count(b"\n") == 1
    plan = CutoverPlan.model_validate_json(raw, strict=True)
    assert raw == canonical_json_bytes(plan) + b"\n"
    assert plan.database_identity == plan.database_file.artifact_id
    assert plan.activation_intent_digest == HostActivationIntent.model_validate_json(
        intent.read_bytes(), strict=True
    ).intent_digest
    assert plan.final_profile_output == str(profile_output.resolve())
    assert _snapshot(runtime_home) == before
    assert not profile_output.exists()
    assert not Path(plan.snapshot.destination).exists()
    assert not (runtime_home / "authority").exists()


def test_plan_rejects_output_option_before_any_operator_write(tmp_path: Path) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    intent = _bound_intent(runtime_home, tmp_path)
    before = _snapshot(runtime_home)

    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "cutover",
                "plan",
                "--runtime-home",
                str(runtime_home),
                "--intent",
                str(intent),
                "--profile-output",
                str(tmp_path / "profile.json"),
                "--output",
                str(tmp_path / "plan.json"),
            ]
        )

    assert _snapshot(runtime_home) == before
    assert not (tmp_path / "plan.json").exists()


def test_plan_rejects_live_predecessor_owner_without_operator_write(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    intent = _bound_intent(runtime_home, tmp_path)
    owner = RuntimeOwnershipLock(runtime_home / "reference.sqlite3")
    before = _snapshot(runtime_home)
    try:
        assert (
            main(
                [
                    "cutover",
                    "plan",
                    "--runtime-home",
                    str(runtime_home),
                    "--intent",
                    str(intent),
                    "--profile-output",
                    str(tmp_path / "profile.json"),
                ]
            )
            == 2
        )
        assert "CUTOVER_OWNER_ACTIVE" in capsys.readouterr().err
        assert _snapshot(runtime_home) == before
    finally:
        owner.close()


def test_cutover_plan_reads_committed_wal_without_checkpointing_or_rewriting_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    database = runtime_home / "reference.sqlite3"
    keeper = sqlite3.connect(database, isolation_level=None)
    writer = sqlite3.connect(database, isolation_level=None)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        keeper.execute("BEGIN")
        keeper.execute("SELECT generation FROM runtime_meta").fetchone()
        writer.execute("UPDATE runtime_meta SET generation=7 WHERE singleton=1")
        writer.commit()
        writer.close()
        intent = _bound_intent(runtime_home, tmp_path)
        before = _snapshot(runtime_home)

        assert (
            main(
                [
                    "cutover",
                    "plan",
                    "--runtime-home",
                    str(runtime_home),
                    "--intent",
                    str(intent),
                    "--profile-output",
                    str(tmp_path / "final-profile.json"),
                ]
            )
            == 0
        )
        plan = CutoverPlan.model_validate_json(capsys.readouterr().out, strict=True)
        verifier = _sqlite_read_only(database)
        try:
            assert verifier.execute(
                "SELECT generation FROM runtime_meta WHERE singleton=1"
            ).fetchone()[0] == 7
            assert plan.canonical_v5_row_set_digest == _row_set_digest(
                verifier, _table_names(verifier)
            )
        finally:
            verifier.close()
        after = _snapshot(runtime_home)
        assert set(after) == set(before)
        for stable_path in ("reference.sqlite3", "reference.sqlite3-wal"):
            assert after[stable_path] == before[stable_path]
        assert after["reference.sqlite3-shm"][0] == before["reference.sqlite3-shm"][0]
    finally:
        writer.close()
        keeper.close()


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


@pytest.mark.parametrize(
    "argv",
    [
        ("cutover", "status"),
        ("activation", "status"),
    ],
)
def test_status_commands_are_strict_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    argv: tuple[str, str],
) -> None:
    runtime_home = _v5_runtime_home(tmp_path)
    before = _snapshot(runtime_home)

    assert main([*argv, "--runtime-home", str(runtime_home)]) == 0

    captured = capsys.readouterr()
    readback = ActivationReadback.model_validate_json(captured.out, strict=True)
    assert readback.state == "migration_required"
    assert readback.reason_code == "REGISTRY_VERSION_UNSUPPORTED"
    assert captured.out.encode() == canonical_json_bytes(readback) + b"\n"
    assert captured.err == ""
    assert _snapshot(runtime_home) == before


def test_activation_verify_validates_supplied_bytes_without_mutation(
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
        == 0
    )

    captured = capsys.readouterr()
    readback = ActivationReadback.model_validate_json(captured.out, strict=True)
    assert readback.state == "migration_required"
    assert captured.err == ""
    assert _snapshot(runtime_home) == before


def test_invalid_profile_and_all_mutation_boundaries_fail_before_write(
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
        assert main(command) == 2
        assert "COMMAND_NOT_IMPLEMENTED" in capsys.readouterr().err

    assert _snapshot(runtime_home) == before


def test_parser_exposes_only_explicit_operator_domains() -> None:
    help_text = build_parser().format_help()
    assert "{cutover,runtime,activation}" in help_text
