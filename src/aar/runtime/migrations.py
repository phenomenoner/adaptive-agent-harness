"""Offline, identity-bound SQLite v5-to-v6 migration primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import validate_contract_document


class MigrationError(RuntimeError):
    """Base class for a migration that cannot safely commit."""


class MigrationIdentityMismatch(MigrationError):
    """The supplied or existing migration identity is inconsistent."""


class MigrationIntegrityError(MigrationError):
    """SQLite integrity evidence failed before publication."""


class SimulatedMigrationCrash(MigrationError):
    """Deterministic fault injection raised inside the migration transaction."""


@dataclass(frozen=True, slots=True)
class SQLiteBackupSnapshot:
    path: Path
    sha256: str
    size_bytes: int
    integrity_result: str
    foreign_key_violation_count: int


@dataclass(frozen=True, slots=True)
class RegistryV6MigrationResult:
    replayed: bool
    attestation_digest: str
    migration_sql_digest: str


def create_sqlite_backup(
    database_path: Path,
    snapshot_path: Path,
) -> SQLiteBackupSnapshot:
    """Create and verify an atomic SQLite backup, including committed WAL pages."""

    source_path = database_path.resolve()
    destination = snapshot_path.resolve()
    if source_path == destination:
        raise ValueError("snapshot path must differ from the source database")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp-{uuid.uuid4().hex}")

    source = sqlite3.connect(source_path)
    target = sqlite3.connect(temporary)
    try:
        source.execute("PRAGMA busy_timeout=5000")
        target.execute("PRAGMA synchronous=FULL")
        source.backup(target)
        target.commit()
        integrity_result = str(target.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violation_count = len(target.execute("PRAGMA foreign_key_check").fetchall())
        if integrity_result != "ok" or foreign_key_violation_count != 0:
            raise MigrationIntegrityError(
                "snapshot failed SQLite integrity or foreign-key verification"
            )
    except BaseException:
        target.close()
        source.close()
        temporary.unlink(missing_ok=True)
        raise
    else:
        target.close()
        source.close()

    _fsync_file(temporary)
    os.replace(temporary, destination)
    _fsync_directory(destination.parent)
    content = destination.read_bytes()
    return SQLiteBackupSnapshot(
        path=destination,
        sha256=_digest_bytes(content),
        size_bytes=len(content),
        integrity_result=integrity_result,
        foreign_key_violation_count=foreign_key_violation_count,
    )


def apply_registry_v6(
    database_path: Path,
    *,
    migration_sql_bytes: bytes,
    attestation: dict[str, Any],
    fail_after_statement: int | None = None,
) -> RegistryV6MigrationResult:
    """Apply reviewed v6 DDL and identity rows in one rollback-safe transaction."""

    if fail_after_statement is not None and fail_after_statement < 0:
        raise ValueError("fail_after_statement must be non-negative")
    sql_digest = _digest_bytes(migration_sql_bytes)
    document = json.loads(json.dumps(attestation))
    validate_contract_document(
        "aar-migration-cutover-v1.schema.json",
        "MigrationAttestationDocument",
        document,
    )
    payload = document["attestation"]
    attestation_digest = document["attestation_digest"]
    if payload["migration_version"] != 6:
        raise MigrationIdentityMismatch("migration version must be 6")
    if payload["migration_sql_digest"] != sql_digest:
        raise MigrationIdentityMismatch("migration SQL digest mismatch")
    if attestation_digest != canonical_sha256(payload):
        raise MigrationIdentityMismatch("migration attestation digest mismatch")

    try:
        statements = _migration_statements(migration_sql_bytes.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise MigrationIdentityMismatch("migration SQL must be UTF-8") from error

    connection = sqlite3.connect(
        database_path.resolve(),
        isolation_level=None,
        timeout=5,
    )
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        existing = _existing_v6_identity(connection)
        if existing is not None:
            existing_sql_digest, existing_attestation_digest = existing
            if existing_sql_digest != sql_digest:
                raise MigrationIdentityMismatch("existing migration SQL digest mismatch")
            if existing_attestation_digest != attestation_digest:
                raise MigrationIdentityMismatch("existing migration attestation digest mismatch")
            return RegistryV6MigrationResult(
                replayed=True,
                attestation_digest=attestation_digest,
                migration_sql_digest=sql_digest,
            )

        connection.execute("BEGIN IMMEDIATE")
        try:
            if fail_after_statement == 0:
                raise SimulatedMigrationCrash("simulated crash before first v6 DDL")
            for index, statement in enumerate(statements, start=1):
                connection.execute(statement)
                if fail_after_statement == index:
                    raise SimulatedMigrationCrash(f"simulated crash after v6 DDL statement {index}")

            integrity_result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_key_violation_count = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
            if integrity_result != "ok" or foreign_key_violation_count != 0:
                raise MigrationIntegrityError(
                    "candidate failed SQLite integrity or foreign-key verification"
                )
            if (
                payload["integrity_result"] != integrity_result
                or payload["foreign_key_violation_count"] != foreign_key_violation_count
            ):
                raise MigrationIdentityMismatch(
                    "attestation integrity evidence does not match candidate database"
                )

            connection.execute(
                """
                INSERT INTO migration_v6_attestations(
                    migration_version, attestation_digest, cutover_epoch,
                    snapshot_id, snapshot_sha256, snapshot_size_bytes,
                    canonical_v5_row_set_digest, source_commit, wheel_digest,
                    profile_digest, skill_digest, contract_manifest_digest,
                    migration_sql_digest, external_authority_store_id,
                    external_authority_prepared_digest, started_at_unix_ms,
                    completed_at_unix_ms, foreign_key_violation_count,
                    integrity_result
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["migration_version"],
                    attestation_digest,
                    payload["cutover_epoch"],
                    payload["snapshot_id"],
                    payload["snapshot_sha256"],
                    payload["snapshot_size_bytes"],
                    payload["canonical_v5_row_set_digest"],
                    payload["source_commit"],
                    payload["wheel_digest"],
                    payload["profile_digest"],
                    payload["skill_digest"],
                    payload["contract_manifest_digest"],
                    payload["migration_sql_digest"],
                    payload["external_authority_store_id"],
                    payload["external_authority_prepared_digest"],
                    payload["started_at_unix_ms"],
                    payload["completed_at_unix_ms"],
                    payload["foreign_key_violation_count"],
                    payload["integrity_result"],
                ),
            )
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at_unix_ms, migration_digest)
                VALUES (6, ?, ?)
                """,
                (payload["completed_at_unix_ms"], sql_digest),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    finally:
        connection.close()

    return RegistryV6MigrationResult(
        replayed=False,
        attestation_digest=attestation_digest,
        migration_sql_digest=sql_digest,
    )


def _existing_v6_identity(connection: sqlite3.Connection) -> tuple[str, str] | None:
    schema_row = connection.execute(
        "SELECT migration_digest FROM schema_migrations WHERE version=6"
    ).fetchone()
    table_row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='migration_v6_attestations'"
    ).fetchone()
    if table_row is None:
        if schema_row is not None:
            raise MigrationIdentityMismatch("v6 schema row exists without attestation table")
        return None
    attestation_row = connection.execute(
        "SELECT attestation_digest, migration_sql_digest "
        "FROM migration_v6_attestations WHERE migration_version=6"
    ).fetchone()
    if schema_row is None and attestation_row is None:
        return None
    if schema_row is None or attestation_row is None:
        raise MigrationIdentityMismatch("v6 schema and attestation identities are incomplete")
    if str(schema_row[0]) != str(attestation_row[1]):
        raise MigrationIdentityMismatch("v6 schema and attestation SQL digests differ")
    return str(schema_row[0]), str(attestation_row[0])


def _migration_statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    buffer = ""
    for line in sql.splitlines(keepends=True):
        buffer += line
        if not sqlite3.complete_statement(buffer):
            continue
        statement = buffer.strip()
        buffer = ""
        if not statement:
            continue
        normalized = " ".join(statement.split()).upper()
        if normalized in {"PRAGMA FOREIGN_KEYS = ON;", "PRAGMA FOREIGN_KEYS=ON;"}:
            continue
        if not (
            normalized.startswith("CREATE TABLE IF NOT EXISTS ")
            or normalized.startswith("CREATE INDEX IF NOT EXISTS ")
            or normalized.startswith(
                "CREATE TRIGGER IF NOT EXISTS CALLER_WORK_COMMAND_RECEIPTS_NO_"
            )
        ):
            raise MigrationIdentityMismatch(
                "migration SQL contains a statement outside the reviewed DDL allowlist"
            )
        statements.append(statement)
    if buffer.strip():
        raise MigrationIdentityMismatch("migration SQL ends with an incomplete statement")
    if not statements:
        raise MigrationIdentityMismatch("migration SQL contains no v6 DDL")
    return tuple(statements)


def _digest_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
