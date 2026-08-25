"""Offline, identity-bound SQLite v5-to-v6 migration primitives."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import validate_contract_document
from aar.runtime.registry import sqlite_connection_path


class MigrationError(RuntimeError):
    """Base class for a migration that cannot safely commit."""


class MigrationIdentityMismatch(MigrationError):
    """The supplied or existing migration identity is inconsistent."""


class MigrationIntegrityError(MigrationError):
    """SQLite integrity evidence failed before publication."""


class SimulatedMigrationCrash(MigrationError):
    """Deterministic fault injection raised inside the migration transaction."""


V6_STATEMENT_NAMES = (
    "migration_v6_attestations",
    "rlm_workbench_jobs",
    "rlm_workbench_cells",
    "rlm_workbench_suspensions",
    "caller_work_tickets",
    "caller_work_candidate_receipts",
    "caller_work_command_receipts",
    "caller_work_command_receipts_no_update",
    "caller_work_command_receipts_no_delete",
    "rlm_workbench_successor_outbox",
    "rlm_workbench_attempt_authority",
    "rlm_workbench_rebind_transfers",
    "rlm_workbench_artifact_stages",
    "rlm_workbench_cell_manifests",
    "rlm_workbench_finalization_manifests",
    "broker_contract_catalog_v2",
    "broker_backend_availability_v2",
    "idx_workbench_phase_deadline",
    "idx_workbench_cells_state",
    "idx_caller_work_state_deadline",
    "idx_caller_work_claim_expiry",
    "idx_candidate_receipts_ticket",
    "idx_caller_command_receipts_ticket",
    "idx_artifact_stages_state",
    "idx_successor_outbox_state",
)

_V6_PRECOMMIT_BOUNDARIES = frozenset(
    {
        "before-first-statement",
        "before-attestation-insert",
        "before-schema-migration-insert",
        "before-commit",
    }
)


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
    """Create an exact backup and publish it no-replace under one retained parent."""

    source_path = Path(sqlite_connection_path(database_path))
    destination = Path(sqlite_connection_path(snapshot_path))
    if source_path == destination:
        raise ValueError("snapshot path must differ from the source database")
    if not source_path.is_file():
        raise FileNotFoundError(source_path)

    parent_parts = destination.parent.parts
    try:
        if (
            len(parent_parts) == 5
            and parent_parts[:4] == ("/", "proc", "self", "fd")
            and parent_parts[4].isdigit()
        ):
            parent_fd = os.dup(int(parent_parts[4]))
            if not stat.S_ISDIR(os.fstat(parent_fd).st_mode):
                raise NotADirectoryError(destination.parent)
        else:
            parent_fd = os.open(
                destination.parent,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
    except OSError as error:
        raise MigrationError("snapshot parent is not a retained directory") from error
    temporary_name = f".{destination.name}.tmp-{uuid.uuid4().hex}"
    temporary_fd = -1
    published_identity: tuple[int, int] | None = None
    temporary_identity: tuple[int, int] | None = None
    try:
        if os.path.lexists(destination):
            raise FileExistsError(destination)
        source = sqlite3.connect(
            f"file:{source_path}?mode=ro",
            uri=True,
            isolation_level=None,
        )
        target = sqlite3.connect(":memory:", isolation_level=None)
        try:
            source.execute("PRAGMA busy_timeout=5000")
            source.backup(target)
            integrity_result = str(target.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_key_violation_count = len(
                target.execute("PRAGMA foreign_key_check").fetchall()
            )
            if integrity_result != "ok" or foreign_key_violation_count != 0:
                raise MigrationIntegrityError(
                    "snapshot failed SQLite integrity or foreign-key verification"
                )
            content = target.serialize()
        finally:
            target.close()
            source.close()

        temporary_fd = os.open(
            temporary_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
            0o600,
            dir_fd=parent_fd,
        )
        view = memoryview(content)
        while view:
            written = os.write(temporary_fd, view)
            view = view[written:]
        os.fchmod(temporary_fd, 0o600)
        os.fsync(temporary_fd)
        temporary_stat = os.fstat(temporary_fd)
        temporary_identity = (int(temporary_stat.st_dev), int(temporary_stat.st_ino))
        named_temporary = os.stat(
            temporary_name, dir_fd=parent_fd, follow_symlinks=False
        )
        if temporary_identity != (
            int(named_temporary.st_dev),
            int(named_temporary.st_ino),
        ):
            raise MigrationError("snapshot temporary identity changed before publication")

        os.link(
            temporary_name,
            destination.name,
            src_dir_fd=parent_fd,
            dst_dir_fd=parent_fd,
            follow_symlinks=False,
        )
        published = os.stat(destination.name, dir_fd=parent_fd, follow_symlinks=False)
        published_identity = (int(published.st_dev), int(published.st_ino))
        if published_identity != temporary_identity:
            raise MigrationError("snapshot destination does not name the retained temporary file")
        named_temporary = os.stat(
            temporary_name, dir_fd=parent_fd, follow_symlinks=False
        )
        if temporary_identity != (
            int(named_temporary.st_dev),
            int(named_temporary.st_ino),
        ):
            raise MigrationError("snapshot temporary identity changed during publication")
        os.unlink(temporary_name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return SQLiteBackupSnapshot(
            path=destination,
            sha256=_digest_bytes(content),
            size_bytes=len(content),
            integrity_result=integrity_result,
            foreign_key_violation_count=foreign_key_violation_count,
        )
    except BaseException:
        if published_identity is not None and published_identity == temporary_identity:
            try:
                observed = os.stat(
                    destination.name, dir_fd=parent_fd, follow_symlinks=False
                )
                if published_identity == (int(observed.st_dev), int(observed.st_ino)):
                    os.unlink(destination.name, dir_fd=parent_fd)
            except OSError:
                pass
        if temporary_identity is not None:
            try:
                observed = os.stat(
                    temporary_name, dir_fd=parent_fd, follow_symlinks=False
                )
                if temporary_identity == (int(observed.st_dev), int(observed.st_ino)):
                    os.unlink(temporary_name, dir_fd=parent_fd)
            except OSError:
                pass
        raise
    finally:
        if temporary_fd >= 0:
            os.close(temporary_fd)
        os.close(parent_fd)


def apply_registry_v6(
    database_path: Path,
    *,
    migration_sql_bytes: bytes,
    attestation: dict[str, Any],
    fail_after_statement: int | str | None = None,
) -> RegistryV6MigrationResult:
    """Apply reviewed v6 DDL and identity rows in one rollback-safe transaction."""

    boundary = _normalize_v6_boundary(fail_after_statement)
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
        sqlite_connection_path(database_path),
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
            if boundary == 0 or boundary == "before-first-statement":
                raise SimulatedMigrationCrash("simulated crash before first v6 DDL")
            for index, statement in enumerate(statements, start=1):
                connection.execute(statement)
                if boundary == index:
                    raise SimulatedMigrationCrash(f"simulated crash after v6 DDL statement {index}")

            if boundary == "before-attestation-insert":
                raise SimulatedMigrationCrash("simulated crash before v6 attestation insert")

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
            if boundary == "before-schema-migration-insert":
                raise SimulatedMigrationCrash("simulated crash before schema migration insert")
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at_unix_ms, migration_digest)
                VALUES (6, ?, ?)
                """,
                (payload["completed_at_unix_ms"], sql_digest),
            )
            if boundary == "before-commit":
                raise SimulatedMigrationCrash("simulated crash before v6 commit")
            connection.commit()
            if boundary == "after-commit-readback":
                _existing_v6_identity(connection)
                raise SimulatedMigrationCrash("simulated crash after v6 commit readback")
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


def _normalize_v6_boundary(value: int | str | None) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if 0 <= value <= len(V6_STATEMENT_NAMES):
            return value
        raise ValueError("fail_after_statement is outside the v6 DDL boundary range")
    if isinstance(value, str):
        if value in _V6_PRECOMMIT_BOUNDARIES or value == "after-commit-readback":
            return value
        for index, name in enumerate(V6_STATEMENT_NAMES, start=1):
            if value == f"fail_after_statement_{index:02d}_{name}":
                return index
    raise ValueError(f"unknown v6 failpoint: {value!r}")


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
