"""Fenced SQLite construction, backup, migration, and quiesce lifecycle."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_install_models import MigrationAttestationDocument
from aar.runtime._install_artifacts import frozen_migration_v6_bytes
from aar.runtime._install_fs import (
    BACKUP_NAME,
    DATABASE_NAME,
    FileIdentity,
    InstallerError,
    RetainedDirectory,
    StageHandle,
    _assert_identity_unchanged,
    _assert_regular_nofollow,
    _entry_stat,
    _fixed_child_path,
    _fsync_fd,
    _stage_entries,
    sys_platform_linux,
)
from aar.runtime.migrations import (
    V6_STATEMENT_NAMES,
    MigrationError,
    SQLiteBackupSnapshot,
    apply_registry_v6,
    create_sqlite_backup,
)
from aar.runtime.registry import OperationRegistry

_V5_OBJECTS = frozenset(
    {
        ("index", "operation_attempts_state_idx"),
        ("index", "operation_dispatch_queue_idx"),
        ("index", "operation_events_operation_idx"),
        ("index", "operation_recovery_policy_kind_idx"),
        ("index", "operation_rlm_boundaries_operation_idx"),
        ("index", "operation_workspace_boundaries_operation_idx"),
        ("index", "operation_workspace_selections_state_idx"),
        ("index", "supervisor_runs_state_idx"),
        ("index", "worker_bindings_active_idx"),
        ("index", "worker_bindings_workspace_idx"),
        ("index", "workspace_checkpoint_source_idx"),
        ("table", "operation_attempts"),
        ("table", "operation_checkpoints"),
        ("table", "operation_controls"),
        ("table", "operation_dispatch"),
        ("table", "operation_events"),
        ("table", "operation_leases"),
        ("table", "operation_recovery_decisions"),
        ("table", "operation_recovery_policies"),
        ("table", "operation_rlm_boundaries"),
        ("table", "operation_workspace_boundaries"),
        ("table", "operation_workspace_checkpoint_selections"),
        ("table", "operations"),
        ("table", "runtime_meta"),
        ("table", "schema_migrations"),
        ("table", "supervisor_runs"),
        ("table", "worker_bindings"),
        ("table", "workspace_checkpoint_catalog"),
    }
)

_V6_OBJECTS = frozenset(
    {
        ("table", "migration_v6_attestations"),
        ("table", "rlm_workbench_jobs"),
        ("table", "rlm_workbench_cells"),
        ("table", "rlm_workbench_suspensions"),
        ("table", "caller_work_tickets"),
        ("table", "caller_work_candidate_receipts"),
        ("table", "caller_work_command_receipts"),
        ("trigger", "caller_work_command_receipts_no_update"),
        ("trigger", "caller_work_command_receipts_no_delete"),
        ("table", "rlm_workbench_successor_outbox"),
        ("table", "rlm_workbench_attempt_authority"),
        ("table", "rlm_workbench_rebind_transfers"),
        ("table", "rlm_workbench_artifact_stages"),
        ("table", "rlm_workbench_cell_manifests"),
        ("table", "rlm_workbench_finalization_manifests"),
        ("table", "broker_contract_catalog_v2"),
        ("table", "broker_backend_availability_v2"),
        ("index", "idx_workbench_phase_deadline"),
        ("index", "idx_workbench_cells_state"),
        ("index", "idx_caller_work_state_deadline"),
        ("index", "idx_caller_work_claim_expiry"),
        ("index", "idx_candidate_receipts_ticket"),
        ("index", "idx_caller_command_receipts_ticket"),
        ("index", "idx_artifact_stages_state"),
        ("index", "idx_successor_outbox_state"),
    }
)

_V5_NON_DOMAIN_TABLES = frozenset({"runtime_meta", "schema_migrations"})


def verify_v6_readback(
    connection: sqlite3.Connection,
    attestation: MigrationAttestationDocument,
) -> tuple[int, ...]:
    """Read-only exact-v6 verifier for a caller-owned query-only connection."""

    objects = frozenset(
        (str(row[0]), str(row[1]))
        for row in connection.execute(
            "SELECT type, name FROM sqlite_schema "
            "WHERE type IN ('table','index','trigger','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    )
    if objects != _V5_OBJECTS | _V6_OBJECTS:
        raise InstallerError("FRESH_INSTALL_V6_INVALID", "v6 object inventory differs")
    versions = tuple(
        int(row[0])
        for row in connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        )
    )
    if versions != (1, 2, 3, 4, 5, 6):
        raise InstallerError("FRESH_INSTALL_V6_INVALID", f"unexpected v6 versions: {versions}")
    fields = (
        "attestation_digest",
        "migration_sql_digest",
        "cutover_epoch",
        "snapshot_id",
        "snapshot_sha256",
        "snapshot_size_bytes",
        "canonical_v5_row_set_digest",
        "source_commit",
        "wheel_digest",
        "profile_digest",
        "skill_digest",
        "contract_manifest_digest",
        "external_authority_store_id",
        "external_authority_prepared_digest",
        "started_at_unix_ms",
        "completed_at_unix_ms",
        "foreign_key_violation_count",
        "integrity_result",
    )
    row = connection.execute(
        "SELECT " + ", ".join(fields) + " FROM migration_v6_attestations "
        "WHERE migration_version=6"
    ).fetchone()
    if row is None:
        raise InstallerError("FRESH_INSTALL_V6_INVALID", "v6 attestation row is missing")
    observed = dict(zip(fields, row, strict=True))
    expected = attestation.attestation
    if observed["attestation_digest"] != attestation.attestation_digest:
        raise InstallerError("FRESH_INSTALL_V6_INVALID", "v6 attestation digest mismatch")
    for field in fields[1:]:
        if observed[field] != getattr(expected, field):
            raise InstallerError(
                "FRESH_INSTALL_V6_INVALID", f"v6 attestation field mismatch: {field}"
            )
    integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    if integrity != "ok" or foreign_keys:
        raise InstallerError("FRESH_INSTALL_V6_INVALID", "v6 integrity evidence failed")
    for _kind, table in objects:
        if _kind != "table" or table in _V5_NON_DOMAIN_TABLES | {
            "migration_v6_attestations"
        }:
            continue
        escaped = table.replace('"', '""')
        if int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]) != 0:
            raise InstallerError(
                "FRESH_INSTALL_V6_INVALID", f"v6 domain table is populated: {table}"
            )
    return versions


class SQLiteStageAdapter:
    """Fence existing SQLite helpers to one retained Linux staging directory."""

    def __init__(
        self,
        stage_fd: int,
        stage_identity: FileIdentity | None = None,
        *,
        now_ms: Callable[[], int] | None = None,
        migration_sql_bytes: bytes | None = None,
    ) -> None:
        _require_linux_sqlite_support()
        self.stage_fd = stage_fd
        self.stage_identity = stage_identity or FileIdentity.from_stat(os.fstat(stage_fd))
        self.now_ms = now_ms or (lambda: time.time_ns() // 1_000_000)
        self.migration_sql_bytes = migration_sql_bytes or _migration_sql_bytes()
        self._fence()

    @property
    def database_path(self) -> Path:
        return _fixed_child_path(self._stage_handle_for_property(), DATABASE_NAME)

    def _stage_handle_for_property(self) -> StageHandle:
        observed = os.fstat(self.stage_fd)
        if not self.stage_identity.exact(observed):
            raise InstallerError(
                "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "retained stage identity changed"
            )
        return StageHandle(
            "", self.stage_fd, self.stage_identity, RetainedDirectory("", -1, self.stage_identity)
        )

    def _fence(self) -> None:
        observed = os.fstat(self.stage_fd)
        if not stat.S_ISDIR(observed.st_mode) or not self.stage_identity.exact(observed):
            raise InstallerError(
                "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "retained stage fd is not stable"
            )
        try:
            link = os.readlink(f"/proc/self/fd/{self.stage_fd}")
            proc_stat = os.stat(f"/proc/self/fd/{self.stage_fd}")
        except OSError as error:
            raise InstallerError(
                "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "/proc fd is unavailable"
            ) from error
        if not link or not self.stage_identity.same_inode(proc_stat):
            raise InstallerError(
                "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "/proc fd does not bind stage"
            )

    def _check_entries(
        self,
        *,
        backup: bool,
        phase_directories: frozenset[str] = frozenset(),
    ) -> dict[str, FileIdentity]:
        regular = {DATABASE_NAME, f"{DATABASE_NAME}-wal", f"{DATABASE_NAME}-shm"}
        if backup:
            regular.update({BACKUP_NAME, f"{BACKUP_NAME}-wal", f"{BACKUP_NAME}-shm"})
        entries = _stage_entries(self.stage_fd, regular | set(phase_directories))
        for name in entries:
            observed = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
            if name in phase_directories:
                if not stat.S_ISDIR(observed.st_mode) or stat.S_IMODE(observed.st_mode) != 0o700:
                    raise InstallerError(
                        "FRESH_INSTALL_SQLITE_RESIDUE",
                        f"phase entry is not a mode-0700 directory: {name}",
                    )
            elif not stat.S_ISREG(observed.st_mode):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"SQLite entry is not a regular file: {name}",
                )
        return entries

    def _remove_backup_sidecars(self) -> None:
        """Remove only SQLite-created sidecars for the invocation-owned backup."""

        for suffix in ("-wal", "-shm"):
            name = f"{BACKUP_NAME}{suffix}"
            observed = _entry_stat(self.stage_fd, name)
            if observed is None:
                continue
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"backup sidecar is not a regular no-follow file: {name}",
                )
            identity = FileIdentity.from_stat(observed)
            current = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
            if not identity.exact(current):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"backup sidecar identity changed before removal: {name}",
                )
            try:
                os.unlink(name, dir_fd=self.stage_fd)
            except OSError as error:
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"cannot remove backup sidecar: {name}",
                ) from error
        _fsync_fd(self.stage_fd)
        for suffix in ("-wal", "-shm"):
            name = f"{BACKUP_NAME}{suffix}"
            if _entry_stat(self.stage_fd, name) is not None:
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"backup sidecar remains after removal: {name}",
                )

    def quiesce_database(self) -> None:
        """Checkpoint committed WAL pages and remove only verified empty sidecars."""

        self._fence()
        before = self._check_entries(backup=True)
        self._assert_entry(DATABASE_NAME, positive=True)
        if BACKUP_NAME not in before:
            raise InstallerError("FRESH_INSTALL_BACKUP_INVALID", "temporary backup is missing")
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                _fixed_child_path(self._stage_handle_for_property(), DATABASE_NAME),
                isolation_level=None,
                timeout=5,
            )
            connection.execute("PRAGMA busy_timeout=5000")
            checkpoint = tuple(
                int(value)
                for value in connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            )
            if checkpoint != (0, 0, 0):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_BUSY",
                    f"staged database did not quiesce: {checkpoint}",
                )
        except InstallerError:
            raise
        except (OSError, sqlite3.Error, TypeError) as error:
            raise InstallerError(
                "FRESH_INSTALL_SQLITE_FAILED",
                "cannot checkpoint the staged database",
            ) from error
        finally:
            if connection is not None:
                connection.close()

        self._fence()
        wal_name = f"{DATABASE_NAME}-wal"
        wal = _entry_stat(self.stage_fd, wal_name)
        if wal is not None and wal.st_size != 0:
            raise InstallerError(
                "FRESH_INSTALL_SQLITE_BUSY",
                "staged database WAL is not empty after checkpoint",
            )
        for suffix in ("-wal", "-shm"):
            name = f"{DATABASE_NAME}{suffix}"
            observed = _entry_stat(self.stage_fd, name)
            if observed is None:
                continue
            if stat.S_ISLNK(observed.st_mode) or not stat.S_ISREG(observed.st_mode):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"database sidecar is not a regular no-follow file: {name}",
                )
            identity = FileIdentity.from_stat(observed)
            current = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
            if not identity.exact(current):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"database sidecar identity changed before removal: {name}",
                )
            try:
                os.unlink(name, dir_fd=self.stage_fd)
            except OSError as error:
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"cannot remove database sidecar: {name}",
                ) from error
        _fsync_fd(self.stage_fd)
        self._fence()
        after = self._check_entries(backup=True)
        _assert_identity_unchanged(before, after, DATABASE_NAME, BACKUP_NAME)
        self._assert_entry(DATABASE_NAME, positive=True)

    def construct_empty_v5(self) -> str:
        self._fence()
        before = self._check_entries(backup=False)
        if before:
            raise InstallerError("FRESH_INSTALL_SQLITE_RESIDUE", "stage is not empty before v5")
        registry = None
        try:
            registry = OperationRegistry(self.database_path, self.now_ms)
            versions = registry.schema_versions()
            if versions != (1, 2, 3, 4, 5):
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", f"unexpected v5 versions: {versions}"
                )
        except InstallerError:
            raise
        except Exception as error:
            raise InstallerError(
                "FRESH_INSTALL_V5_INVALID", "OperationRegistry did not construct v1-v5"
            ) from error
        finally:
            if registry is not None:
                registry.close()
        self._fence()
        self._require_private_file(DATABASE_NAME)
        after = self._check_entries(backup=False)
        if DATABASE_NAME not in after:
            raise InstallerError("FRESH_INSTALL_V5_INVALID", "empty v5 database is missing")
        row_digest = self._row_set_digest(DATABASE_NAME)
        self._verify_empty_v5(DATABASE_NAME)
        return row_digest

    def _read_only_connection(self, name: str) -> sqlite3.Connection:
        self._assert_entry(name)
        path = _fixed_child_path(self._stage_handle_for_property(), name)
        uri = f"file:{path}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            connection.execute("PRAGMA foreign_keys=ON")
            return connection
        except sqlite3.Error as error:
            raise InstallerError(
                "FRESH_INSTALL_SQLITE_FAILED", f"cannot open {name} read-only"
            ) from error

    def _assert_entry(self, name: str, *, positive: bool = False) -> os.stat_result:
        self._fence()
        observed = _assert_regular_nofollow(self.stage_fd, name, positive=positive)
        self._fence()
        return observed

    def _require_private_file(self, name: str) -> FileIdentity:
        """Set one retained no-follow regular file to mode 0600 and fence its name."""

        self._fence()
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=self.stage_fd,
        )
        try:
            before = os.fstat(descriptor)
            identity = FileIdentity.from_stat(before)
            if not stat.S_ISREG(before.st_mode):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE", f"SQLite member is not regular: {name}"
                )
            os.fchmod(descriptor, 0o600)
            after = os.fstat(descriptor)
            named = os.stat(name, dir_fd=self.stage_fd, follow_symlinks=False)
            if (
                not identity.same_inode(after)
                or not FileIdentity.from_stat(after).exact(named)
                or stat.S_IMODE(after.st_mode) != 0o600
            ):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_RESIDUE",
                    f"SQLite member did not retain private identity: {name}",
                )
            return FileIdentity.from_stat(after)
        finally:
            os.close(descriptor)

    def _table_names(self, connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
        return tuple(
            (str(row[0]), str(row[1]))
            for row in connection.execute(
                "SELECT type, name FROM sqlite_schema "
                "WHERE type IN ('table','index','trigger','view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
            )
        )

    def _verify_empty_domain_tables(
        self,
        connection: sqlite3.Connection,
        *,
        excluded: frozenset[str],
        error_code: str,
        phase: str,
    ) -> None:
        for _kind, table in self._table_names(connection):
            if _kind != "table" or table in excluded:
                continue
            escaped = table.replace('"', '""')
            if int(connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]) != 0:
                raise InstallerError(error_code, f"{phase} domain table is populated: {table}")

    def _retained_file_digest(self, name: str) -> tuple[int, str]:
        observed = self._assert_entry(name, positive=True)
        try:
            descriptor = os.open(
                name,
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=self.stage_fd,
            )
        except OSError as error:
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", f"cannot retain backup bytes: {name}"
            ) from error
        try:
            retained = os.fstat(descriptor)
            if not FileIdentity.from_stat(observed).exact(retained):
                raise InstallerError(
                    "FRESH_INSTALL_BACKUP_INVALID",
                    f"backup identity changed before hashing: {name}",
                )
            digest = hashlib.sha256()
            size = 0
            while chunk := os.read(descriptor, 1024 * 1024):
                size += len(chunk)
                digest.update(chunk)
        finally:
            os.close(descriptor)
        self._assert_entry(name, positive=True)
        return size, f"sha256:{digest.hexdigest()}"

    def _row_set_digest(self, name: str) -> str:
        connection = self._read_only_connection(name)
        try:
            tables = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_schema WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            )
            payload: list[dict[str, Any]] = []
            for table in tables:
                escaped = table.replace('"', '""')
                columns = [
                    str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')
                ]
                rows: list[list[Any]] = []
                for row in connection.execute(f'SELECT * FROM "{escaped}"'):
                    rows.append(
                        [
                            {"bytes_hex": bytes(value).hex()}
                            if isinstance(value, (bytes, bytearray))
                            else value
                            for value in row
                        ]
                    )
                rows.sort(key=canonical_json_bytes)
                payload.append({"table": table, "columns": columns, "rows": rows})
            return canonical_sha256(payload)
        finally:
            connection.close()

    def _verify_empty_v5(self, name: str) -> None:
        connection = self._read_only_connection(name)
        try:
            if frozenset(self._table_names(connection)) != _V5_OBJECTS:
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", "empty v5 object inventory differs"
                )
            versions = tuple(
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            if versions != (1, 2, 3, 4, 5):
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", f"unexpected v5 versions: {versions}"
                )
            migrations = tuple(
                (int(row[0]), str(row[1]))
                for row in connection.execute(
                    "SELECT version, migration_digest FROM schema_migrations ORDER BY version"
                )
            )
            expected_migrations = tuple(
                (version, OperationRegistry._migration_digest(version))
                for version in (1, 2, 3, 4, 5)
            )
            if migrations != expected_migrations:
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", "empty v5 migration rows differ"
                )
            runtime_rows = tuple(
                (int(row[0]), int(row[1]))
                for row in connection.execute(
                    "SELECT singleton, generation FROM runtime_meta ORDER BY singleton"
                )
            )
            if runtime_rows != ((1, 0),):
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", "empty v5 runtime_meta row differs"
                )
            self._verify_empty_domain_tables(
                connection,
                excluded=_V5_NON_DOMAIN_TABLES,
                error_code="FRESH_INSTALL_V5_INVALID",
                phase="empty v5",
            )
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise InstallerError(
                    "FRESH_INSTALL_V5_INVALID", "empty v5 integrity evidence failed"
                )
        finally:
            connection.close()

    def create_backup(
        self,
        *,
        backup_factory: Callable[[Path, Path], SQLiteBackupSnapshot] | None = None,
    ) -> SQLiteBackupSnapshot:
        self._fence()
        self._assert_entry(DATABASE_NAME, positive=True)
        before = self._check_entries(backup=False)
        if BACKUP_NAME in before:
            raise InstallerError("FRESH_INSTALL_SQLITE_RESIDUE", "backup already exists")
        try:
            create_backup_fn = backup_factory or create_sqlite_backup
            snapshot = create_backup_fn(
                _fixed_child_path(self._stage_handle_for_property(), DATABASE_NAME),
                _fixed_child_path(self._stage_handle_for_property(), BACKUP_NAME),
            )
        except (OSError, MigrationError, ValueError) as error:
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", "SQLite backup creation failed"
            ) from error
        self._fence()
        self._require_private_file(BACKUP_NAME)
        after_create = self._check_entries(backup=True)
        _assert_identity_unchanged(before, after_create, DATABASE_NAME)
        self._assert_entry(BACKUP_NAME, positive=True)
        connection = self._read_only_connection(BACKUP_NAME)
        try:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            if integrity != "ok" or foreign_keys:
                raise InstallerError(
                    "FRESH_INSTALL_BACKUP_INVALID", "backup read-only integrity failed"
                )
            versions = tuple(
                int(row[0])
                for row in connection.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                )
            )
            if versions != (1, 2, 3, 4, 5):
                raise InstallerError(
                    "FRESH_INSTALL_BACKUP_INVALID", "backup is not canonical empty v5"
                )
        finally:
            connection.close()
        try:
            self._verify_empty_v5(BACKUP_NAME)
        except InstallerError as error:
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", "backup exact empty-v5 verification failed"
            ) from error
        self._remove_backup_sidecars()
        after_verify = self._check_entries(backup=True)
        _assert_identity_unchanged(
            after_create,
            after_verify,
            DATABASE_NAME,
            BACKUP_NAME,
        )
        retained_size, retained_digest = self._retained_file_digest(BACKUP_NAME)
        if retained_size <= 0:
            raise InstallerError("FRESH_INSTALL_BACKUP_INVALID", "backup size must be positive")
        if (
            snapshot.size_bytes != retained_size
            or snapshot.sha256 != retained_digest
        ):
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", "backup snapshot does not match retained bytes"
            )
        return snapshot

    def prepare_v6_sidecars(
        self, expected_identities: Mapping[str, FileIdentity]
    ) -> None:
        """Pre-own the exact WAL/SHM names before SQLite may mutate them."""

        self._fence()
        before = self._check_entries(backup=True)
        created: dict[str, FileIdentity] = {}
        try:
            for suffix in ("-wal", "-shm"):
                name = f"{DATABASE_NAME}{suffix}"
                observed = before.get(name)
                expected = expected_identities.get(name)
                if observed is not None:
                    if observed != expected:
                        raise InstallerError(
                            "FRESH_INSTALL_SQLITE_REPLACED",
                            f"SQLite sidecar was not owned before the phase: {name}",
                        )
                    created[name] = observed
                    continue
                if expected is not None:
                    raise InstallerError(
                        "FRESH_INSTALL_SQLITE_REPLACED",
                        f"owned SQLite sidecar disappeared before the phase: {name}",
                    )
                descriptor = os.open(
                    name,
                    os.O_RDWR
                    | os.O_CREAT
                    | os.O_EXCL
                    | os.O_CLOEXEC
                    | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self.stage_fd,
                )
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                    created[name] = FileIdentity.from_stat(os.fstat(descriptor))
                finally:
                    os.close(descriptor)
            _fsync_fd(self.stage_fd)
            self._fence()
            after = self._check_entries(backup=True)
            _assert_identity_unchanged(before, after, DATABASE_NAME, BACKUP_NAME)
            if any(after.get(name) != identity for name, identity in created.items()):
                raise InstallerError(
                    "FRESH_INSTALL_SQLITE_REPLACED",
                    "pre-owned SQLite sidecar identity changed",
                )
        except BaseException:
            for name, identity in created.items():
                if expected_identities.get(name) is not None:
                    continue
                observed = _entry_stat(self.stage_fd, name)
                if observed is not None and identity.exact(observed):
                    os.unlink(name, dir_fd=self.stage_fd)
            _fsync_fd(self.stage_fd)
            raise

    def apply_v6(
        self,
        attestation: MigrationAttestationDocument,
        *,
        fail_after_statement: int | str | None = None,
        migration_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._fence()
        before = self._check_entries(backup=True)
        failure = _normalize_v6_failpoint(fail_after_statement)
        try:
            apply_v6_fn = migration_factory or apply_registry_v6
            apply_v6_fn(
                _fixed_child_path(self._stage_handle_for_property(), DATABASE_NAME),
                migration_sql_bytes=self.migration_sql_bytes,
                attestation=attestation.model_dump(mode="python"),
                fail_after_statement=failure,
            )
        except (MigrationError, OSError, sqlite3.Error, ValueError) as error:
            raise InstallerError(
                "FRESH_INSTALL_V6_FAILED", "v6 transaction did not commit"
            ) from error
        self._fence()
        after = self._check_entries(backup=True)
        _assert_identity_unchanged(before, after, DATABASE_NAME, BACKUP_NAME)

    def verify_v6(self, attestation: MigrationAttestationDocument) -> tuple[int, ...]:
        self._fence()
        before = self._check_entries(backup=True)
        connection = self._read_only_connection(DATABASE_NAME)
        try:
            versions = verify_v6_readback(connection, attestation)
        finally:
            connection.close()
        self._fence()
        after = self._check_entries(backup=True)
        _assert_identity_unchanged(before, after, DATABASE_NAME, BACKUP_NAME)
        return versions

    def remove_backup(self) -> None:
        self._fence()
        before = self._check_entries(
            backup=True,
            phase_directories=frozenset({"authority"}),
        )
        observed = self._assert_entry(BACKUP_NAME, positive=True)
        current = os.stat(BACKUP_NAME, dir_fd=self.stage_fd, follow_symlinks=False)
        if (
            before.get(BACKUP_NAME) != FileIdentity.from_stat(current)
            or current.st_ino != observed.st_ino
        ):
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", "backup identity changed before removal"
            )
        try:
            os.unlink(BACKUP_NAME, dir_fd=self.stage_fd)
        except OSError as error:
            raise InstallerError(
                "FRESH_INSTALL_BACKUP_INVALID", "cannot remove temporary backup"
            ) from error
        _fsync_fd(self.stage_fd)
        self._fence()
        if _entry_stat(self.stage_fd, BACKUP_NAME) is not None:
            raise InstallerError("FRESH_INSTALL_BACKUP_INVALID", "temporary backup remains")
        after = self._check_entries(
            backup=False,
            phase_directories=frozenset({"authority"}),
        )
        _assert_identity_unchanged(before, after, DATABASE_NAME, "authority")
def _normalize_v6_failpoint(value: int | str | None) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if not 0 <= value <= len(V6_STATEMENT_NAMES):
            raise InstallerError("FRESH_INSTALL_V6_FAILED", "v6 failpoint must be non-negative")
        return value
    if isinstance(value, str):
        if value in {
            "before-first-statement",
            "before-attestation-insert",
            "before-schema-migration-insert",
            "before-commit",
            "after-commit-readback",
        }:
            return value
        for number, name in enumerate(V6_STATEMENT_NAMES, start=1):
            if value == f"fail_after_statement_{number:02d}_{name}":
                return value
    raise InstallerError("FRESH_INSTALL_V6_FAILED", f"unknown v6 failpoint: {value!r}")
def _require_linux_sqlite_support() -> None:
    if os.name != "posix" or not sys_platform_linux():
        raise InstallerError(
            "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "clean install requires Linux/WSL"
        )
    if not os.path.isdir("/proc/self/fd"):
        raise InstallerError(
            "FRESH_INSTALL_PUBLICATION_UNSUPPORTED", "/proc/self/fd is unavailable"
        )
def _migration_sql_bytes() -> bytes:
    return frozen_migration_v6_bytes()
