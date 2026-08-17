from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aar.runtime.reference_host import ReferenceHost

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
BASELINE_SQL = SDD / "fixtures" / "registry-v5.sql"
MIGRATION_SQL = SDD / "migration-v6.sql"
FIXED_NOW_MS = 2_000_000_000_000


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: object) -> str:
    return digest_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def create_v5_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(BASELINE_SQL.read_text(encoding="utf-8"))
    finally:
        connection.close()


def table_exists(path: Path, table: str) -> bool:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    finally:
        connection.close()


def attestation_document(
    snapshot: Any,
    migration_bytes: bytes,
    *,
    migration_sql_digest: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "aar.migration-v6-attestation-payload.v1",
        "migration_version": 6,
        "cutover_epoch": "cutover-test",
        "snapshot_id": "snapshot-test",
        "snapshot_sha256": snapshot.sha256,
        "snapshot_size_bytes": snapshot.size_bytes,
        "canonical_v5_row_set_digest": "sha256:" + "2" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "profile_digest": "sha256:" + "4" * 64,
        "skill_digest": "sha256:" + "5" * 64,
        "contract_manifest_digest": "sha256:" + "6" * 64,
        "migration_sql_digest": migration_sql_digest or digest_bytes(migration_bytes),
        "external_authority_store_id": "cutover-authority-test",
        "external_authority_prepared_digest": "sha256:" + "8" * 64,
        "started_at_unix_ms": FIXED_NOW_MS,
        "completed_at_unix_ms": FIXED_NOW_MS + 1,
        "foreign_key_violation_count": 0,
        "integrity_result": "ok",
    }
    return {
        "attestation": payload,
        "attestation_digest": canonical_digest(payload),
    }


def test_sqlite_backup_includes_committed_wal_pages(tmp_path: Path) -> None:
    from aar.runtime.migrations import create_sqlite_backup

    database = tmp_path / "registry.sqlite"
    snapshot_path = tmp_path / "registry-v5.snapshot.sqlite"
    create_v5_database(database)

    writer = sqlite3.connect(database)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO asset_events(event_digest, event_kind, episode_digest, event_json) "
            "VALUES(?, ?, ?, ?)",
            (
                "sha256:" + "8" * 64,
                "test.wal-committed",
                None,
                '{"kind":"test.wal-committed"}',
            ),
        )
        writer.commit()
        wal_path = Path(f"{database}-wal")
        assert wal_path.is_file() and wal_path.stat().st_size > 0

        snapshot = create_sqlite_backup(database, snapshot_path)
    finally:
        writer.close()

    assert snapshot.path == snapshot_path.resolve()
    assert snapshot.sha256 == digest_bytes(snapshot_path.read_bytes())
    assert snapshot.size_bytes == snapshot_path.stat().st_size
    assert snapshot.integrity_result == "ok"
    assert snapshot.foreign_key_violation_count == 0

    reader = sqlite3.connect(snapshot_path)
    try:
        assert reader.execute(
            "SELECT event_kind FROM asset_events WHERE event_digest=?",
            ("sha256:" + "8" * 64,),
        ).fetchone() == ("test.wal-committed",)
    finally:
        reader.close()


def test_v6_migration_crash_rolls_back_every_ddl_and_v5_reopens(
    tmp_path: Path,
) -> None:
    from aar.runtime.migrations import (
        SimulatedMigrationCrash,
        apply_registry_v6,
        create_sqlite_backup,
    )

    database = tmp_path / "registry.sqlite"
    snapshot_path = tmp_path / "snapshot.sqlite"
    create_v5_database(database)
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = MIGRATION_SQL.read_bytes()

    with pytest.raises(SimulatedMigrationCrash):
        apply_registry_v6(
            database,
            migration_sql_bytes=migration_bytes,
            attestation=attestation_document(snapshot, migration_bytes),
            fail_after_statement=5,
        )

    assert not table_exists(database, "migration_v6_attestations")
    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=6"
        ).fetchone() == (0,)
    finally:
        connection.close()

    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS + 10,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    host.close()


def test_v6_migration_replay_is_idempotent_for_same_identity(tmp_path: Path) -> None:
    from aar.runtime.migrations import apply_registry_v6, create_sqlite_backup

    database = tmp_path / "registry.sqlite"
    snapshot_path = tmp_path / "snapshot.sqlite"
    create_v5_database(database)
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = MIGRATION_SQL.read_bytes()
    attestation = attestation_document(snapshot, migration_bytes)

    first = apply_registry_v6(
        database,
        migration_sql_bytes=migration_bytes,
        attestation=attestation,
    )
    replay = apply_registry_v6(
        database,
        migration_sql_bytes=migration_bytes,
        attestation=attestation,
    )

    assert first.replayed is False
    assert replay.replayed is True
    assert replay.attestation_digest == first.attestation_digest
    assert replay.migration_sql_digest == first.migration_sql_digest

    connection = sqlite3.connect(database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version=6"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT COUNT(*) FROM migration_v6_attestations "
            "WHERE migration_version=6"
        ).fetchone() == (1,)
    finally:
        connection.close()


def test_v6_migration_rejects_wrong_sql_digest_before_first_ddl(
    tmp_path: Path,
) -> None:
    from aar.runtime.migrations import (
        MigrationIdentityMismatch,
        apply_registry_v6,
        create_sqlite_backup,
    )

    database = tmp_path / "registry.sqlite"
    snapshot_path = tmp_path / "snapshot.sqlite"
    create_v5_database(database)
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = MIGRATION_SQL.read_bytes()
    wrong_attestation = attestation_document(
        snapshot,
        migration_bytes,
        migration_sql_digest="sha256:" + "0" * 64,
    )

    with pytest.raises(MigrationIdentityMismatch, match="migration SQL digest"):
        apply_registry_v6(
            database,
            migration_sql_bytes=migration_bytes,
            attestation=wrong_attestation,
        )

    assert not table_exists(database, "migration_v6_attestations")
