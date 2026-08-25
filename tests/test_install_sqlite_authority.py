from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from aar.provider_ready_install_models import (
    MigrationAttestationDocument,
    MigrationAttestationPayload,
)
from aar.runtime import _install_sqlite as sqlite_module
from aar.runtime import migrations as migrations_module
from aar.runtime import registry as registry_module
from aar.runtime._install_fs import BACKUP_NAME, DATABASE_NAME, FileIdentity, InstallerError
from aar.runtime._install_sqlite import (
    _V5_OBJECTS,
    _V6_OBJECTS,
    SQLiteStageAdapter,
    _normalize_v6_failpoint,
)
from aar.runtime.migrations import (
    V6_STATEMENT_NAMES,
    SimulatedMigrationCrash,
    create_sqlite_backup,
)
from aar.runtime.registry import OperationRegistry


def _digest(char: str) -> str:
    return f"sha256:{char * 64}"


def _migration_sql() -> bytes:
    return (
        Path(__file__).parents[1]
        / "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql"
    ).read_bytes()


def _open_adapter(tmp_path: Path) -> tuple[SQLiteStageAdapter, int, Path]:
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700, parents=True)
    descriptor = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    adapter = SQLiteStageAdapter(
        descriptor,
        FileIdentity.from_stat(os.fstat(descriptor)),
        now_ms=lambda: 1234,
        migration_sql_bytes=_migration_sql(),
    )
    return adapter, descriptor, stage


def _attestation(
    adapter: SQLiteStageAdapter,
    *,
    row_set_digest: str,
) -> MigrationAttestationDocument:
    snapshot = adapter.create_backup()
    migration_digest = "sha256:" + hashlib.sha256(_migration_sql()).hexdigest()
    return MigrationAttestationDocument.issue(
        MigrationAttestationPayload(
            schema_version="aar.migration-v6-attestation-payload.v1",
            migration_version=6,
            cutover_epoch="install-" + "1" * 64,
            snapshot_id="empty-v5-" + row_set_digest.removeprefix("sha256:"),
            snapshot_sha256=snapshot.sha256,
            snapshot_size_bytes=snapshot.size_bytes,
            canonical_v5_row_set_digest=row_set_digest,
            source_commit="0" * 40,
            wheel_digest=_digest("2"),
            profile_digest=_digest("3"),
            skill_digest=_digest("4"),
            contract_manifest_digest=_digest("5"),
            migration_sql_digest=migration_digest,
            external_authority_store_id="authority-" + "6" * 64,
            external_authority_prepared_digest=_digest("7"),
            started_at_unix_ms=1234,
            completed_at_unix_ms=1235,
            foreign_key_violation_count=0,
            integrity_result="ok",
        )
    )


def _prepared_adapter(
    tmp_path: Path,
) -> tuple[SQLiteStageAdapter, int, Path, MigrationAttestationDocument]:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    row_set_digest = adapter.construct_empty_v5()
    return adapter, descriptor, stage, _attestation(adapter, row_set_digest=row_set_digest)


def _database_path(descriptor: int) -> Path:
    return Path(f"/proc/self/fd/{descriptor}/{DATABASE_NAME}")


def test_post_helper_stage_identity_drift_is_fenced_before_acceptance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    original_close = OperationRegistry.close
    original_fstat = sqlite_module.os.fstat
    helper_returned = False

    def close_then_drift(registry: OperationRegistry) -> None:
        nonlocal helper_returned
        original_close(registry)
        helper_returned = True

    def projected_fstat(fd: int) -> os.stat_result:
        observed = original_fstat(fd)
        if fd == descriptor and helper_returned:
            values = list(observed)
            values[1] = observed.st_ino + 1
            return os.stat_result(values)
        return observed

    monkeypatch.setattr(OperationRegistry, "close", close_then_drift)
    monkeypatch.setattr(sqlite_module.os, "fstat", projected_fstat)
    try:
        with pytest.raises(InstallerError) as raised:
            adapter.construct_empty_v5()
        assert raised.value.code == "FRESH_INSTALL_PUBLICATION_UNSUPPORTED"
        assert "retained stage fd is not stable" in str(raised.value)
        assert (stage / DATABASE_NAME).is_file()
        assert not (stage / BACKUP_NAME).exists()
        assert not (stage / f"{DATABASE_NAME}-wal").exists()
        assert not (stage / f"{DATABASE_NAME}-shm").exists()
    finally:
        os.close(descriptor)


def test_descriptor_bound_paths_do_not_follow_stage_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    retained = tmp_path / "retained"
    external = tmp_path / "external"
    relocated = tmp_path / "relocated"
    retained.mkdir()
    external.mkdir()
    descriptor = os.open(retained, os.O_RDONLY | os.O_DIRECTORY)
    original_connect = sqlite3.connect
    switched = False

    def connect_with_stage_swap(
        path: str | Path, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        nonlocal switched
        if not switched:
            assert str(path).removeprefix("file:").startswith(
                f"/proc/self/fd/{descriptor}/"
            )
            retained.rename(relocated)
            external.rename(retained)
            switched = True
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(registry_module.sqlite3, "connect", connect_with_stage_swap)
    try:
        registry = OperationRegistry(_database_path(descriptor), lambda: 1234)
        registry.close()
    finally:
        os.close(descriptor)

    assert switched
    assert (relocated / DATABASE_NAME).is_file()
    assert not (retained / DATABASE_NAME).exists()


@pytest.mark.parametrize("helper", ["backup", "v6"])
def test_descriptor_bound_migration_helpers_do_not_follow_stage_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, helper: str
) -> None:
    retained = tmp_path / "retained"
    external = tmp_path / "external"
    relocated = tmp_path / "relocated"
    retained.mkdir()
    external.mkdir()
    descriptor = os.open(retained, os.O_RDONLY | os.O_DIRECTORY)
    database = _database_path(descriptor)
    registry = OperationRegistry(database, lambda: 1234)
    registry.close()
    if helper == "backup":

        def action() -> object:
            return create_sqlite_backup(
                database, Path(f"/proc/self/fd/{descriptor}/{BACKUP_NAME}")
            )

    else:
        adapter = SQLiteStageAdapter(
            descriptor,
            FileIdentity.from_stat(os.fstat(descriptor)),
            now_ms=lambda: 1234,
            migration_sql_bytes=_migration_sql(),
        )
        row_set_digest = adapter._row_set_digest(DATABASE_NAME)
        attestation = _attestation(adapter, row_set_digest=row_set_digest)

        def action() -> object:
            return adapter.apply_v6(attestation)

    original_connect = sqlite3.connect
    switched = False

    def connect_with_stage_swap(
        path: str | Path, *args: object, **kwargs: object
    ) -> sqlite3.Connection:
        nonlocal switched
        if not switched:
            assert str(path).removeprefix("file:").startswith(
                f"/proc/self/fd/{descriptor}/"
            )
            retained.rename(relocated)
            external.rename(retained)
            switched = True
        return original_connect(path, *args, **kwargs)

    monkeypatch.setattr(migrations_module.sqlite3, "connect", connect_with_stage_swap)
    try:
        action()
    finally:
        os.close(descriptor)

    assert switched
    assert (relocated / DATABASE_NAME).is_file()
    assert not (retained / DATABASE_NAME).exists()
    assert not (retained / BACKUP_NAME).exists()


def test_backup_publish_refuses_foreign_destination_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    try:
        adapter.construct_empty_v5()
        original_link = migrations_module.os.link

        def inject_destination(*args: object, **kwargs: object) -> None:
            (stage / BACKUP_NAME).write_bytes(b"foreign-destination")
            original_link(*args, **kwargs)

        monkeypatch.setattr(migrations_module.os, "link", inject_destination)
        with pytest.raises(InstallerError, match="FRESH_INSTALL_BACKUP_INVALID"):
            adapter.create_backup()
        assert (stage / BACKUP_NAME).read_bytes() == b"foreign-destination"
    finally:
        os.close(descriptor)


def test_backup_publish_refuses_replaced_temp_without_follow_or_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    external = tmp_path / "external.sqlite3"
    external.write_bytes(b"external-unchanged")
    try:
        adapter.construct_empty_v5()
        original_link = migrations_module.os.link
        displaced: Path | None = None

        def replace_temporary(
            source: str,
            destination: str,
            *args: object,
            **kwargs: object,
        ) -> None:
            nonlocal displaced
            displaced = stage / f"displaced-{source}"
            (stage / source).rename(displaced)
            (stage / source).symlink_to(external)
            original_link(source, destination, *args, **kwargs)

        monkeypatch.setattr(migrations_module.os, "link", replace_temporary)
        with pytest.raises(InstallerError, match="FRESH_INSTALL_BACKUP_INVALID"):
            adapter.create_backup()
        assert external.read_bytes() == b"external-unchanged"
        assert displaced is not None and displaced.is_file()
        assert (stage / BACKUP_NAME).is_symlink()
    finally:
        os.close(descriptor)


def test_v5_inventory_and_backup_snapshot_are_independently_verified(tmp_path: Path) -> None:
    adapter, descriptor, _stage = _open_adapter(tmp_path)
    try:
        adapter.construct_empty_v5()
        connection = sqlite3.connect(_database_path(descriptor))
        try:
            connection.execute("CREATE TABLE unexpected_v5_object (value INTEGER)")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(InstallerError, match="empty v5 object inventory differs"):
            adapter._verify_empty_v5(DATABASE_NAME)
    finally:
        os.close(descriptor)

    adapter, descriptor, _stage = _open_adapter(tmp_path / "backup")
    try:
        adapter.construct_empty_v5()

        def lying_backup(source: Path, destination: Path):
            snapshot = create_sqlite_backup(source, destination)
            return replace(snapshot, size_bytes=snapshot.size_bytes + 1)

        with pytest.raises(InstallerError, match="snapshot does not match retained bytes"):
            adapter.create_backup(backup_factory=lying_backup)
    finally:
        os.close(descriptor)


def test_v5_transaction_fault_rolls_back_schema_and_domain_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    original_apply_v3 = registry_module.OperationRegistry._apply_v3_schema

    def fail_inside_transaction(registry: registry_module.OperationRegistry) -> None:
        original_apply_v3(registry)
        raise RuntimeError("injected v5 transaction fault")

    monkeypatch.setattr(
        registry_module.OperationRegistry,
        "_apply_v3_schema",
        fail_inside_transaction,
    )
    try:
        with pytest.raises(InstallerError) as raised:
            adapter.construct_empty_v5()
        assert raised.value.code == "FRESH_INSTALL_V5_INVALID"
        connection = sqlite3.connect(_database_path(descriptor))
        try:
            tables = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
                )
            )
            assert tables == ()
        finally:
            connection.close()
        assert not (stage / f"{DATABASE_NAME}-wal").exists()
        assert not (stage / f"{DATABASE_NAME}-shm").exists()
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("variant", "entry_name", "expected_error"),
    [
        ("database", DATABASE_NAME, None),
        ("backup", BACKUP_NAME, None),
        ("database-wal", f"{DATABASE_NAME}-wal", None),
        ("database-shm", f"{DATABASE_NAME}-shm", None),
        ("backup-wal", f"{BACKUP_NAME}-wal", None),
        ("backup-shm", f"{BACKUP_NAME}-shm", None),
        ("symlink", f"{DATABASE_NAME}-wal", "FRESH_INSTALL_SQLITE_RESIDUE"),
        ("wrong-type", f"{DATABASE_NAME}-shm", "FRESH_INSTALL_SQLITE_RESIDUE"),
        ("unknown-entry", "unknown.sqlite3", "FRESH_INSTALL_SQLITE_RESIDUE"),
    ],
)
def test_sqlite_stage_inventory_enforces_complete_nofollow_allowlist(
    tmp_path: Path,
    variant: str,
    entry_name: str,
    expected_error: str | None,
) -> None:
    adapter, descriptor, stage = _open_adapter(tmp_path)
    try:
        if variant == "symlink":
            foreign = tmp_path / "foreign.sqlite3"
            foreign.write_bytes(b"foreign")
            (stage / entry_name).symlink_to(foreign)
        elif variant == "wrong-type":
            (stage / entry_name).mkdir()
        else:
            (stage / entry_name).write_bytes(b"entry")
        if expected_error is None:
            assert set(adapter._check_entries(backup=True)) == {entry_name}
        else:
            with pytest.raises(InstallerError) as raised:
                adapter._check_entries(backup=True)
            assert raised.value.code == expected_error
    finally:
        os.close(descriptor)


def test_v6_inventory_and_all_attestation_fields_are_exact(tmp_path: Path) -> None:
    assert len(_V5_OBJECTS) == 28
    assert len(_V6_OBJECTS) == 25
    adapter, descriptor, _stage, attestation = _prepared_adapter(tmp_path)
    try:
        adapter.apply_v6(attestation)
        connection = sqlite3.connect(_database_path(descriptor))
        try:
            connection.execute("CREATE TABLE unexpected_v6_object (value INTEGER)")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(InstallerError, match="v6 object inventory differs"):
            adapter.verify_v6(attestation)
    finally:
        os.close(descriptor)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source_commit", "f" * 40),
        ("wheel_digest", _digest("f")),
        ("skill_digest", _digest("e")),
        ("contract_manifest_digest", _digest("d")),
    ],
)
def test_v6_readback_rejects_each_previously_omitted_attestation_field(
    tmp_path: Path, field: str, replacement: str
) -> None:
    adapter, descriptor, _stage, attestation = _prepared_adapter(tmp_path)
    try:
        adapter.apply_v6(attestation)
        connection = sqlite3.connect(_database_path(descriptor))
        try:
            connection.execute(
                f"UPDATE migration_v6_attestations SET {field} = ? WHERE migration_version = 6",
                (replacement,),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(InstallerError, match=f"field mismatch: {field}"):
            adapter.verify_v6(attestation)
    finally:
        os.close(descriptor)


_V6_BOUNDARIES = (
    *(
        f"fail_after_statement_{index:02d}_{name}"
        for index, name in enumerate(V6_STATEMENT_NAMES, start=1)
    ),
    "before-first-statement",
    "before-attestation-insert",
    "before-schema-migration-insert",
    "before-commit",
    "after-commit-readback",
)


@pytest.mark.parametrize("boundary", _V6_BOUNDARIES, ids=_V6_BOUNDARIES)
def test_each_named_v6_boundary_is_distinct_and_contained(
    tmp_path: Path, boundary: str
) -> None:
    assert len(_V6_BOUNDARIES) == 30
    assert _normalize_v6_failpoint(boundary) == boundary
    adapter, descriptor, _stage, attestation = _prepared_adapter(tmp_path)
    target = tmp_path / "target-must-remain-absent"
    try:
        with pytest.raises(InstallerError, match="v6 transaction did not commit") as raised:
            adapter.apply_v6(attestation, fail_after_statement=boundary)
        cause = raised.value.__cause__
        assert isinstance(cause, SimulatedMigrationCrash)
        if boundary.startswith("fail_after_statement_"):
            expected_fragment = f"after v6 DDL statement {int(boundary.split('_')[3])}"
        else:
            expected_fragment = {
                "before-first-statement": "before first v6 DDL",
                "before-attestation-insert": "before v6 attestation insert",
                "before-schema-migration-insert": "before schema migration insert",
                "before-commit": "before v6 commit",
                "after-commit-readback": "after v6 commit readback",
            }[boundary]
        assert expected_fragment in str(cause)
        connection = sqlite3.connect(_database_path(descriptor))
        try:
            versions = tuple(
                row[0]
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            if boundary == "after-commit-readback":
                assert versions == (1, 2, 3, 4, 5, 6)
                assert "after v6 commit readback" in str(cause)
            else:
                assert versions == (1, 2, 3, 4, 5)
                assert connection.execute(
                    "SELECT 1 FROM sqlite_schema WHERE name = 'migration_v6_attestations'"
                ).fetchone() is None
        finally:
            connection.close()
        assert not target.exists()
    finally:
        os.close(descriptor)
