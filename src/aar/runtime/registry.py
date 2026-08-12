"""SQLite-backed operation truth with durable continuity attempts and leases."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, cast

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.continuity_models import (
    DispatchState,
    OperationAttemptRefV1,
    OperationCheckpointBindingV1,
    OperationContinuitySnapshotV1,
    OperationControlStateV1,
    OperationEventEnvelopeV1,
    OperationEventPageV1,
    OperationLeaseRecordV1,
    OperationRecoveryDecisionV1,
    OperationRecoveryPolicyBindingV1,
    OperationRecoveryPolicyV1,
    OperationRlmStepBoundaryV1,
    OperationWorkspaceCheckpointBoundaryV1,
    OperationWorkspaceCheckpointSelectionV1,
)
from aar.runtime.models import OperationEvent, OperationRecord
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
)
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    FailureEnvelope,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    RequestEnvelope,
)


@dataclass(frozen=True)
class OperationDispatchRecordV1:
    operation: OperationRef
    kind: str
    state: str
    queued_at_unix_ms: int | None
    running_at_unix_ms: int | None
    finished_at_unix_ms: int | None
    current_attempt_no: int | None


@dataclass(frozen=True)
class OperationDispatchClaim:
    attempt: OperationAttemptRefV1
    runtime_generation: int
    dispatcher_generation: int
    lease_epoch: int
    owner_digest: str
    kind: str

    @property
    def operation(self) -> OperationRef:
        return self.attempt.operation


@dataclass(frozen=True)
class RecoveryAttemptFence:
    attempt: OperationAttemptRefV1
    runtime_generation: int
    dispatcher_generation: int
    lease_epoch: int


@dataclass(frozen=True)
class SupervisorRunRecord:
    runtime_generation: int
    dispatcher_generation: int
    pid: int
    process_start_identity: str
    capability_digest: str
    runtime_home_digest: str
    endpoint_kind: str
    discovery_digest: str
    state: str
    started_at_unix_ms: int
    ready_at_unix_ms: int | None
    draining_at_unix_ms: int | None
    stopped_at_unix_ms: int | None
    terminal_reason: str | None


@dataclass(frozen=True)
class WorkerBindingRecord:
    worker_id: str
    runtime_generation: int
    worker_kind: str
    workspace_id: str
    workspace_generation: int
    operation_id: str | None
    pid: int
    process_start_identity: str
    capability_digest: str
    environment_digest: str
    state: str
    started_at_unix_ms: int
    heartbeat_at_unix_ms: int
    last_event_sequence: int
    ended_at_unix_ms: int | None
    termination_receipt_json: str | None


TERMINAL_STATES = frozenset(
    {
        OperationState.SUCCEEDED,
        OperationState.FAILED,
        OperationState.CANCELLED,
        OperationState.TIMED_OUT,
    }
)

SCHEMA_VERSION_BASELINE = 1
SCHEMA_VERSION_CONTINUITY = 2
SCHEMA_VERSION_SUPERVISOR = 3
SCHEMA_VERSION_RECOVERY_POLICY = 4
SCHEMA_VERSION_WORKSPACE_RECOVERY = 5
SCHEMA_VERSION_CURRENT = SCHEMA_VERSION_WORKSPACE_RECOVERY
DEFAULT_EVENT_PAGE_LIMIT = 100
MAX_EVENT_PAGE_LIMIT = 500
DEFAULT_EVENT_PAGE_BYTES = 64 * 1024
MAX_EVENT_PAGE_BYTES = 1024 * 1024

_DISPATCH_QUEUED = "queued"
_DISPATCH_RUNNING = "running"
_DISPATCH_PARKED = "parked"
_DISPATCH_COMPLETED = "completed"
_DURABLE_DISPATCH_KINDS = frozenset(
    {"rlm.execute", "workspace.program.execute"}
)
_RECOVERY_START_SUCCESSOR = "start_successor"
_RECOVERY_RESTORE_CHECKPOINT = "restore_checkpoint"
_RECOVERY_SUCCESSOR_DECISIONS = frozenset(
    {_RECOVERY_START_SUCCESSOR, _RECOVERY_RESTORE_CHECKPOINT}
)
_RECOVERY_DECISIONS = frozenset(
    {
        "needs_user",
        "quarantine",
        "reconcile_effect",
        "restore_checkpoint",
        "resume_native",
        _RECOVERY_START_SUCCESSOR,
        "terminal_from_receipt",
    }
)


class RegistryError(RuntimeError):
    """Base class for operation registry contract failures."""


class IdempotencyConflict(RegistryError):
    """The same scoped idempotency key was reused outside its original binding."""


class InvalidTransition(RegistryError):
    """The requested lifecycle transition is not valid from the stored state."""


class StaleRuntimeGeneration(RegistryError):
    """A caller attempted to mutate an operation through a stale runtime generation."""


class UnsupportedRegistrySchema(RegistryError):
    """The database was written by a registry newer than this implementation."""


class StaleAttemptFence(RegistryError):
    """A lease, attempt, owner, or generation fence no longer matches."""


def _operation_id(envelope: RequestEnvelope) -> OperationRef:
    material = "\0".join(
        (envelope.host.value, envelope.principal.value, envelope.idempotency_key)
    ).encode()
    return OperationRef(value=f"op-{hashlib.sha256(material).hexdigest()[:32]}")


def _value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _decision_value(value: Any) -> str:
    normalized = _value(value)
    return str(normalized)



def _operation_ref(value: Any) -> OperationRef:
    if isinstance(value, OperationRef):
        return value
    if hasattr(value, "operation"):
        return _operation_ref(value.operation)
    return OperationRef(value=str(value))


def _attempt_identity(attempt_ref: Any) -> tuple[OperationRef, int, str]:
    nested = getattr(attempt_ref, "ref", None)
    if nested is not None and nested is not attempt_ref:
        attempt_ref = nested
    operation = _operation_ref(getattr(attempt_ref, "operation", None))
    attempt_no = int(attempt_ref.attempt_no)
    attempt_id = str(attempt_ref.attempt_id)
    return operation, attempt_no, attempt_id


class OperationRegistry:
    """Persist accepted intent before acknowledgement and never infer uncertain success."""

    def __init__(self, database_path: Path, now_ms: Callable[[], int]) -> None:
        self.database_path = database_path.resolve()
        self._now_ms = now_ms
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path,
            isolation_level="IMMEDIATE",
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def _table_exists(self, table_name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _column_names(self, table_name: str) -> set[str]:
        return {
            str(row["name"])
            for row in self._connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        }

    def _execute_ddl_script(self, script: str) -> None:
        """Run simple migration DDL statements without executescript's implicit commit."""

        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._connection.execute(statement)

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if self._table_exists("schema_migrations"):
                    migration_rows = self._connection.execute(
                        """
                        SELECT version, migration_digest
                        FROM schema_migrations ORDER BY version
                        """
                    ).fetchall()
                    versions = [int(row["version"]) for row in migration_rows]
                    newer = [version for version in versions if version > SCHEMA_VERSION_CURRENT]
                    if newer:
                        raise UnsupportedRegistrySchema(
                            "registry schema version "
                            f"{max(newer)} is newer than supported version "
                            f"{SCHEMA_VERSION_CURRENT}"
                        )
                    expected_prefix = list(range(1, len(versions) + 1))
                    if versions != expected_prefix:
                        raise UnsupportedRegistrySchema(
                            f"registry migration sequence is not contiguous: {versions}"
                        )
                    for migration_row in migration_rows:
                        version = int(migration_row["version"])
                        if migration_row["migration_digest"] != self._migration_digest(version):
                            raise UnsupportedRegistrySchema(
                                f"registry migration {version} digest does not match"
                            )
                else:
                    versions = []

                if not versions:
                    self._create_v1_schema()
                    self._insert_schema_version(SCHEMA_VERSION_BASELINE)
                    current_version = SCHEMA_VERSION_BASELINE
                else:
                    current_version = max(versions)
                    if current_version < SCHEMA_VERSION_BASELINE:
                        raise RegistryError("registry schema has no supported baseline")

                # Reapply additive DDL idempotently so interrupted migrations converge.
                self._apply_v2_schema()
                if current_version < SCHEMA_VERSION_CONTINUITY:
                    self._insert_schema_version(SCHEMA_VERSION_CONTINUITY)
                    current_version = SCHEMA_VERSION_CONTINUITY
                self._apply_v3_schema()
                if current_version < SCHEMA_VERSION_SUPERVISOR:
                    self._insert_schema_version(SCHEMA_VERSION_SUPERVISOR)
                    current_version = SCHEMA_VERSION_SUPERVISOR
                self._apply_v4_schema()
                if current_version < SCHEMA_VERSION_RECOVERY_POLICY:
                    self._insert_schema_version(SCHEMA_VERSION_RECOVERY_POLICY)
                    current_version = SCHEMA_VERSION_RECOVERY_POLICY
                self._apply_v5_schema()
                if current_version < SCHEMA_VERSION_WORKSPACE_RECOVERY:
                    self._insert_schema_version(SCHEMA_VERSION_WORKSPACE_RECOVERY)

                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def _create_v1_schema(self) -> None:
        self._execute_ddl_script(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at_unix_ms INTEGER NOT NULL,
                migration_digest TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS runtime_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                generation INTEGER NOT NULL
            );
            INSERT OR IGNORE INTO runtime_meta(singleton, generation) VALUES (1, 0);

            CREATE TABLE IF NOT EXISTS operations (
                operation_id TEXT PRIMARY KEY,
                host_value TEXT NOT NULL,
                principal_value TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                runtime_generation INTEGER NOT NULL,
                record_revision INTEGER NOT NULL,
                reconciliation_required INTEGER NOT NULL,
                request_json TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                result_json TEXT,
                failure_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                UNIQUE(host_value, principal_value, idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                record_revision INTEGER NOT NULL,
                at_unix_ms INTEGER NOT NULL,
                note TEXT NOT NULL
            );
            """
        )

    @staticmethod
    def _migration_digest(version: int) -> str:
        return canonical_sha256(
            {
                "registry": "aar.runtime.registry",
                "version": version,
            }
        )

    def _insert_schema_version(self, version: int) -> None:
        digest = self._migration_digest(version)
        self._connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations(version, applied_at_unix_ms, migration_digest)
            VALUES (?, ?, ?)
            """,
            (version, self._now_ms(), digest),
        )

    def _apply_v2_schema(self) -> None:
        if not self._table_exists("schema_migrations"):
            self._connection.execute(
                """
                CREATE TABLE schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at_unix_ms INTEGER NOT NULL,
                    migration_digest TEXT NOT NULL
                )
                """
            )

        event_columns = self._column_names("operation_events")
        for name, declaration in (
            ("attempt_no", "INTEGER"),
            ("event_kind", "TEXT"),
            ("payload_json", "TEXT"),
            ("payload_digest", "TEXT"),
        ):
            if name not in event_columns:
                self._connection.execute(
                    f"ALTER TABLE operation_events ADD COLUMN {name} {declaration}"
                )

        self._execute_ddl_script(
            """
            CREATE TABLE IF NOT EXISTS operation_attempts (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                attempt_no INTEGER NOT NULL CHECK (attempt_no > 0),
                attempt_id TEXT NOT NULL UNIQUE,
                runtime_generation INTEGER NOT NULL,
                dispatcher_generation INTEGER NOT NULL,
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                recovery_reason TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                started_at_unix_ms INTEGER,
                ended_at_unix_ms INTEGER,
                checkpoint_digest TEXT,
                PRIMARY KEY (operation_id, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS operation_leases (
                operation_id TEXT NOT NULL,
                attempt_no INTEGER NOT NULL,
                lease_epoch INTEGER NOT NULL CHECK (lease_epoch > 0),
                owner_digest TEXT NOT NULL,
                acquired_at_unix_ms INTEGER NOT NULL,
                heartbeat_at_unix_ms INTEGER NOT NULL,
                expires_at_unix_ms INTEGER NOT NULL,
                released_at_unix_ms INTEGER,
                PRIMARY KEY (operation_id, attempt_no, lease_epoch),
                FOREIGN KEY (operation_id, attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS operation_controls (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                control_revision INTEGER NOT NULL CHECK (control_revision > 0),
                cancellation_requested INTEGER NOT NULL CHECK (cancellation_requested IN (0, 1)),
                requested_at_unix_ms INTEGER,
                requested_by_digest TEXT,
                reason_code TEXT
            );

            CREATE TABLE IF NOT EXISTS operation_dispatch (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                kind TEXT NOT NULL,
                state TEXT NOT NULL,
                queued_at_unix_ms INTEGER,
                running_at_unix_ms INTEGER,
                finished_at_unix_ms INTEGER,
                current_attempt_no INTEGER
            );

            CREATE TABLE IF NOT EXISTS operation_checkpoints (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                attempt_no INTEGER NOT NULL,
                checkpoint_digest TEXT NOT NULL,
                checkpoint_kind TEXT NOT NULL,
                artifact_id TEXT,
                media_type TEXT,
                size_bytes INTEGER,
                created_by_operation_id TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, attempt_no, checkpoint_digest),
                FOREIGN KEY (operation_id, attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS operation_recovery_decisions (
                operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                decision_no INTEGER NOT NULL CHECK (decision_no > 0),
                prior_attempt_no INTEGER,
                decision TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                input_digest TEXT NOT NULL,
                checkpoint_digest TEXT,
                successor_attempt_no INTEGER,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, decision_no)
            );

            CREATE INDEX IF NOT EXISTS operation_dispatch_queue_idx
                ON operation_dispatch(state, queued_at_unix_ms, operation_id);
            CREATE INDEX IF NOT EXISTS operation_attempts_state_idx
                ON operation_attempts(operation_id, state, attempt_no);
            CREATE INDEX IF NOT EXISTS operation_events_operation_idx
                ON operation_events(operation_id, sequence);
            """
        )

    def _apply_v3_schema(self) -> None:
        self._execute_ddl_script(
            """
            CREATE TABLE IF NOT EXISTS supervisor_runs (
                runtime_generation INTEGER PRIMARY KEY,
                dispatcher_generation INTEGER NOT NULL,
                pid INTEGER NOT NULL CHECK (pid > 0),
                process_start_identity TEXT NOT NULL,
                capability_digest TEXT NOT NULL,
                runtime_home_digest TEXT NOT NULL,
                endpoint_kind TEXT NOT NULL,
                discovery_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at_unix_ms INTEGER NOT NULL,
                ready_at_unix_ms INTEGER,
                draining_at_unix_ms INTEGER,
                stopped_at_unix_ms INTEGER,
                terminal_reason TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_bindings (
                worker_id TEXT PRIMARY KEY,
                runtime_generation INTEGER NOT NULL,
                worker_kind TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                workspace_generation INTEGER NOT NULL CHECK (workspace_generation > 0),
                operation_id TEXT,
                pid INTEGER NOT NULL CHECK (pid > 0),
                process_start_identity TEXT NOT NULL,
                capability_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                started_at_unix_ms INTEGER NOT NULL,
                heartbeat_at_unix_ms INTEGER NOT NULL,
                last_event_sequence INTEGER NOT NULL DEFAULT 0 CHECK (last_event_sequence >= 0),
                ended_at_unix_ms INTEGER,
                termination_receipt_json TEXT
            );

            CREATE INDEX IF NOT EXISTS supervisor_runs_state_idx
                ON supervisor_runs(state, runtime_generation);
            CREATE INDEX IF NOT EXISTS worker_bindings_active_idx
                ON worker_bindings(state, runtime_generation, worker_id);
            CREATE INDEX IF NOT EXISTS worker_bindings_workspace_idx
                ON worker_bindings(workspace_id, workspace_generation, runtime_generation);
            """
        )


    def _apply_v4_schema(self) -> None:
        decision_columns = self._column_names("operation_recovery_decisions")
        for name, declaration in (
            ("policy_version", "INTEGER"),
            ("policy_digest", "TEXT"),
            ("effect_receipt_digest", "TEXT"),
            ("continuation_boundary_digest", "TEXT"),
        ):
            if name not in decision_columns:
                self._connection.execute(
                    f"ALTER TABLE operation_recovery_decisions ADD COLUMN {name} {declaration}"
                )

        self._execute_ddl_script(
            """
            CREATE TABLE IF NOT EXISTS operation_recovery_policies (
                operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
                operation_kind TEXT NOT NULL,
                policy_version INTEGER NOT NULL CHECK (policy_version > 0),
                policy_id TEXT NOT NULL,
                policy_json TEXT NOT NULL,
                policy_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operation_rlm_boundaries (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                boundary_digest TEXT NOT NULL,
                boundary_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(boundary_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );

            CREATE INDEX IF NOT EXISTS operation_recovery_policy_kind_idx
                ON operation_recovery_policies(operation_kind, policy_id, policy_version);
            CREATE INDEX IF NOT EXISTS operation_rlm_boundaries_operation_idx
                ON operation_rlm_boundaries(operation_id, prior_attempt_no);
            """
        )

    def _apply_v5_schema(self) -> None:
        self._execute_ddl_script(
            """
            CREATE TABLE IF NOT EXISTS workspace_checkpoint_catalog (
                manifest_digest TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                source_generation INTEGER NOT NULL CHECK (source_generation > 0),
                source_revision INTEGER NOT NULL CHECK (source_revision >= 0),
                backend_capability_digest TEXT NOT NULL,
                environment_digest TEXT NOT NULL,
                creation_operation_id TEXT NOT NULL REFERENCES operations(operation_id),
                manifest_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS operation_workspace_boundaries (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                boundary_digest TEXT NOT NULL,
                boundary_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(boundary_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );

            CREATE TABLE IF NOT EXISTS operation_workspace_checkpoint_selections (
                operation_id TEXT NOT NULL,
                prior_attempt_no INTEGER NOT NULL CHECK (prior_attempt_no > 0),
                selection_digest TEXT NOT NULL,
                selection_json TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('selected', 'restored')),
                restored_handle_json TEXT,
                selected_at_unix_ms INTEGER NOT NULL,
                restored_at_unix_ms INTEGER,
                PRIMARY KEY (operation_id, prior_attempt_no),
                UNIQUE(selection_digest),
                FOREIGN KEY (operation_id, prior_attempt_no)
                    REFERENCES operation_attempts(operation_id, attempt_no)
            );

            CREATE INDEX IF NOT EXISTS workspace_checkpoint_source_idx
                ON workspace_checkpoint_catalog(
                    workspace_id, source_generation, source_revision, created_at_unix_ms
                );
            CREATE INDEX IF NOT EXISTS operation_workspace_boundaries_operation_idx
                ON operation_workspace_boundaries(operation_id, prior_attempt_no);
            CREATE INDEX IF NOT EXISTS operation_workspace_selections_state_idx
                ON operation_workspace_checkpoint_selections(state, operation_id);
            """
        )


    def schema_versions(self) -> tuple[int, ...]:
        """Return the applied registry migration versions without mutating the database."""

        with self._lock:
            rows = self._connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            return tuple(int(row["version"]) for row in rows)

    def record_supervisor_ready(
        self,
        *,
        runtime_generation: int,
        dispatcher_generation: int,
        pid: int,
        process_start_identity: str,
        capability_digest: str,
        runtime_home_digest: str,
        endpoint_kind: str,
        discovery_digest: str,
    ) -> SupervisorRunRecord:
        """Persist the exact ready owner before attachments or claims are accepted."""

        with self._lock, self._connection:
            current = self.current_runtime_generation()
            if runtime_generation != current or dispatcher_generation != current:
                raise StaleRuntimeGeneration(
                    f"supervisor generation {runtime_generation}/{dispatcher_generation} "
                    f"does not match runtime {current}"
                )
            now = self._now_ms()
            self._connection.execute(
                """
                INSERT INTO supervisor_runs(
                    runtime_generation, dispatcher_generation, pid, process_start_identity,
                    capability_digest, runtime_home_digest, endpoint_kind, discovery_digest,
                    state, started_at_unix_ms, ready_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    runtime_generation,
                    dispatcher_generation,
                    pid,
                    process_start_identity,
                    capability_digest,
                    runtime_home_digest,
                    endpoint_kind,
                    discovery_digest,
                    now,
                    now,
                ),
            )
            return self._supervisor_run(runtime_generation)

    def transition_supervisor_run(
        self,
        *,
        runtime_generation: int,
        pid: int,
        process_start_identity: str,
        state: str,
        terminal_reason: str | None = None,
    ) -> SupervisorRunRecord:
        """Advance one exact supervisor receipt without accepting PID-only ownership."""

        if state not in {"degraded", "draining", "reconcile-required", "stopped"}:
            raise ValueError(f"unsupported supervisor state transition: {state}")
        with self._lock, self._connection:
            prior = self._supervisor_run(runtime_generation)
            if prior.pid != pid or prior.process_start_identity != process_start_identity:
                raise StaleRuntimeGeneration("supervisor process identity is stale")
            allowed = {
                "ready": {"degraded", "draining", "reconcile-required"},
                "degraded": {"draining", "reconcile-required"},
                "draining": {"stopped", "reconcile-required"},
                "reconcile-required": {"stopped"},
                "stopped": set(),
            }
            if state not in allowed.get(prior.state, set()):
                raise InvalidTransition(f"cannot transition supervisor {prior.state} -> {state}")
            now = self._now_ms()
            draining_at = now if state == "draining" else prior.draining_at_unix_ms
            stopped_at = now if state in {"reconcile-required", "stopped"} else None
            self._connection.execute(
                """
                UPDATE supervisor_runs
                SET state = ?, draining_at_unix_ms = ?, stopped_at_unix_ms = ?,
                    terminal_reason = ?
                WHERE runtime_generation = ? AND pid = ? AND process_start_identity = ?
                """,
                (
                    state,
                    draining_at,
                    stopped_at,
                    terminal_reason,
                    runtime_generation,
                    pid,
                    process_start_identity,
                ),
            )
            return self._supervisor_run(runtime_generation)

    def supervisor_runs(self) -> tuple[SupervisorRunRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM supervisor_runs ORDER BY runtime_generation"
            ).fetchall()
            return tuple(self._supervisor_run_from_row(row) for row in rows)

    def reconcile_predecessor_supervisor_runs(self, current_runtime_generation: int) -> int:
        """Classify nonterminal predecessor owners after the exclusive runtime lock is held."""

        with self._lock, self._connection:
            current = self.current_runtime_generation()
            if current != current_runtime_generation:
                raise StaleRuntimeGeneration(
                    f"expected runtime generation {current_runtime_generation}, "
                    f"current is {current}"
                )
            cursor = self._connection.execute(
                """
                UPDATE supervisor_runs
                SET state = 'reconcile-required',
                    stopped_at_unix_ms = ?,
                    terminal_reason = 'predecessor_process_absent_on_startup'
                WHERE runtime_generation < ?
                  AND state IN ('ready', 'degraded', 'draining')
                """,
                (self._now_ms(), current_runtime_generation),
            )
            return cursor.rowcount

    def register_worker_binding(
        self,
        *,
        worker_id: str,
        runtime_generation: int,
        worker_kind: str,
        workspace_id: str,
        workspace_generation: int,
        pid: int,
        process_start_identity: str,
        capability_digest: str,
        environment_digest: str,
    ) -> WorkerBindingRecord:
        """Create one immutable PID/start binding under the current supervisor generation."""

        with self._lock, self._connection:
            current = self.current_runtime_generation()
            if runtime_generation != current:
                raise StaleRuntimeGeneration(
                    f"worker runtime generation {runtime_generation} does not match {current}"
                )
            now = self._now_ms()
            self._connection.execute(
                """
                INSERT INTO worker_bindings(
                    worker_id, runtime_generation, worker_kind, workspace_id,
                    workspace_generation, pid, process_start_identity, capability_digest,
                    environment_digest, state, started_at_unix_ms, heartbeat_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', ?, ?)
                """,
                (
                    worker_id,
                    runtime_generation,
                    worker_kind,
                    workspace_id,
                    workspace_generation,
                    pid,
                    process_start_identity,
                    capability_digest,
                    environment_digest,
                    now,
                    now,
                ),
            )
            return self._worker_binding(worker_id)

    def heartbeat_worker_binding(
        self,
        *,
        worker_id: str,
        runtime_generation: int,
        process_start_identity: str,
        state: str,
        operation_id: str | None = None,
        last_event_sequence: int | None = None,
    ) -> WorkerBindingRecord:
        if state not in {"ready", "busy", "terminating"}:
            raise ValueError(f"unsupported active worker state: {state}")
        with self._lock, self._connection:
            binding = self._worker_binding(worker_id)
            if (
                binding.runtime_generation != runtime_generation
                or binding.process_start_identity != process_start_identity
                or binding.state not in {"ready", "busy", "terminating"}
            ):
                raise StaleAttemptFence("worker binding fence is stale")
            sequence = (
                binding.last_event_sequence
                if last_event_sequence is None
                else last_event_sequence
            )
            if sequence < binding.last_event_sequence:
                raise StaleAttemptFence("worker event sequence cannot move backwards")
            self._connection.execute(
                """
                UPDATE worker_bindings
                SET state = ?, operation_id = ?, heartbeat_at_unix_ms = ?,
                    last_event_sequence = ?
                WHERE worker_id = ? AND runtime_generation = ?
                    AND process_start_identity = ?
                """,
                (
                    state,
                    operation_id,
                    self._now_ms(),
                    sequence,
                    worker_id,
                    runtime_generation,
                    process_start_identity,
                ),
            )
            return self._worker_binding(worker_id)

    def finish_worker_binding(
        self,
        *,
        worker_id: str,
        runtime_generation: int,
        process_start_identity: str,
        state: str,
        termination_receipt_json: str,
    ) -> WorkerBindingRecord:
        if state not in {"lost", "quarantined", "terminated"}:
            raise ValueError(f"unsupported terminal worker state: {state}")
        with self._lock, self._connection:
            binding = self._worker_binding(worker_id)
            if (
                binding.runtime_generation != runtime_generation
                or binding.process_start_identity != process_start_identity
                or binding.state not in {"ready", "busy", "terminating"}
            ):
                raise StaleAttemptFence("worker terminal fence is stale")
            self._connection.execute(
                """
                UPDATE worker_bindings
                SET state = ?, operation_id = NULL, heartbeat_at_unix_ms = ?,
                    ended_at_unix_ms = ?, termination_receipt_json = ?
                WHERE worker_id = ? AND runtime_generation = ?
                    AND process_start_identity = ?
                """,
                (
                    state,
                    self._now_ms(),
                    self._now_ms(),
                    termination_receipt_json,
                    worker_id,
                    runtime_generation,
                    process_start_identity,
                ),
            )
            return self._worker_binding(worker_id)

    def active_worker_bindings(self) -> tuple[WorkerBindingRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM worker_bindings
                WHERE state IN ('ready', 'busy', 'terminating')
                ORDER BY runtime_generation, worker_id
                """
            ).fetchall()
            return tuple(self._worker_binding_from_row(row) for row in rows)

    def worker_binding(self, worker_id: str) -> WorkerBindingRecord:
        with self._lock:
            return self._worker_binding(worker_id)

    def _supervisor_run(self, runtime_generation: int) -> SupervisorRunRecord:
        row = self._connection.execute(
            "SELECT * FROM supervisor_runs WHERE runtime_generation = ?",
            (runtime_generation,),
        ).fetchone()
        if row is None:
            raise RegistryError(f"supervisor run {runtime_generation} does not exist")
        return self._supervisor_run_from_row(row)

    @staticmethod
    def _supervisor_run_from_row(row: sqlite3.Row) -> SupervisorRunRecord:
        return SupervisorRunRecord(
            runtime_generation=int(row["runtime_generation"]),
            dispatcher_generation=int(row["dispatcher_generation"]),
            pid=int(row["pid"]),
            process_start_identity=str(row["process_start_identity"]),
            capability_digest=str(row["capability_digest"]),
            runtime_home_digest=str(row["runtime_home_digest"]),
            endpoint_kind=str(row["endpoint_kind"]),
            discovery_digest=str(row["discovery_digest"]),
            state=str(row["state"]),
            started_at_unix_ms=int(row["started_at_unix_ms"]),
            ready_at_unix_ms=(
                None if row["ready_at_unix_ms"] is None else int(row["ready_at_unix_ms"])
            ),
            draining_at_unix_ms=(
                None
                if row["draining_at_unix_ms"] is None
                else int(row["draining_at_unix_ms"])
            ),
            stopped_at_unix_ms=(
                None if row["stopped_at_unix_ms"] is None else int(row["stopped_at_unix_ms"])
            ),
            terminal_reason=(
                None if row["terminal_reason"] is None else str(row["terminal_reason"])
            ),
        )

    def _worker_binding(self, worker_id: str) -> WorkerBindingRecord:
        row = self._connection.execute(
            "SELECT * FROM worker_bindings WHERE worker_id = ?", (worker_id,)
        ).fetchone()
        if row is None:
            raise RegistryError(f"worker binding {worker_id} does not exist")
        return self._worker_binding_from_row(row)

    @staticmethod
    def _worker_binding_from_row(row: sqlite3.Row) -> WorkerBindingRecord:
        return WorkerBindingRecord(
            worker_id=str(row["worker_id"]),
            runtime_generation=int(row["runtime_generation"]),
            worker_kind=str(row["worker_kind"]),
            workspace_id=str(row["workspace_id"]),
            workspace_generation=int(row["workspace_generation"]),
            operation_id=(None if row["operation_id"] is None else str(row["operation_id"])),
            pid=int(row["pid"]),
            process_start_identity=str(row["process_start_identity"]),
            capability_digest=str(row["capability_digest"]),
            environment_digest=str(row["environment_digest"]),
            state=str(row["state"]),
            started_at_unix_ms=int(row["started_at_unix_ms"]),
            heartbeat_at_unix_ms=int(row["heartbeat_at_unix_ms"]),
            last_event_sequence=int(row["last_event_sequence"]),
            ended_at_unix_ms=(
                None if row["ended_at_unix_ms"] is None else int(row["ended_at_unix_ms"])
            ),
            termination_receipt_json=(
                None
                if row["termination_receipt_json"] is None
                else str(row["termination_receipt_json"])
            ),
        )

    def start_runtime(self) -> int:
        """Start a generation, rebound accepted work, and fence running attempts."""

        with self._lock, self._connection:
            row = self._connection.execute(
                "UPDATE runtime_meta SET generation = generation + 1 WHERE singleton = 1 "
                "RETURNING generation"
            ).fetchone()
            assert row is not None
            generation = int(row["generation"])
            accepted = self._connection.execute(
                "SELECT operation_id, record_revision FROM operations WHERE state = ?",
                (OperationState.ACCEPTED.value,),
            ).fetchall()
            running = self._connection.execute(
                "SELECT operation_id, record_revision FROM operations WHERE state = ?",
                (OperationState.RUNNING.value,),
            ).fetchall()
            now = self._now_ms()
            for item in accepted:
                revision = int(item["record_revision"]) + 1
                self._connection.execute(
                    """
                    UPDATE operations
                    SET runtime_generation = ?, record_revision = ?, updated_at_unix_ms = ?
                    WHERE operation_id = ?
                    """,
                    (generation, revision, now, item["operation_id"]),
                )
                self._insert_event(
                    str(item["operation_id"]),
                    OperationState.ACCEPTED,
                    OutcomeCertainty.CERTAIN,
                    revision,
                    now,
                    "runtime_restart_rebound_accepted",
                )
            for item in running:
                operation_id = str(item["operation_id"])
                revision = int(item["record_revision"]) + 1
                attempt = self._connection.execute(
                    """
                    SELECT * FROM operation_attempts
                    WHERE operation_id = ? AND state = ?
                    ORDER BY attempt_no DESC LIMIT 1
                    """,
                    (operation_id, OperationState.RUNNING.value),
                ).fetchone()
                attempt_no = None if attempt is None else int(attempt["attempt_no"])
                self._connection.execute(
                    """
                    UPDATE operations
                    SET state = ?, certainty = ?, record_revision = ?,
                        reconciliation_required = 1, updated_at_unix_ms = ?
                    WHERE operation_id = ?
                    """,
                    (
                        OperationState.INDETERMINATE.value,
                        OutcomeCertainty.INDETERMINATE.value,
                        revision,
                        now,
                        operation_id,
                    ),
                )
                if attempt is not None:
                    self._connection.execute(
                        """
                        UPDATE operation_attempts
                        SET state = ?, certainty = ?, recovery_reason = ?, ended_at_unix_ms = ?
                        WHERE operation_id = ? AND attempt_no = ?
                        """,
                        (
                            OperationState.INDETERMINATE.value,
                            OutcomeCertainty.INDETERMINATE.value,
                            "runtime_restart",
                            now,
                            operation_id,
                            int(attempt["attempt_no"]),
                        ),
                    )
                    self._connection.execute(
                        """
                        UPDATE operation_leases
                        SET released_at_unix_ms = ?
                        WHERE operation_id = ? AND attempt_no = ? AND released_at_unix_ms IS NULL
                        """,
                        (now, operation_id, int(attempt["attempt_no"])),
                    )
                # Deliberately leave dispatch.state=running: recovery must explicitly decide.
                self._insert_event(
                    operation_id,
                    OperationState.INDETERMINATE,
                    OutcomeCertainty.INDETERMINATE,
                    revision,
                    now,
                    "runtime_restart",
                    attempt_no=attempt_no,
                    event_kind="runtime_restart",
                    payload={"attempt_no": attempt_no, "runtime_generation": generation},
                )
            return generation

    def current_runtime_generation(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT generation FROM runtime_meta WHERE singleton = 1"
            ).fetchone()
            assert row is not None
            return int(row["generation"])

    def accept(
        self,
        envelope: RequestEnvelope,
        payload_json: str,
    ) -> tuple[OperationRecord, bool]:
        operation = _operation_id(envelope)
        request_json = canonical_json_bytes(envelope).decode()
        with self._lock, self._connection:
            now = self._now_ms()
            existing = self._connection.execute(
                """
                SELECT * FROM operations
                WHERE host_value = ? AND principal_value = ? AND idempotency_key = ?
                """,
                (envelope.host.value, envelope.principal.value, envelope.idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["input_digest"] != envelope.input_digest:
                    raise IdempotencyConflict(
                        "idempotency key already binds a different input digest"
                    )
                if str(existing["payload_json"]) != payload_json:
                    raise IdempotencyConflict(
                        "idempotency key already binds different payload bytes"
                    )
                stored = RequestEnvelope.model_validate_json(
                    str(existing["request_json"]), strict=True
                )
                replay_scope = (
                    "session",
                    "lane",
                    "workspace",
                    "workspace_generation",
                    "expected_workspace_revision",
                    "parent_operation",
                )
                if any(
                    getattr(stored, field_name) != getattr(envelope, field_name)
                    for field_name in replay_scope
                ):
                    raise IdempotencyConflict(
                        "idempotency key already binds a different authority scope"
                    )
                return self._record(existing), False

            self._connection.execute(
                """
                INSERT INTO operations(
                    operation_id, host_value, principal_value, idempotency_key, input_digest,
                    state, certainty, runtime_generation, record_revision,
                    reconciliation_required, request_json, payload_json, created_at_unix_ms,
                    updated_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?)
                """,
                (
                    operation.value,
                    envelope.host.value,
                    envelope.principal.value,
                    envelope.idempotency_key,
                    envelope.input_digest,
                    OperationState.ACCEPTED.value,
                    OutcomeCertainty.CERTAIN.value,
                    envelope.runtime_generation,
                    request_json,
                    payload_json,
                    now,
                    now,
                ),
            )
            self._insert_event(
                operation.value,
                OperationState.ACCEPTED,
                OutcomeCertainty.CERTAIN,
                0,
                now,
                "intent_persisted",
            )
            return self.get(operation), True

    def bind_recovery_policy(
        self,
        operation: OperationRef,
        *,
        operation_kind: str,
        policy: OperationRecoveryPolicyV1,
        environment_digest: str,
    ) -> OperationRecoveryPolicyBindingV1:
        """Bind exact recovery policy bytes before durable dispatch can begin."""

        policy_json = canonical_json_bytes(policy).decode()
        policy_digest = canonical_sha256(policy)
        if policy.operation_kind != operation_kind:
            raise RegistryError("recovery policy operation kind does not match admission kind")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM operation_recovery_policies WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is not None:
                existing = self._recovery_policy_binding(operation, row)
                if (
                    existing.operation_kind != operation_kind
                    or existing.policy_digest != policy_digest
                    or existing.environment_digest != environment_digest
                    or str(row["policy_json"]) != policy_json
                ):
                    raise IdempotencyConflict(
                        "operation already binds different recovery policy bytes"
                    )
                return existing

            operation_row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if operation_row is None:
                raise KeyError(operation.value)
            dispatch_exists = self._connection.execute(
                "SELECT 1 FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            attempt_exists = self._connection.execute(
                "SELECT 1 FROM operation_attempts WHERE operation_id = ? LIMIT 1",
                (operation.value,),
            ).fetchone()
            if (
                OperationState(str(operation_row["state"])) is not OperationState.ACCEPTED
                or dispatch_exists is not None
                or attempt_exists is not None
            ):
                raise InvalidTransition(
                    "recovery policy must be bound before durable dispatch"
                )

            now = self._now_ms()
            self._connection.execute(
                """
                INSERT INTO operation_recovery_policies(
                    operation_id, operation_kind, policy_version, policy_id, policy_json,
                    policy_digest, environment_digest, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation.value,
                    operation_kind,
                    policy.policy_version,
                    policy.policy_id,
                    policy_json,
                    policy_digest,
                    environment_digest,
                    now,
                ),
            )
            self._insert_event(
                operation.value,
                OperationState.ACCEPTED,
                OutcomeCertainty.CERTAIN,
                int(operation_row["record_revision"]),
                now,
                "recovery_policy_bound",
                event_kind="recovery_policy_bound",
                payload={
                    "operation_kind": operation_kind,
                    "policy_id": policy.policy_id,
                    "policy_version": policy.policy_version,
                    "policy_digest": policy_digest,
                    "environment_digest": environment_digest,
                },
            )
            created = self._connection.execute(
                "SELECT * FROM operation_recovery_policies WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            assert created is not None
            return self._recovery_policy_binding(operation, created)

    def recovery_policy_binding(
        self,
        operation: OperationRef,
    ) -> OperationRecoveryPolicyBindingV1 | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operation_recovery_policies WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            return None if row is None else self._recovery_policy_binding(operation, row)

    def latest_recovery_attempt_fence(
        self,
        operation: OperationRef,
    ) -> RecoveryAttemptFence | None:
        """Read the immutable predecessor attempt and highest lease epoch for recovery."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT a.attempt_no, a.attempt_id, a.runtime_generation,
                       a.dispatcher_generation, l.lease_epoch
                FROM operation_attempts AS a
                JOIN operation_leases AS l
                  ON l.operation_id = a.operation_id AND l.attempt_no = a.attempt_no
                WHERE a.operation_id = ?
                ORDER BY a.attempt_no DESC, l.lease_epoch DESC
                LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
            if row is None:
                return None
            return RecoveryAttemptFence(
                attempt=OperationAttemptRefV1(
                    operation=operation,
                    attempt_no=int(row["attempt_no"]),
                    attempt_id=str(row["attempt_id"]),
                ),
                runtime_generation=int(row["runtime_generation"]),
                dispatcher_generation=int(row["dispatcher_generation"]),
                lease_epoch=int(row["lease_epoch"]),
            )

    def rlm_step_boundaries(
        self,
        operation: OperationRef,
    ) -> tuple[OperationRlmStepBoundaryV1, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT boundary_json FROM operation_rlm_boundaries
                WHERE operation_id = ? ORDER BY prior_attempt_no
                """,
                (operation.value,),
            ).fetchall()
            return tuple(
                OperationRlmStepBoundaryV1.model_validate_json(
                    str(row["boundary_json"]), strict=True
                )
                for row in rows
            )

    def succeed_workspace_checkpoint(
        self,
        operation: OperationRef,
        manifest: WorkspaceCheckpointManifest,
        runtime_generation: int,
    ) -> OperationRecord:
        """Atomically publish a completed checkpoint receipt and catalog row."""

        manifest = WorkspaceCheckpointManifest.model_validate_json(
            canonical_json_bytes(manifest), strict=True
        )
        if manifest.creation_operation != operation:
            raise IdempotencyConflict(
                "checkpoint creation operation does not match the published operation"
            )
        manifest_json = canonical_json_bytes(manifest).decode()
        handle = manifest.source_handle
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if OperationState(row["state"]) is not OperationState.RUNNING:
                raise InvalidTransition("checkpoint publication requires a running operation")
            if int(row["runtime_generation"]) != runtime_generation:
                raise StaleRuntimeGeneration(
                    "checkpoint operation belongs to a stale runtime generation"
                )
            existing = self._connection.execute(
                "SELECT manifest_json FROM workspace_checkpoint_catalog WHERE manifest_digest = ?",
                (manifest.content_digest,),
            ).fetchone()
            if existing is not None and str(existing["manifest_json"]) != manifest_json:
                raise IdempotencyConflict("checkpoint digest is bound to different bytes")
            revision = int(row["record_revision"]) + 1
            now = self._now_ms()
            self._connection.execute(
                """
                UPDATE operations
                SET state = ?, certainty = ?, record_revision = ?,
                    reconciliation_required = 0, result_json = ?, failure_json = NULL,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND state = ?
                """,
                (
                    OperationState.SUCCEEDED.value,
                    OutcomeCertainty.CERTAIN.value,
                    revision,
                    manifest_json,
                    now,
                    operation.value,
                    OperationState.RUNNING.value,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise InvalidTransition("checkpoint operation changed during publication")
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO workspace_checkpoint_catalog(
                        manifest_digest, workspace_id, source_generation, source_revision,
                        backend_capability_digest, environment_digest, creation_operation_id,
                        manifest_json, created_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        manifest.content_digest,
                        handle.workspace.value,
                        handle.generation,
                        handle.revision,
                        handle.backend.capability_digest,
                        manifest.environment.digest,
                        operation.value,
                        manifest_json,
                        now,
                    ),
                )
            self._insert_event(
                operation.value,
                OperationState.SUCCEEDED,
                OutcomeCertainty.CERTAIN,
                revision,
                now,
                "succeeded",
            )
            return self.get(operation)

    def record_workspace_checkpoint(
        self,
        manifest: WorkspaceCheckpointManifest,
    ) -> WorkspaceCheckpointManifest:
        """Catalog one authoritative completed checkpoint manifest idempotently."""

        manifest = WorkspaceCheckpointManifest.model_validate_json(
            canonical_json_bytes(manifest), strict=True
        )
        manifest_json = canonical_json_bytes(manifest).decode()
        handle = manifest.source_handle
        with self._lock, self._connection:
            operation_row = self._connection.execute(
                "SELECT state, result_json FROM operations WHERE operation_id = ?",
                (manifest.creation_operation.value,),
            ).fetchone()
            if operation_row is None:
                raise KeyError(manifest.creation_operation.value)
            if OperationState(operation_row["state"]) is not OperationState.SUCCEEDED:
                raise InvalidTransition("checkpoint creation operation is not succeeded")
            if operation_row["result_json"] != manifest_json:
                raise IdempotencyConflict(
                    "checkpoint manifest does not match the authoritative operation result"
                )
            existing = self._connection.execute(
                "SELECT manifest_json FROM workspace_checkpoint_catalog WHERE manifest_digest = ?",
                (manifest.content_digest,),
            ).fetchone()
            if existing is not None:
                if str(existing["manifest_json"]) != manifest_json:
                    raise IdempotencyConflict("checkpoint digest is bound to different bytes")
                return manifest
            self._connection.execute(
                """
                INSERT INTO workspace_checkpoint_catalog(
                    manifest_digest, workspace_id, source_generation, source_revision,
                    backend_capability_digest, environment_digest, creation_operation_id,
                    manifest_json, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    manifest.content_digest,
                    handle.workspace.value,
                    handle.generation,
                    handle.revision,
                    handle.backend.capability_digest,
                    manifest.environment.digest,
                    manifest.creation_operation.value,
                    manifest_json,
                    self._now_ms(),
                ),
            )
            return manifest

    def workspace_checkpoint_manifest(
        self,
        manifest_digest: str,
    ) -> WorkspaceCheckpointManifest | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT manifest_json FROM workspace_checkpoint_catalog WHERE manifest_digest = ?",
                (manifest_digest,),
            ).fetchone()
            if row is None:
                return None
            return WorkspaceCheckpointManifest.model_validate_json(
                str(row["manifest_json"]), strict=True
            )

    def latest_workspace_checkpoint(
        self,
        handle: ProgrammableWorkspaceHandle,
    ) -> WorkspaceCheckpointManifest | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT manifest_json FROM workspace_checkpoint_catalog
                WHERE workspace_id = ? AND source_generation = ? AND source_revision = ?
                  AND backend_capability_digest = ?
                ORDER BY created_at_unix_ms DESC, manifest_digest DESC
                LIMIT 1
                """,
                (
                    handle.workspace.value,
                    handle.generation,
                    handle.revision,
                    handle.backend.capability_digest,
                ),
            ).fetchone()
            if row is None:
                return None
            return WorkspaceCheckpointManifest.model_validate_json(
                str(row["manifest_json"]), strict=True
            )

    def select_workspace_checkpoint(
        self,
        selection: OperationWorkspaceCheckpointSelectionV1,
        runtime_generation: int,
    ) -> OperationWorkspaceCheckpointSelectionV1:
        """Persist one exact checkpoint choice before restore begins."""

        selection = OperationWorkspaceCheckpointSelectionV1.model_validate_json(
            canonical_json_bytes(selection), strict=True
        )
        operation = selection.operation
        attempt_no = selection.prior_attempt.attempt_no
        selection_json = canonical_json_bytes(selection).decode()
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            operation_row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if operation_row is None:
                raise KeyError(operation.value)
            if OperationState(operation_row["state"]) is not OperationState.INDETERMINATE:
                raise InvalidTransition("checkpoint selection requires an indeterminate operation")
            if str(operation_row["input_digest"]) != selection.input_digest:
                raise IdempotencyConflict("checkpoint selection input digest is stale")
            attempt_row = self._connection.execute(
                """
                SELECT * FROM operation_attempts
                WHERE operation_id = ? AND attempt_no = ?
                """,
                (operation.value, attempt_no),
            ).fetchone()
            if (
                attempt_row is None
                or str(attempt_row["attempt_id"]) != selection.prior_attempt.attempt_id
                or int(attempt_row["runtime_generation"]) != selection.runtime_generation
                or int(attempt_row["dispatcher_generation"])
                != selection.dispatcher_generation
            ):
                raise StaleAttemptFence("checkpoint selection attempt fence is stale")
            dispatch = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if (
                dispatch is None
                or dispatch["current_attempt_no"] is None
                or int(dispatch["current_attempt_no"]) != attempt_no
                or str(dispatch["state"]) not in {_DISPATCH_RUNNING, _DISPATCH_PARKED}
            ):
                raise StaleAttemptFence("checkpoint selection dispatch fence is stale")
            lease = self._connection.execute(
                """
                SELECT lease_epoch FROM operation_leases
                WHERE operation_id = ? AND attempt_no = ?
                ORDER BY lease_epoch DESC LIMIT 1
                """,
                (operation.value, attempt_no),
            ).fetchone()
            if lease is None or int(lease["lease_epoch"]) != selection.lease_epoch:
                raise StaleAttemptFence("checkpoint selection lease epoch is stale")
            policy_row = self._connection.execute(
                "SELECT * FROM operation_recovery_policies WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if policy_row is None:
                raise InvalidTransition("checkpoint selection requires a recovery policy")
            policy = self._recovery_policy_binding(operation, policy_row)
            if (
                policy.policy_digest != selection.policy_digest
                or policy.environment_digest != selection.environment_digest
            ):
                raise IdempotencyConflict("checkpoint selection policy binding is stale")
            manifest = self.workspace_checkpoint_manifest(
                selection.checkpoint_manifest_digest
            )
            if manifest is None:
                raise InvalidTransition("selected checkpoint is not in the durable catalog")
            if (
                manifest.creation_operation != selection.checkpoint_operation
                or manifest.source_handle != selection.source_handle
                or len(manifest.exclusions) != selection.exclusion_count
                or canonical_sha256(manifest.exclusions) != selection.exclusions_digest
                or canonical_sha256(manifest.artifacts) != selection.artifacts_digest
            ):
                raise IdempotencyConflict("selected checkpoint manifest binding is inconsistent")
            control = self._connection.execute(
                "SELECT cancellation_requested FROM operation_controls WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if control is not None and bool(control["cancellation_requested"]):
                raise InvalidTransition("cancellation prevents checkpoint selection")
            now = self._now_ms()
            if now >= selection.deadline_unix_ms:
                raise InvalidTransition("deadline prevents checkpoint selection")
            decision = self._connection.execute(
                """
                SELECT 1 FROM operation_recovery_decisions
                WHERE operation_id = ? AND prior_attempt_no = ? LIMIT 1
                """,
                (operation.value, attempt_no),
            ).fetchone()
            if decision is not None:
                raise InvalidTransition("predecessor attempt already has a recovery decision")
            existing = self._connection.execute(
                """
                SELECT * FROM operation_workspace_checkpoint_selections
                WHERE operation_id = ? AND prior_attempt_no = ?
                """,
                (operation.value, attempt_no),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["selection_digest"]) != selection.selection_digest
                    or str(existing["selection_json"]) != selection_json
                ):
                    raise IdempotencyConflict(
                        "predecessor attempt already selected a different checkpoint"
                    )
                return selection
            self._connection.execute(
                """
                INSERT INTO operation_workspace_checkpoint_selections(
                    operation_id, prior_attempt_no, selection_digest, selection_json,
                    state, restored_handle_json, selected_at_unix_ms, restored_at_unix_ms
                ) VALUES (?, ?, ?, ?, 'selected', NULL, ?, NULL)
                """,
                (
                    operation.value,
                    attempt_no,
                    selection.selection_digest,
                    selection_json,
                    selection.selected_at_unix_ms,
                ),
            )
            self._insert_event(
                operation.value,
                OperationState.INDETERMINATE,
                OutcomeCertainty.INDETERMINATE,
                int(operation_row["record_revision"]),
                now,
                "checkpoint_selected",
                attempt_no=attempt_no,
                event_kind="checkpoint_selected",
                payload={
                    "checkpoint_manifest_digest": selection.checkpoint_manifest_digest,
                    "selection_digest": selection.selection_digest,
                },
            )
            return selection

    def workspace_checkpoint_selection(
        self,
        operation: OperationRef,
        prior_attempt_no: int,
    ) -> OperationWorkspaceCheckpointSelectionV1 | None:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT selection_json FROM operation_workspace_checkpoint_selections
                WHERE operation_id = ? AND prior_attempt_no = ?
                """,
                (operation.value, prior_attempt_no),
            ).fetchone()
            if row is None:
                return None
            return OperationWorkspaceCheckpointSelectionV1.model_validate_json(
                str(row["selection_json"]), strict=True
            )

    def mark_workspace_checkpoint_restored(
        self,
        selection: OperationWorkspaceCheckpointSelectionV1,
        restored_handle: ProgrammableWorkspaceHandle,
        runtime_generation: int,
    ) -> ProgrammableWorkspaceHandle:
        """Persist verified restore completion before successor admission."""

        selection = OperationWorkspaceCheckpointSelectionV1.model_validate_json(
            canonical_json_bytes(selection), strict=True
        )
        if (
            restored_handle.workspace != selection.source_handle.workspace
            or restored_handle.backend != selection.source_handle.backend
            or restored_handle.generation != selection.source_handle.generation + 1
            or restored_handle.revision != 0
        ):
            raise IdempotencyConflict("restored handle does not match checkpoint selection")
        restored_json = canonical_json_bytes(restored_handle).decode()
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            operation_row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (selection.operation.value,),
            ).fetchone()
            if operation_row is None:
                raise KeyError(selection.operation.value)
            if OperationState(operation_row["state"]) is not OperationState.INDETERMINATE:
                raise InvalidTransition("restore binding requires an indeterminate operation")
            row = self._connection.execute(
                """
                SELECT * FROM operation_workspace_checkpoint_selections
                WHERE operation_id = ? AND prior_attempt_no = ?
                """,
                (selection.operation.value, selection.prior_attempt.attempt_no),
            ).fetchone()
            if row is None:
                raise InvalidTransition("checkpoint restore requires a persisted selection")
            if (
                str(row["selection_digest"]) != selection.selection_digest
                or str(row["selection_json"]) != canonical_json_bytes(selection).decode()
            ):
                raise IdempotencyConflict("checkpoint restore selection is inconsistent")
            if str(row["state"]) == "restored":
                if str(row["restored_handle_json"]) != restored_json:
                    raise IdempotencyConflict("checkpoint selection restored a different handle")
                return restored_handle
            now = self._now_ms()
            self._connection.execute(
                """
                UPDATE operation_workspace_checkpoint_selections
                SET state = 'restored', restored_handle_json = ?, restored_at_unix_ms = ?
                WHERE operation_id = ? AND prior_attempt_no = ? AND state = 'selected'
                """,
                (
                    restored_json,
                    now,
                    selection.operation.value,
                    selection.prior_attempt.attempt_no,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise StaleAttemptFence("checkpoint selection changed during restore binding")
            self._insert_event(
                selection.operation.value,
                OperationState.INDETERMINATE,
                OutcomeCertainty.INDETERMINATE,
                int(operation_row["record_revision"]),
                now,
                "checkpoint_restored",
                attempt_no=selection.prior_attempt.attempt_no,
                event_kind="checkpoint_restored",
                payload={
                    "restored_generation": restored_handle.generation,
                    "selection_digest": selection.selection_digest,
                },
            )
            return restored_handle

    def workspace_checkpoint_boundaries(
        self,
        operation: OperationRef,
    ) -> tuple[OperationWorkspaceCheckpointBoundaryV1, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT boundary_json FROM operation_workspace_boundaries
                WHERE operation_id = ? ORDER BY prior_attempt_no
                """,
                (operation.value,),
            ).fetchall()
            return tuple(
                OperationWorkspaceCheckpointBoundaryV1.model_validate_json(
                    str(row["boundary_json"]), strict=True
                )
                for row in rows
            )

    def get(self, operation: OperationRef) -> OperationRecord:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            return self._record(row)

    def events(self, operation: OperationRef) -> tuple[OperationEvent, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM operation_events WHERE operation_id = ? ORDER BY sequence",
                (operation.value,),
            ).fetchall()
            return tuple(
                OperationEvent(
                    sequence=int(row["sequence"]),
                    operation=operation,
                    state=OperationState(row["state"]),
                    certainty=OutcomeCertainty(row["certainty"]),
                    record_revision=int(row["record_revision"]),
                    at_unix_ms=int(row["at_unix_ms"]),
                    note=str(row["note"]),
                )
                for row in rows
            )

    def begin(self, operation: OperationRef, runtime_generation: int) -> OperationRecord:
        return self._transition(
            operation,
            expected_states={OperationState.ACCEPTED},
            state=OperationState.RUNNING,
            certainty=OutcomeCertainty.CERTAIN,
            reconciliation_required=False,
            runtime_generation=runtime_generation,
            note="execution_started",
        )

    def succeed(
        self,
        operation: OperationRef,
        result_json: str,
        runtime_generation: int,
        *,
        reconciled: bool = False,
    ) -> OperationRecord:
        expected = {OperationState.INDETERMINATE} if reconciled else {OperationState.RUNNING}
        return self._transition(
            operation,
            expected_states=expected,
            state=OperationState.SUCCEEDED,
            certainty=OutcomeCertainty.CERTAIN,
            reconciliation_required=False,
            runtime_generation=runtime_generation,
            result_json=result_json,
            note="reconciled_success" if reconciled else "execution_succeeded",
            allow_generation_change=reconciled,
        )

    def fail(
        self,
        operation: OperationRef,
        failure: FailureEnvelope,
        runtime_generation: int,
        *,
        reconciled: bool = False,
        result_json: str | None = None,
    ) -> OperationRecord:
        expected = (
            {OperationState.INDETERMINATE}
            if reconciled
            else {OperationState.ACCEPTED, OperationState.RUNNING}
        )
        return self._transition(
            operation,
            expected_states=expected,
            state=OperationState.FAILED,
            certainty=OutcomeCertainty.CERTAIN,
            reconciliation_required=False,
            runtime_generation=runtime_generation,
            failure=failure,
            result_json=result_json,
            note="reconciled_failure" if reconciled else "execution_failed",
            allow_generation_change=reconciled,
        )

    def cancel(
        self,
        operation: OperationRef,
        runtime_generation: int,
        *,
        result_json: str | None = None,
    ) -> OperationRecord:
        return self._transition(
            operation,
            expected_states={OperationState.ACCEPTED, OperationState.RUNNING},
            state=OperationState.CANCELLED,
            certainty=OutcomeCertainty.CERTAIN,
            reconciliation_required=False,
            runtime_generation=runtime_generation,
            result_json=result_json,
            note="cancelled",
        )

    def time_out(
        self,
        operation: OperationRef,
        runtime_generation: int,
        *,
        result_json: str | None = None,
    ) -> OperationRecord:
        return self._transition(
            operation,
            expected_states={OperationState.ACCEPTED, OperationState.RUNNING},
            state=OperationState.TIMED_OUT,
            certainty=OutcomeCertainty.CERTAIN,
            reconciliation_required=False,
            runtime_generation=runtime_generation,
            result_json=result_json,
            note="deadline_expired",
        )

    def mark_indeterminate(
        self, operation: OperationRef, runtime_generation: int, note: str
    ) -> OperationRecord:
        return self._transition(
            operation,
            expected_states={OperationState.RUNNING},
            state=OperationState.INDETERMINATE,
            certainty=OutcomeCertainty.INDETERMINATE,
            reconciliation_required=True,
            runtime_generation=runtime_generation,
            note=note,
        )

    def _transition(
        self,
        operation: OperationRef,
        *,
        expected_states: set[OperationState],
        state: OperationState,
        certainty: OutcomeCertainty,
        reconciliation_required: bool,
        runtime_generation: int,
        note: str,
        result_json: str | None = None,
        failure: FailureEnvelope | None = None,
        allow_generation_change: bool = False,
    ) -> OperationRecord:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            current = OperationState(row["state"])
            if current not in expected_states:
                raise InvalidTransition(f"cannot transition {current.value} to {state.value}")
            stored_generation = int(row["runtime_generation"])
            if not allow_generation_change and runtime_generation != stored_generation:
                raise StaleRuntimeGeneration(
                    "operation belongs to runtime generation "
                    f"{stored_generation}, not {runtime_generation}"
                )
            if runtime_generation != self._current_runtime_generation_unlocked():
                raise StaleRuntimeGeneration("runtime generation is no longer current")
            revision = int(row["record_revision"]) + 1
            now = self._now_ms()
            failure_json = row["failure_json"]
            if failure is not None:
                failure_json = canonical_json_bytes(failure).decode()
            self._connection.execute(
                """
                UPDATE operations
                SET state = ?, certainty = ?, runtime_generation = ?, record_revision = ?,
                    reconciliation_required = ?, result_json = ?, failure_json = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ?
                """,
                (
                    state.value,
                    certainty.value,
                    runtime_generation,
                    revision,
                    int(reconciliation_required),
                    result_json if result_json is not None else row["result_json"],
                    failure_json,
                    now,
                    operation.value,
                ),
            )
            self._insert_event(
                operation.value,
                state,
                certainty,
                revision,
                now,
                note,
            )
            return self.get(operation)

    def request_dispatch(
        self, operation: OperationRef, kind: str = "rlm.execute"
    ) -> Any:
        """Durably enqueue an accepted operation without claiming it."""

        if kind not in _DURABLE_DISPATCH_KINDS:
            raise ValueError(f"unsupported durable dispatch kind: {kind}")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            state = OperationState(row["state"])
            existing = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if existing is not None and str(existing["kind"]) != kind:
                raise IdempotencyConflict("operation is already bound to another dispatch kind")
            now = self._now_ms()
            if state is not OperationState.ACCEPTED:
                if existing is not None:
                    return self._dispatch_model(operation, existing)
                raise InvalidTransition(f"cannot dispatch {state.value} operation")
            if existing is not None and str(existing["state"]) == _DISPATCH_QUEUED:
                return self._dispatch_model(operation, existing)
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO operation_dispatch(
                        operation_id, kind, state, queued_at_unix_ms,
                        running_at_unix_ms, finished_at_unix_ms, current_attempt_no
                    ) VALUES (?, ?, ?, ?, NULL, NULL, NULL)
                    """,
                    (operation.value, kind, _DISPATCH_QUEUED, now),
                )
            elif str(existing["state"]) != _DISPATCH_QUEUED:
                self._connection.execute(
                    """
                    UPDATE operation_dispatch
                    SET state = ?, queued_at_unix_ms = ?, running_at_unix_ms = NULL,
                        finished_at_unix_ms = NULL
                    WHERE operation_id = ?
                    """,
                    (_DISPATCH_QUEUED, now, operation.value),
                )
            self._insert_event(
                operation.value,
                state,
                OutcomeCertainty(row["certainty"]),
                int(row["record_revision"]),
                now,
                "dispatch_requested",
                event_kind="dispatch_requested",
                payload={"kind": kind},
            )
            current = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            assert current is not None
            return self._dispatch_model(operation, current)

    def claim_next(
        self,
        runtime_generation: int,
        dispatcher_generation: int,
        owner_digest: str,
        lease_duration_ms: int,
    ) -> Any | None:
        """Atomically claim the oldest queued accepted operation or return ``None``."""

        if lease_duration_ms <= 0:
            raise ValueError("lease_duration_ms must be positive")
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            now = self._now_ms()
            queued = self._connection.execute(
                """
                SELECT d.operation_id AS dispatch_operation_id,
                       d.kind AS dispatch_kind,
                       d.state AS dispatch_state,
                       d.queued_at_unix_ms,
                       d.running_at_unix_ms,
                       d.finished_at_unix_ms,
                       d.current_attempt_no,
                       o.*
                FROM operation_dispatch AS d
                JOIN operations AS o ON o.operation_id = d.operation_id
                WHERE d.state = ?
                ORDER BY d.queued_at_unix_ms, d.operation_id
                """,
                (_DISPATCH_QUEUED,),
            ).fetchall()
            for candidate in queued:
                operation_id = str(candidate["operation_id"])
                state = OperationState(candidate["state"])
                if state is not OperationState.ACCEPTED:
                    self._connection.execute(
                        "UPDATE operation_dispatch SET state = ? WHERE operation_id = ?",
                        (_DISPATCH_PARKED, operation_id),
                    )
                    continue
                deadline = self._request_deadline(candidate["request_json"])
                if now >= deadline:
                    self._expire_queued_unlocked(candidate, now)
                    continue

                previous_attempt = self._connection.execute(
                    "SELECT COALESCE(MAX(attempt_no), 0) AS value FROM operation_attempts "
                    "WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                attempt_no = int(previous_attempt["value"]) + 1
                attempt_id = self._attempt_id(
                    operation_id, attempt_no, runtime_generation, dispatcher_generation
                )
                previous_epoch = self._connection.execute(
                    "SELECT COALESCE(MAX(lease_epoch), 0) AS value FROM operation_leases "
                    "WHERE operation_id = ?",
                    (operation_id,),
                ).fetchone()
                lease_epoch = int(previous_epoch["value"]) + 1
                expires = now + lease_duration_ms
                self._connection.execute(
                    """
                    INSERT INTO operation_attempts(
                        operation_id, attempt_no, attempt_id, runtime_generation,
                        dispatcher_generation, state, certainty, recovery_reason,
                        created_at_unix_ms, started_at_unix_ms, ended_at_unix_ms,
                        checkpoint_digest
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, NULL, NULL)
                    """,
                    (
                        operation_id,
                        attempt_no,
                        attempt_id,
                        runtime_generation,
                        dispatcher_generation,
                        OperationState.RUNNING.value,
                        OutcomeCertainty.CERTAIN.value,
                        now,
                        now,
                    ),
                )
                self._connection.execute(
                    """
                    INSERT INTO operation_leases(
                        operation_id, attempt_no, lease_epoch, owner_digest,
                        acquired_at_unix_ms, heartbeat_at_unix_ms, expires_at_unix_ms,
                        released_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
                    """,
                    (
                        operation_id,
                        attempt_no,
                        lease_epoch,
                        str(owner_digest),
                        now,
                        now,
                        expires,
                    ),
                )
                revision = int(candidate["record_revision"]) + 1
                self._connection.execute(
                    """
                    UPDATE operations
                    SET state = ?, certainty = ?, runtime_generation = ?,
                        record_revision = ?, reconciliation_required = 0,
                        updated_at_unix_ms = ?
                    WHERE operation_id = ? AND state = ?
                    """,
                    (
                        OperationState.RUNNING.value,
                        OutcomeCertainty.CERTAIN.value,
                        runtime_generation,
                        revision,
                        now,
                        operation_id,
                        OperationState.ACCEPTED.value,
                    ),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise StaleRuntimeGeneration("operation changed during claim")
                self._connection.execute(
                    """
                    UPDATE operation_dispatch
                    SET state = ?, running_at_unix_ms = ?, current_attempt_no = ?
                    WHERE operation_id = ? AND state = ?
                    """,
                    (_DISPATCH_RUNNING, now, attempt_no, operation_id, _DISPATCH_QUEUED),
                )
                if (
                    str(candidate["dispatch_kind"]) == "workspace.program.execute"
                    and attempt_no > 1
                ):
                    boundary = self._connection.execute(
                        """
                        SELECT boundary_digest FROM operation_workspace_boundaries
                        WHERE operation_id = ? AND prior_attempt_no = ?
                        """,
                        (operation_id, attempt_no - 1),
                    ).fetchone()
                    if boundary is None:
                        raise InvalidTransition(
                            "workspace successor claim requires a restored boundary"
                        )
                    self._insert_event(
                        operation_id,
                        OperationState.RUNNING,
                        OutcomeCertainty.CERTAIN,
                        revision,
                        now,
                        "successor_started",
                        attempt_no=attempt_no,
                        event_kind="successor_started",
                        payload={
                            "boundary_digest": str(boundary["boundary_digest"]),
                            "successor_attempt_no": attempt_no,
                        },
                    )
                self._insert_event(
                    operation_id,
                    OperationState.RUNNING,
                    OutcomeCertainty.CERTAIN,
                    revision,
                    now,
                    "attempt_claimed",
                    attempt_no=attempt_no,
                    event_kind="attempt_claimed",
                    payload={
                        "attempt_id": attempt_id,
                        "attempt_no": attempt_no,
                        "dispatcher_generation": dispatcher_generation,
                        "lease_epoch": lease_epoch,
                        "runtime_generation": runtime_generation,
                    },
                )
                attempt_ref = OperationAttemptRefV1(
                    operation=OperationRef(value=operation_id),
                    attempt_no=attempt_no,
                    attempt_id=attempt_id,
                )
                return OperationDispatchClaim(
                    attempt=attempt_ref,
                    runtime_generation=runtime_generation,
                    dispatcher_generation=dispatcher_generation,
                    lease_epoch=lease_epoch,
                    owner_digest=str(owner_digest),
                    kind=str(candidate["dispatch_kind"]),
                )
            return None

    def heartbeat(
        self,
        attempt_ref: Any,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        lease_duration_ms: int,
    ) -> Any:
        """Extend a live lease only when every attempt fence matches exactly."""

        if lease_duration_ms <= 0:
            raise ValueError("lease_duration_ms must be positive")
        with self._lock, self._connection:
            operation, attempt_no, attempt_id = _attempt_identity(attempt_ref)
            attempt, _lease = self._validate_fence_unlocked(
                operation,
                attempt_no,
                attempt_id,
                runtime_generation,
                dispatcher_generation,
                lease_epoch,
                owner_digest,
            )
            now = self._now_ms()
            expires = now + lease_duration_ms
            self._connection.execute(
                """
                UPDATE operation_leases
                SET heartbeat_at_unix_ms = ?, expires_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
                """,
                (now, expires, operation.value, attempt_no, lease_epoch),
            )
            updated = self._connection.execute(
                """
                SELECT * FROM operation_leases
                WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
                """,
                (operation.value, attempt_no, lease_epoch),
            ).fetchone()
            assert updated is not None
            return self._lease_model(operation, updated, attempt)

    def request_cancel(
        self,
        operation: OperationRef,
        requested_by_digest: str,
        reason_code: str,
    ) -> Any:
        """Persist cancellation intent without changing the logical operation state."""

        if not reason_code:
            raise ValueError("reason_code must not be empty")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            control = self._connection.execute(
                "SELECT * FROM operation_controls WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            now = self._now_ms()
            if control is not None and bool(control["cancellation_requested"]):
                return self._control_model(operation, control)
            revision = 1 if control is None else int(control["control_revision"]) + 1
            self._connection.execute(
                """
                INSERT INTO operation_controls(
                    operation_id, control_revision, cancellation_requested,
                    requested_at_unix_ms, requested_by_digest, reason_code
                ) VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    control_revision = excluded.control_revision,
                    cancellation_requested = 1,
                    requested_at_unix_ms = excluded.requested_at_unix_ms,
                    requested_by_digest = excluded.requested_by_digest,
                    reason_code = excluded.reason_code
                """,
                (operation.value, revision, now, str(requested_by_digest), reason_code),
            )
            self._insert_event(
                operation.value,
                OperationState(row["state"]),
                OutcomeCertainty(row["certainty"]),
                int(row["record_revision"]),
                now,
                "cancel_requested",
                event_kind="cancel_requested",
                payload={
                    "control_revision": revision,
                    "reason_code": reason_code,
                    "requested_by_digest": str(requested_by_digest),
                },
            )
            current = self._connection.execute(
                "SELECT * FROM operation_controls WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            assert current is not None
            return self._control_model(operation, current)

    def cancellation_requested(self, operation: OperationRef) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT cancellation_requested FROM operation_controls WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            return bool(row and row["cancellation_requested"])

    def cancel_queued_dispatch(
        self,
        operation: OperationRef,
        runtime_generation: int,
        *,
        result_json: str | None = None,
    ) -> tuple[OperationRecord, bool]:
        """Cancel only if durable work is still queued and has no attempt owner."""

        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            dispatch = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if (
                OperationState(row["state"]) is not OperationState.ACCEPTED
                or dispatch is None
                or str(dispatch["state"]) != _DISPATCH_QUEUED
                or dispatch["current_attempt_no"] is not None
            ):
                return self._record(row), False
            revision = int(row["record_revision"]) + 1
            now = self._now_ms()
            self._connection.execute(
                """
                UPDATE operations
                SET state = ?, certainty = ?, record_revision = ?,
                    reconciliation_required = 0, result_json = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND state = ?
                """,
                (
                    OperationState.CANCELLED.value,
                    OutcomeCertainty.CERTAIN.value,
                    revision,
                    result_json,
                    now,
                    operation.value,
                    OperationState.ACCEPTED.value,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                return self.get(operation), False
            self._connection.execute(
                """
                UPDATE operation_dispatch
                SET state = ?, finished_at_unix_ms = ?
                WHERE operation_id = ? AND state = ? AND current_attempt_no IS NULL
                """,
                (
                    _DISPATCH_COMPLETED,
                    now,
                    operation.value,
                    _DISPATCH_QUEUED,
                ),
            )
            self._insert_event(
                operation.value,
                OperationState.CANCELLED,
                OutcomeCertainty.CERTAIN,
                revision,
                now,
                "cancelled_before_claim",
                event_kind="cancelled_before_claim",
                payload={"state": OperationState.CANCELLED.value},
            )
            return self.get(operation), True

    def transition_claimed(
        self,
        attempt_ref: OperationAttemptRefV1,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        *,
        state: OperationState,
        result_json: str | None = None,
        failure: FailureEnvelope | None = None,
        note: str,
    ) -> OperationRecord:
        """Atomically commit a fenced attempt outcome and release its ownership."""

        if state not in TERMINAL_STATES and state is not OperationState.INDETERMINATE:
            raise ValueError("claimed outcome must be terminal or indeterminate")
        certainty = (
            OutcomeCertainty.INDETERMINATE
            if state is OperationState.INDETERMINATE
            else OutcomeCertainty.CERTAIN
        )
        with self._lock, self._connection:
            operation, attempt_no, attempt_id = _attempt_identity(attempt_ref)
            self._validate_fence_unlocked(
                operation,
                attempt_no,
                attempt_id,
                runtime_generation,
                dispatcher_generation,
                lease_epoch,
                owner_digest,
            )
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            assert row is not None
            if OperationState(row["state"]) is not OperationState.RUNNING:
                raise StaleAttemptFence("outer operation is no longer running")
            revision = int(row["record_revision"]) + 1
            now = self._now_ms()
            failure_json = (
                None if failure is None else canonical_json_bytes(failure).decode()
            )
            self._connection.execute(
                """
                UPDATE operations
                SET state = ?, certainty = ?, record_revision = ?,
                    reconciliation_required = ?, result_json = ?, failure_json = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND state = ? AND runtime_generation = ?
                """,
                (
                    state.value,
                    certainty.value,
                    revision,
                    int(state is OperationState.INDETERMINATE),
                    result_json,
                    failure_json,
                    now,
                    operation.value,
                    OperationState.RUNNING.value,
                    runtime_generation,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise StaleAttemptFence("outer operation changed during claimed commit")
            self._connection.execute(
                """
                UPDATE operation_attempts
                SET state = ?, certainty = ?, recovery_reason = ?, ended_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_no = ? AND attempt_id = ?
                    AND state = ?
                """,
                (
                    state.value,
                    certainty.value,
                    note if state is OperationState.INDETERMINATE else None,
                    now,
                    operation.value,
                    attempt_no,
                    attempt_id,
                    OperationState.RUNNING.value,
                ),
            )
            self._connection.execute(
                """
                UPDATE operation_leases
                SET released_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
                    AND released_at_unix_ms IS NULL
                """,
                (now, operation.value, attempt_no, lease_epoch),
            )
            dispatch_state = (
                _DISPATCH_PARKED
                if state is OperationState.INDETERMINATE
                else _DISPATCH_COMPLETED
            )
            self._connection.execute(
                """
                UPDATE operation_dispatch
                SET state = ?, finished_at_unix_ms = ?
                WHERE operation_id = ? AND state = ? AND current_attempt_no = ?
                """,
                (
                    dispatch_state,
                    now,
                    operation.value,
                    _DISPATCH_RUNNING,
                    attempt_no,
                ),
            )
            self._insert_event(
                operation.value,
                state,
                certainty,
                revision,
                now,
                note,
                attempt_no=attempt_no,
                event_kind=note,
                payload={"attempt_no": attempt_no, "state": state.value},
            )
            return self._record_from_operation_id(operation)

    def bind_checkpoint(
        self,
        attempt_ref: OperationAttemptRefV1,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        checkpoint: ArtifactReference,
        environment_digest: str,
    ) -> OperationCheckpointBindingV1:
        """Bind one portable artifact checkpoint to the exact live attempt/environment."""

        if checkpoint.redacted:
            raise ValueError("redacted artifacts cannot be continuity checkpoints")
        with self._lock, self._connection:
            operation, attempt_no, attempt_id = _attempt_identity(attempt_ref)
            self._validate_fence_unlocked(
                operation,
                attempt_no,
                attempt_id,
                runtime_generation,
                dispatcher_generation,
                lease_epoch,
                owner_digest,
            )
            outer = self._connection.execute(
                "SELECT state, certainty, record_revision FROM operations "
                "WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            assert outer is not None
            now = self._now_ms()
            existing = self._connection.execute(
                """
                SELECT * FROM operation_checkpoints
                WHERE operation_id = ? AND attempt_no = ? AND checkpoint_digest = ?
                """,
                (operation.value, attempt_no, checkpoint.digest),
            ).fetchone()
            values = (
                environment_digest,
                checkpoint.artifact.value,
                checkpoint.media_type,
                checkpoint.size_bytes,
                checkpoint.created_by.value,
            )
            if existing is not None:
                stored = (
                    str(existing["checkpoint_kind"]),
                    str(existing["artifact_id"]),
                    str(existing["media_type"]),
                    int(existing["size_bytes"]),
                    str(existing["created_by_operation_id"]),
                )
                if stored != values:
                    raise IdempotencyConflict(
                        "checkpoint digest already binds different artifact metadata"
                    )
            else:
                self._connection.execute(
                    """
                    INSERT INTO operation_checkpoints(
                        operation_id, attempt_no, checkpoint_digest, checkpoint_kind,
                        artifact_id, media_type, size_bytes, created_by_operation_id,
                        created_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation.value,
                        attempt_no,
                        checkpoint.digest,
                        *values,
                        now,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE operation_attempts SET checkpoint_digest = ?
                    WHERE operation_id = ? AND attempt_no = ? AND attempt_id = ?
                    """,
                    (checkpoint.digest, operation.value, attempt_no, attempt_id),
                )
                self._insert_event(
                    operation.value,
                    OperationState(outer["state"]),
                    OutcomeCertainty(outer["certainty"]),
                    int(outer["record_revision"]),
                    now,
                    "checkpoint_bound",
                    attempt_no=attempt_no,
                    event_kind="checkpoint_bound",
                    payload={"checkpoint_digest": checkpoint.digest},
                )
            return OperationCheckpointBindingV1(
                operation=operation,
                attempt=attempt_ref,
                checkpoint=checkpoint,
                environment_digest=environment_digest,
                created_at_unix_ms=(
                    int(existing["created_at_unix_ms"]) if existing is not None else now
                ),
            )

    def checkpoint_bindings(
        self,
        operation: OperationRef,
    ) -> tuple[OperationCheckpointBindingV1, ...]:
        """Read every checkpoint binding for one logical operation."""

        with self._lock:
            outer = self._connection.execute(
                "SELECT 1 FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if outer is None:
                raise KeyError(operation.value)
            rows = self._connection.execute(
                """
                SELECT * FROM operation_checkpoints
                WHERE operation_id = ? ORDER BY attempt_no, created_at_unix_ms
                """,
                (operation.value,),
            ).fetchall()
            bindings: list[OperationCheckpointBindingV1] = []
            for row in rows:
                attempt = self._attempt_ref_for_no(operation, int(row["attempt_no"]))
                assert attempt is not None
                checkpoint = ArtifactReference(
                    artifact=ArtifactIdRef(value=str(row["artifact_id"])),
                    digest=str(row["checkpoint_digest"]),
                    media_type=str(row["media_type"]),
                    size_bytes=int(row["size_bytes"]),
                    created_by=OperationRef(value=str(row["created_by_operation_id"])),
                    redacted=False,
                )
                bindings.append(
                    OperationCheckpointBindingV1(
                        operation=operation,
                        attempt=attempt,
                        checkpoint=checkpoint,
                        environment_digest=str(row["checkpoint_kind"]),
                        created_at_unix_ms=int(row["created_at_unix_ms"]),
                    )
                )
            return tuple(bindings)

    def park_attempt(
        self,
        attempt_ref: OperationAttemptRefV1,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        *,
        note: str = "dispatcher_worker_exception",
    ) -> OperationRecord:
        return self.transition_claimed(
            attempt_ref,
            runtime_generation,
            dispatcher_generation,
            lease_epoch,
            owner_digest,
            state=OperationState.INDETERMINATE,
            note=note,
        )

    def finish_attempt(
        self,
        attempt_ref: Any,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
    ) -> OperationRecord:
        """Close a fenced attempt after an outer terminal or indeterminate decision."""

        with self._lock, self._connection:
            operation, attempt_no, attempt_id = _attempt_identity(attempt_ref)
            stored_attempt = self._connection.execute(
                """
                SELECT * FROM operation_attempts
                WHERE operation_id = ? AND attempt_no = ? AND attempt_id = ?
                """,
                (operation.value, attempt_no, attempt_id),
            ).fetchone()
            if stored_attempt is None:
                raise StaleAttemptFence("attempt identity is not current")
            if str(stored_attempt["state"]) != OperationState.RUNNING.value:
                self._validate_closed_fence_unlocked(
                    operation,
                    stored_attempt,
                    runtime_generation,
                    dispatcher_generation,
                    lease_epoch,
                    owner_digest,
                )
                return self.get(operation)
            attempt, _lease = self._validate_fence_unlocked(
                operation,
                attempt_no,
                attempt_id,
                runtime_generation,
                dispatcher_generation,
                lease_epoch,
                owner_digest,
            )
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            assert row is not None
            outer_state = OperationState(row["state"])
            if (
                outer_state not in TERMINAL_STATES
                and outer_state is not OperationState.INDETERMINATE
            ):
                raise InvalidTransition(
                    "attempt can finish only after a terminal or indeterminate outer state"
                )
            now = self._now_ms()
            self._connection.execute(
                """
                UPDATE operation_attempts
                SET state = ?, certainty = ?, ended_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_no = ? AND attempt_id = ?
                """,
                (
                    outer_state.value,
                    OutcomeCertainty.INDETERMINATE.value
                    if outer_state is OperationState.INDETERMINATE
                    else OutcomeCertainty.CERTAIN.value,
                    now,
                    operation.value,
                    attempt_no,
                    attempt_id,
                ),
            )
            self._connection.execute(
                """
                UPDATE operation_leases
                SET released_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
                """,
                (now, operation.value, attempt_no, lease_epoch),
            )
            dispatch_state = (
                _DISPATCH_PARKED
                if outer_state is OperationState.INDETERMINATE
                else _DISPATCH_COMPLETED
            )
            self._connection.execute(
                """
                UPDATE operation_dispatch
                SET state = ?, finished_at_unix_ms = ?
                WHERE operation_id = ?
                """,
                (dispatch_state, now, operation.value),
            )
            del attempt
            return self.get(operation)

    def list_recovery_candidates(self) -> tuple[OperationRecord, ...]:
        """Return undecided indeterminate dispatch attempts that require reconciliation."""

        with self._lock:
            rows = self._connection.execute(
                """
                SELECT o.*
                FROM operations AS o
                JOIN operation_dispatch AS d ON d.operation_id = o.operation_id
                WHERE o.state = ? AND o.certainty = ? AND d.state IN (?, ?)
                  AND NOT EXISTS (
                      SELECT 1 FROM operation_recovery_decisions AS r
                      WHERE r.operation_id = o.operation_id
                        AND r.prior_attempt_no = d.current_attempt_no
                  )
                ORDER BY o.updated_at_unix_ms, o.operation_id
                """,
                (
                    OperationState.INDETERMINATE.value,
                    OutcomeCertainty.INDETERMINATE.value,
                    _DISPATCH_RUNNING,
                    _DISPATCH_PARKED,
                ),
            ).fetchall()
            return tuple(self._record(row) for row in rows)

    def recovery_decisions(
        self,
        operation: OperationRef,
    ) -> tuple[OperationRecoveryDecisionV1, ...]:
        """Read persisted recovery policy decisions in decision order."""

        with self._lock:
            if self._connection.execute(
                "SELECT 1 FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() is None:
                raise KeyError(operation.value)
            rows = self._connection.execute(
                """
                SELECT * FROM operation_recovery_decisions
                WHERE operation_id = ? ORDER BY decision_no
                """,
                (operation.value,),
            ).fetchall()
            return tuple(
                OperationRecoveryDecisionV1(
                    operation=operation,
                    decision_no=int(row["decision_no"]),
                    policy_version=(
                        1 if row["policy_version"] is None else int(row["policy_version"])
                    ),
                    prior_attempt=self._attempt_ref_for_no(
                        operation, row["prior_attempt_no"]
                    ),
                    decision=cast(Any, str(row["decision"])),
                    reason_code=str(row["reason_code"]),
                    input_digest=str(row["input_digest"]),
                    policy_digest=row["policy_digest"],
                    checkpoint_digest=row["checkpoint_digest"],
                    effect_receipt_digest=row["effect_receipt_digest"],
                    continuation_boundary_digest=row[
                        "continuation_boundary_digest"
                    ],
                    successor_attempt=self._attempt_ref_for_no(
                        operation, row["successor_attempt_no"]
                    ),
                    created_at_unix_ms=int(row["created_at_unix_ms"]),
                )
                for row in rows
            )

    def fence_expired_attempts(
        self,
        runtime_generation: int,
    ) -> tuple[OperationRecord, ...]:
        """Fence expired running attempts as indeterminate before recovery policy runs."""

        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            now = self._now_ms()
            rows = self._connection.execute(
                """
                SELECT o.*, d.current_attempt_no, l.lease_epoch
                FROM operations AS o
                JOIN operation_dispatch AS d ON d.operation_id = o.operation_id
                JOIN operation_attempts AS a
                  ON a.operation_id = o.operation_id
                 AND a.attempt_no = d.current_attempt_no
                JOIN operation_leases AS l
                  ON l.operation_id = a.operation_id
                 AND l.attempt_no = a.attempt_no
                WHERE o.state = ? AND d.state = ? AND a.state = ?
                  AND l.released_at_unix_ms IS NULL
                  AND l.expires_at_unix_ms <= ?
                ORDER BY l.expires_at_unix_ms, o.operation_id
                """,
                (
                    OperationState.RUNNING.value,
                    _DISPATCH_RUNNING,
                    OperationState.RUNNING.value,
                    now,
                ),
            ).fetchall()
            operations: list[OperationRecord] = []
            for row in rows:
                operation_id = str(row["operation_id"])
                attempt_no = int(row["current_attempt_no"])
                revision = int(row["record_revision"]) + 1
                self._connection.execute(
                    """
                    UPDATE operations
                    SET state = ?, certainty = ?, record_revision = ?,
                        reconciliation_required = 1, updated_at_unix_ms = ?
                    WHERE operation_id = ? AND state = ?
                    """,
                    (
                        OperationState.INDETERMINATE.value,
                        OutcomeCertainty.INDETERMINATE.value,
                        revision,
                        now,
                        operation_id,
                        OperationState.RUNNING.value,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE operation_attempts
                    SET state = ?, certainty = ?, recovery_reason = ?,
                        ended_at_unix_ms = ?
                    WHERE operation_id = ? AND attempt_no = ? AND state = ?
                    """,
                    (
                        OperationState.INDETERMINATE.value,
                        OutcomeCertainty.INDETERMINATE.value,
                        "lease_expired",
                        now,
                        operation_id,
                        attempt_no,
                        OperationState.RUNNING.value,
                    ),
                )
                self._connection.execute(
                    """
                    UPDATE operation_leases
                    SET released_at_unix_ms = ?
                    WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
                        AND released_at_unix_ms IS NULL
                    """,
                    (now, operation_id, attempt_no, int(row["lease_epoch"])),
                )
                self._insert_event(
                    operation_id,
                    OperationState.INDETERMINATE,
                    OutcomeCertainty.INDETERMINATE,
                    revision,
                    now,
                    "lease_expired",
                    attempt_no=attempt_no,
                    event_kind="lease_expired",
                    payload={
                        "attempt_no": attempt_no,
                        "lease_epoch": int(row["lease_epoch"]),
                    },
                )
                operations.append(
                    self._record_from_operation_id(OperationRef(value=operation_id))
                )
            return tuple(operations)

    def requeue_indeterminate(
        self,
        operation: OperationRef,
        runtime_generation: int,
        decision: Any = _RECOVERY_START_SUCCESSOR,
        reason_code: str = "explicit_recovery",
        input_digest: str = "",
        policy_binding: OperationRecoveryPolicyBindingV1 | None = None,
        continuation_boundary: (
            OperationRlmStepBoundaryV1 | OperationWorkspaceCheckpointBoundaryV1 | None
        ) = None,
    ) -> OperationRecord:
        """Persist a recovery decision and explicitly return an indeterminate op to the queue."""

        if not reason_code:
            raise ValueError("reason_code must not be empty")
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if OperationState(row["state"]) is not OperationState.INDETERMINATE:
                raise InvalidTransition("only indeterminate operations can be requeued")
            if input_digest and input_digest != str(row["input_digest"]):
                raise IdempotencyConflict("recovery input digest does not match accepted intent")
            bound_input_digest = input_digest or str(row["input_digest"])
            dispatch = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            prior_attempt_no = None if dispatch is None else dispatch["current_attempt_no"]
            if prior_attempt_no is None:
                prior = self._connection.execute(
                    """
                    SELECT * FROM operation_attempts
                    WHERE operation_id = ? ORDER BY attempt_no DESC LIMIT 1
                    """,
                    (operation.value,),
                ).fetchone()
            else:
                prior = self._connection.execute(
                    """
                    SELECT * FROM operation_attempts
                    WHERE operation_id = ? AND attempt_no = ?
                    """,
                    (operation.value, int(prior_attempt_no)),
                ).fetchone()
            if prior is not None:
                prior_attempt_no = int(prior["attempt_no"])
            now = self._now_ms()
            if prior is not None and str(prior["state"]) == OperationState.RUNNING.value:
                self._connection.execute(
                    """
                    UPDATE operation_attempts
                    SET state = ?, certainty = ?, recovery_reason = ?, ended_at_unix_ms = ?
                    WHERE operation_id = ? AND attempt_no = ?
                    """,
                    (
                        OperationState.INDETERMINATE.value,
                        OutcomeCertainty.INDETERMINATE.value,
                        reason_code,
                        now,
                        operation.value,
                        int(prior["attempt_no"]),
                    ),
                )
            if prior is not None:
                self._connection.execute(
                    """
                    UPDATE operation_leases
                    SET released_at_unix_ms = ?
                    WHERE operation_id = ? AND attempt_no = ? AND released_at_unix_ms IS NULL
                    """,
                    (now, operation.value, int(prior["attempt_no"])),
                )
            decision_no_row = self._connection.execute(
                """
                SELECT COALESCE(MAX(decision_no), 0) AS value
                FROM operation_recovery_decisions WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            decision_no = int(decision_no_row["value"]) + 1
            decision_value = _decision_value(decision)
            if decision_value not in _RECOVERY_DECISIONS:
                raise ValueError(f"unsupported recovery decision: {decision_value}")
            admits_successor = decision_value in _RECOVERY_SUCCESSOR_DECISIONS
            successor_attempt_no = (
                None
                if not admits_successor
                else (None if prior_attempt_no is None else prior_attempt_no + 1)
            )
            bound_policy_digest: str | None = None
            bound_policy_version: int | None = None
            boundary_digest: str | None = None
            checkpoint_digest: str | None = None
            if continuation_boundary is not None and policy_binding is None:
                raise InvalidTransition("continuation boundary requires a bound recovery policy")
            if policy_binding is not None:
                stored_policy_row = self._connection.execute(
                    "SELECT * FROM operation_recovery_policies WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()
                if stored_policy_row is None:
                    raise InvalidTransition("operation has no admission-bound recovery policy")
                stored_policy = self._recovery_policy_binding(operation, stored_policy_row)
                if stored_policy != policy_binding:
                    raise IdempotencyConflict("recovery policy binding does not match admission")
                if decision_value not in policy_binding.policy.allowed_decisions:
                    raise InvalidTransition("recovery decision is not allowed by bound policy")
                bound_policy_digest = policy_binding.policy_digest
                bound_policy_version = policy_binding.policy.policy_version

            if admits_successor and policy_binding is not None:
                if continuation_boundary is None or prior is None or prior_attempt_no is None:
                    raise InvalidTransition(
                        "policy-bound successor requires an exact predecessor boundary"
                    )
                if (
                    continuation_boundary.operation != operation
                    or continuation_boundary.prior_attempt.attempt_no != prior_attempt_no
                    or continuation_boundary.prior_attempt.attempt_id
                    != str(prior["attempt_id"])
                    or continuation_boundary.runtime_generation
                    != int(prior["runtime_generation"])
                    or continuation_boundary.dispatcher_generation
                    != int(prior["dispatcher_generation"])
                    or continuation_boundary.policy_digest != policy_binding.policy_digest
                    or continuation_boundary.input_digest != bound_input_digest
                    or continuation_boundary.environment_digest
                    != policy_binding.environment_digest
                    or continuation_boundary.deadline_unix_ms
                    != self._request_deadline(str(row["request_json"]))
                ):
                    raise StaleAttemptFence(
                        "continuation boundary does not match predecessor authority"
                    )
                lease_row = self._connection.execute(
                    """
                    SELECT lease_epoch FROM operation_leases
                    WHERE operation_id = ? AND attempt_no = ?
                    ORDER BY lease_epoch DESC LIMIT 1
                    """,
                    (operation.value, prior_attempt_no),
                ).fetchone()
                if (
                    lease_row is None
                    or int(lease_row["lease_epoch"])
                    != continuation_boundary.lease_epoch
                ):
                    raise StaleAttemptFence("continuation boundary lease epoch is stale")
                control = self._connection.execute(
                    "SELECT cancellation_requested FROM operation_controls WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()
                if control is not None and bool(control["cancellation_requested"]):
                    raise InvalidTransition("cancellation prevents successor recovery")
                if now >= continuation_boundary.deadline_unix_ms:
                    raise InvalidTransition("deadline prevents successor recovery")
                prior_successors = self._connection.execute(
                    """
                    SELECT COUNT(*) AS value FROM operation_recovery_decisions
                    WHERE operation_id = ? AND decision IN (?, ?)
                    """,
                    (
                        operation.value,
                        _RECOVERY_START_SUCCESSOR,
                        _RECOVERY_RESTORE_CHECKPOINT,
                    ),
                ).fetchone()
                assert prior_successors is not None
                if int(prior_successors["value"]) >= policy_binding.policy.max_successor_attempts:
                    raise InvalidTransition("successor recovery bound is exhausted")

                if decision_value == _RECOVERY_START_SUCCESSOR:
                    if not isinstance(continuation_boundary, OperationRlmStepBoundaryV1):
                        raise InvalidTransition("RLM successor requires an RLM step boundary")
                    boundary_table = "operation_rlm_boundaries"
                    checkpoint_digest = None
                else:
                    if not isinstance(
                        continuation_boundary, OperationWorkspaceCheckpointBoundaryV1
                    ):
                        raise InvalidTransition(
                            "workspace restore requires a workspace checkpoint boundary"
                        )
                    selection_row = self._connection.execute(
                        """
                        SELECT * FROM operation_workspace_checkpoint_selections
                        WHERE operation_id = ? AND prior_attempt_no = ?
                        """,
                        (operation.value, prior_attempt_no),
                    ).fetchone()
                    if selection_row is None or str(selection_row["state"]) != "restored":
                        raise InvalidTransition(
                            "workspace successor requires a verified restored selection"
                        )
                    selection = OperationWorkspaceCheckpointSelectionV1.model_validate_json(
                        str(selection_row["selection_json"]), strict=True
                    )
                    restored_json = canonical_json_bytes(
                        continuation_boundary.restored_handle
                    ).decode()
                    if (
                        selection.operation != continuation_boundary.operation
                        or selection.prior_attempt
                        != continuation_boundary.prior_attempt
                        or selection.runtime_generation
                        != continuation_boundary.runtime_generation
                        or selection.dispatcher_generation
                        != continuation_boundary.dispatcher_generation
                        or selection.lease_epoch != continuation_boundary.lease_epoch
                        or selection.policy_digest != continuation_boundary.policy_digest
                        or selection.input_digest != continuation_boundary.input_digest
                        or selection.checkpoint_operation
                        != continuation_boundary.checkpoint_operation
                        or selection.checkpoint_manifest_digest
                        != continuation_boundary.checkpoint_manifest_digest
                        or selection.source_handle != continuation_boundary.source_handle
                        or selection.environment_digest
                        != continuation_boundary.environment_digest
                        or selection.exclusion_count
                        != continuation_boundary.exclusion_count
                        or selection.exclusions_digest
                        != continuation_boundary.exclusions_digest
                        or selection.artifacts_digest
                        != continuation_boundary.artifacts_digest
                        or selection.completeness != continuation_boundary.completeness
                        or selection.deadline_unix_ms
                        != continuation_boundary.deadline_unix_ms
                        or str(selection_row["restored_handle_json"]) != restored_json
                    ):
                        raise IdempotencyConflict(
                            "workspace boundary does not match the restored selection"
                        )
                    boundary_table = "operation_workspace_boundaries"
                    checkpoint_digest = continuation_boundary.checkpoint_manifest_digest
                boundary_json = canonical_json_bytes(continuation_boundary).decode()
                existing_boundary = self._connection.execute(
                    f"""
                    SELECT * FROM {boundary_table}
                    WHERE operation_id = ? AND prior_attempt_no = ?
                    """,
                    (operation.value, prior_attempt_no),
                ).fetchone()
                if existing_boundary is not None:
                    if (
                        str(existing_boundary["boundary_digest"])
                        != continuation_boundary.boundary_digest
                        or str(existing_boundary["boundary_json"]) != boundary_json
                    ):
                        raise IdempotencyConflict(
                            "predecessor attempt already binds a different continuation boundary"
                        )
                else:
                    self._connection.execute(
                        f"""
                        INSERT INTO {boundary_table}(
                            operation_id, prior_attempt_no, boundary_digest,
                            boundary_json, created_at_unix_ms
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            operation.value,
                            prior_attempt_no,
                            continuation_boundary.boundary_digest,
                            boundary_json,
                            continuation_boundary.created_at_unix_ms,
                        ),
                    )
                boundary_digest = continuation_boundary.boundary_digest

            self._connection.execute(
                """
                INSERT INTO operation_recovery_decisions(
                    operation_id, decision_no, prior_attempt_no, decision, reason_code,
                    input_digest, policy_version, policy_digest, checkpoint_digest,
                    effect_receipt_digest, continuation_boundary_digest,
                    successor_attempt_no, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                """,
                (
                    operation.value,
                    decision_no,
                    prior_attempt_no,
                    str(decision_value),
                    reason_code,
                    bound_input_digest,
                    bound_policy_version,
                    bound_policy_digest,
                    checkpoint_digest,
                    boundary_digest,
                    successor_attempt_no,
                    now,
                ),
            )
            revision = int(row["record_revision"]) + 1
            if admits_successor:
                self._connection.execute(
                    """
                    UPDATE operations
                    SET state = ?, certainty = ?, runtime_generation = ?,
                        record_revision = ?, reconciliation_required = 0,
                        updated_at_unix_ms = ?
                    WHERE operation_id = ?
                    """,
                    (
                        OperationState.ACCEPTED.value,
                        OutcomeCertainty.CERTAIN.value,
                        runtime_generation,
                        revision,
                        now,
                        operation.value,
                    ),
                )
                if dispatch is None:
                    self._connection.execute(
                        """
                        INSERT INTO operation_dispatch(
                            operation_id, kind, state, queued_at_unix_ms,
                            running_at_unix_ms, finished_at_unix_ms, current_attempt_no
                        ) VALUES (?, ?, ?, ?, NULL, NULL, ?)
                        """,
                        (
                            operation.value,
                            (
                                policy_binding.operation_kind
                                if policy_binding is not None
                                else "rlm.execute"
                            ),
                            _DISPATCH_QUEUED,
                            now,
                            prior_attempt_no,
                        ),
                    )
                else:
                    if (
                        policy_binding is not None
                        and str(dispatch["kind"]) != policy_binding.operation_kind
                    ):
                        raise IdempotencyConflict(
                            "dispatch kind does not match the recovery policy"
                        )
                    self._connection.execute(
                        """
                        UPDATE operation_dispatch
                        SET state = ?, queued_at_unix_ms = ?, running_at_unix_ms = NULL,
                            finished_at_unix_ms = NULL
                        WHERE operation_id = ?
                        """,
                        (_DISPATCH_QUEUED, now, operation.value),
                    )
                event_state = OperationState.ACCEPTED
                event_certainty = OutcomeCertainty.CERTAIN
            else:
                self._connection.execute(
                    """
                    UPDATE operation_dispatch SET state = ?, finished_at_unix_ms = ?
                    WHERE operation_id = ?
                    """,
                    (_DISPATCH_PARKED, now, operation.value),
                )
                event_state = OperationState.INDETERMINATE
                event_certainty = OutcomeCertainty.INDETERMINATE
                revision = int(row["record_revision"])
            self._insert_event(
                operation.value,
                event_state,
                event_certainty,
                revision,
                now,
                "recovery_decision",
                attempt_no=prior_attempt_no,
                event_kind="recovery_decision",
                payload={
                    "decision": str(decision_value),
                    "decision_no": decision_no,
                    "reason_code": reason_code,
                    "policy_digest": bound_policy_digest,
                    "continuation_boundary_digest": boundary_digest,
                    "successor_attempt_no": successor_attempt_no,
                },
            )
            return self.get(operation)

    def recover_terminal_from_receipt(
        self,
        operation: OperationRef,
        runtime_generation: int,
        *,
        state: OperationState,
        result_json: str,
        reason_code: str,
        input_digest: str,
    ) -> OperationRecord:
        """Commit a certain terminal state from an authoritative durable receipt."""

        if state not in {
            OperationState.SUCCEEDED,
            OperationState.CANCELLED,
            OperationState.TIMED_OUT,
        }:
            raise ValueError("receipt recovery supports succeeded, cancelled, or timed_out")
        if not reason_code:
            raise ValueError("reason_code must not be empty")
        with self._lock, self._connection:
            self._require_current_runtime_unlocked(runtime_generation)
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if OperationState(row["state"]) is not OperationState.INDETERMINATE:
                raise InvalidTransition("receipt recovery requires an indeterminate operation")
            if input_digest != str(row["input_digest"]):
                raise IdempotencyConflict("recovery input digest does not match accepted intent")
            dispatch = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if dispatch is None or str(dispatch["state"]) not in {
                _DISPATCH_RUNNING,
                _DISPATCH_PARKED,
            }:
                raise InvalidTransition("receipt recovery requires a parked or running dispatch")
            attempt_no = dispatch["current_attempt_no"]
            now = self._now_ms()
            decision_row = self._connection.execute(
                """
                SELECT COALESCE(MAX(decision_no), 0) AS value
                FROM operation_recovery_decisions WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            decision_no = int(decision_row["value"]) + 1
            self._connection.execute(
                """
                INSERT INTO operation_recovery_decisions(
                    operation_id, decision_no, prior_attempt_no, decision,
                    reason_code, input_digest, checkpoint_digest,
                    successor_attempt_no, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)
                """,
                (
                    operation.value,
                    decision_no,
                    attempt_no,
                    "terminal_from_receipt",
                    reason_code,
                    input_digest,
                    now,
                ),
            )
            revision = int(row["record_revision"]) + 1
            self._connection.execute(
                """
                UPDATE operations
                SET state = ?, certainty = ?, runtime_generation = ?,
                    record_revision = ?, reconciliation_required = 0,
                    result_json = ?, failure_json = NULL, updated_at_unix_ms = ?
                WHERE operation_id = ? AND state = ?
                """,
                (
                    state.value,
                    OutcomeCertainty.CERTAIN.value,
                    runtime_generation,
                    revision,
                    result_json,
                    now,
                    operation.value,
                    OperationState.INDETERMINATE.value,
                ),
            )
            if attempt_no is not None:
                self._connection.execute(
                    """
                    UPDATE operation_attempts
                    SET state = ?, certainty = ?, recovery_reason = ?,
                        ended_at_unix_ms = COALESCE(ended_at_unix_ms, ?)
                    WHERE operation_id = ? AND attempt_no = ?
                    """,
                    (
                        state.value,
                        OutcomeCertainty.CERTAIN.value,
                        reason_code,
                        now,
                        operation.value,
                        int(attempt_no),
                    ),
                )
            self._connection.execute(
                """
                UPDATE operation_dispatch
                SET state = ?, finished_at_unix_ms = ?
                WHERE operation_id = ?
                """,
                (_DISPATCH_COMPLETED, now, operation.value),
            )
            self._insert_event(
                operation.value,
                state,
                OutcomeCertainty.CERTAIN,
                revision,
                now,
                "terminal_from_receipt",
                attempt_no=None if attempt_no is None else int(attempt_no),
                event_kind="terminal_from_receipt",
                payload={
                    "decision_no": decision_no,
                    "reason_code": reason_code,
                    "state": state.value,
                },
            )
            return self.get(operation)

    def event_page(
        self,
        operation: OperationRef,
        after_sequence: int = 0,
        limit: int = DEFAULT_EVENT_PAGE_LIMIT,
        max_limit: int = MAX_EVENT_PAGE_LIMIT,
        max_bytes: int = DEFAULT_EVENT_PAGE_BYTES,
    ) -> Any:
        """Read a bounded global cursor page without writing any registry state."""

        if after_sequence < 0:
            raise ValueError("after_sequence must not be negative")
        if limit < 1 or limit > min(max_limit, MAX_EVENT_PAGE_LIMIT):
            raise ValueError(f"limit must be between 1 and {min(max_limit, MAX_EVENT_PAGE_LIMIT)}")
        if max_bytes < 1024 or max_bytes > MAX_EVENT_PAGE_BYTES:
            raise ValueError(
                f"max_bytes must be between 1024 and {MAX_EVENT_PAGE_BYTES}"
            )
        with self._lock:
            exists = self._connection.execute(
                "SELECT 1 FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if exists is None:
                raise KeyError(operation.value)
            rows = self._connection.execute(
                """
                SELECT * FROM operation_events
                WHERE operation_id = ? AND sequence > ?
                ORDER BY sequence
                LIMIT ?
                """,
                (operation.value, after_sequence, limit + 1),
            ).fetchall()
            visible_rows: list[sqlite3.Row] = []
            events_list: list[OperationEventEnvelopeV1] = []
            used_bytes = 0
            for event_row in rows[:limit]:
                event = self._event_envelope(operation, event_row)
                event_bytes = len(canonical_json_bytes(event.model_dump(mode="json")))
                if used_bytes + event_bytes > max_bytes:
                    if not events_list:
                        raise RegistryError("single operation event exceeds max_bytes")
                    break
                visible_rows.append(event_row)
                events_list.append(event)
                used_bytes += event_bytes
            events = tuple(events_list)
            has_more = len(rows) > len(events)
            next_sequence = (
                after_sequence
                if not visible_rows
                else int(visible_rows[-1]["sequence"])
            )
            terminal = self._record_from_operation_id(operation)
            terminal_snapshot = (
                None
                if terminal.state not in TERMINAL_STATES or has_more
                else self.continuity_snapshot(operation)
            )
            return OperationEventPageV1(
                operation=operation,
                after_sequence=after_sequence,
                events=events,
                next_sequence=next_sequence,
                has_more=has_more,
                terminal_snapshot=terminal_snapshot,
            )

    def continuity_snapshot(self, operation: OperationRef) -> Any:
        """Project continuity state using read-only queries."""

        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            attempt_row = self._connection.execute(
                """
                SELECT * FROM operation_attempts
                WHERE operation_id = ? ORDER BY attempt_no DESC LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
            control_row = self._connection.execute(
                "SELECT * FROM operation_controls WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            dispatch_row = self._connection.execute(
                "SELECT * FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            last_event = self._connection.execute(
                "SELECT MAX(sequence) AS value FROM operation_events WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            record = self._record(row)
            control = self._control_model(operation, control_row)
            last_attempt = (
                None
                if attempt_row is None
                else self._attempt_ref_for_no(operation, int(attempt_row["attempt_no"]))
            )
            current_attempt = (
                last_attempt
                if dispatch_row is not None and str(dispatch_row["state"]) == _DISPATCH_RUNNING
                else None
            )
            dispatcher_state = None if dispatch_row is None else str(dispatch_row["state"])
            last_event_sequence = (
                0 if last_event["value"] is None else int(last_event["value"])
            )
            return OperationContinuitySnapshotV1(
                operation=operation,
                operation_state=record.state,
                certainty=record.certainty,
                record_revision=record.record_revision,
                current_attempt=current_attempt,
                last_attempt=last_attempt,
                control=control,
                dispatcher_state=cast(DispatchState | None, dispatcher_state),
                last_event_sequence=last_event_sequence,
                reconciliation_required=record.reconciliation_required,
                recovery_reason=(
                    None if attempt_row is None else attempt_row["recovery_reason"]
                ),
            )

    def _current_runtime_generation_unlocked(self) -> int:
        row = self._connection.execute(
            "SELECT generation FROM runtime_meta WHERE singleton = 1"
        ).fetchone()
        assert row is not None
        return int(row["generation"])

    def _require_current_runtime_unlocked(self, runtime_generation: int) -> None:
        if runtime_generation != self._current_runtime_generation_unlocked():
            raise StaleRuntimeGeneration(
                f"runtime generation {runtime_generation} is not current"
            )

    @staticmethod
    def _request_deadline(request_json: str) -> int:
        try:
            value = json.loads(request_json)["deadline_unix_ms"]
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RegistryError("stored request envelope has no valid deadline") from error
        if isinstance(value, bool) or not isinstance(value, int):
            raise RegistryError("stored request deadline is not an integer")
        return value

    @staticmethod
    def _attempt_id(
        operation_id: str,
        attempt_no: int,
        runtime_generation: int,
        dispatcher_generation: int,
    ) -> str:
        digest = canonical_sha256(
            {
                "attempt_no": attempt_no,
                "dispatcher_generation": dispatcher_generation,
                "operation_id": operation_id,
                "runtime_generation": runtime_generation,
            }
        )
        return f"attempt-{digest[7:39]}"

    def _expire_queued_unlocked(self, row: sqlite3.Row, now: int) -> None:
        operation_id = str(row["operation_id"])
        revision = int(row["record_revision"]) + 1
        self._connection.execute(
            """
            UPDATE operations
            SET state = ?, certainty = ?, runtime_generation = ?, record_revision = ?,
                reconciliation_required = 0, updated_at_unix_ms = ?
            WHERE operation_id = ? AND state = ?
            """,
            (
                OperationState.TIMED_OUT.value,
                OutcomeCertainty.CERTAIN.value,
                self._current_runtime_generation_unlocked(),
                revision,
                now,
                operation_id,
                OperationState.ACCEPTED.value,
            ),
        )
        self._connection.execute(
            """
            UPDATE operation_dispatch
            SET state = ?, finished_at_unix_ms = ?
            WHERE operation_id = ?
            """,
            (_DISPATCH_COMPLETED, now, operation_id),
        )
        self._insert_event(
            operation_id,
            OperationState.TIMED_OUT,
            OutcomeCertainty.CERTAIN,
            revision,
            now,
            "deadline_expired_before_claim",
            event_kind="deadline_expired_before_claim",
            payload={"deadline_unix_ms": self._request_deadline(row["request_json"])},
        )

    def _validate_fence_unlocked(
        self,
        operation: OperationRef,
        attempt_no: int,
        attempt_id: str,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        if runtime_generation != self._current_runtime_generation_unlocked():
            raise StaleAttemptFence("runtime generation fence is stale")
        attempt = self._connection.execute(
            """
            SELECT * FROM operation_attempts
            WHERE operation_id = ? AND attempt_no = ? AND attempt_id = ?
            """,
            (operation.value, attempt_no, attempt_id),
        ).fetchone()
        if attempt is None:
            raise StaleAttemptFence("attempt identity is not current")
        if int(attempt["runtime_generation"]) != runtime_generation:
            raise StaleAttemptFence("attempt runtime generation fence is stale")
        if int(attempt["dispatcher_generation"]) != dispatcher_generation:
            raise StaleAttemptFence("dispatcher generation fence is stale")
        if str(attempt["state"]) != OperationState.RUNNING.value:
            raise StaleAttemptFence("attempt is no longer running")
        lease = self._connection.execute(
            """
            SELECT * FROM operation_leases
            WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
            """,
            (operation.value, attempt_no, lease_epoch),
        ).fetchone()
        if lease is None:
            raise StaleAttemptFence("lease epoch is stale")
        if str(lease["owner_digest"]) != str(owner_digest):
            raise StaleAttemptFence("lease owner fence is stale")
        if lease["released_at_unix_ms"] is not None:
            raise StaleAttemptFence("lease has been released")
        if self._now_ms() >= int(lease["expires_at_unix_ms"]):
            raise StaleAttemptFence("lease has expired")
        dispatch = self._connection.execute(
            """
            SELECT state, current_attempt_no FROM operation_dispatch
            WHERE operation_id = ?
            """,
            (operation.value,),
        ).fetchone()
        if (
            dispatch is None
            or str(dispatch["state"]) != _DISPATCH_RUNNING
            or int(dispatch["current_attempt_no"]) != attempt_no
        ):
            raise StaleAttemptFence("dispatch no longer owns this attempt")
        outer = self._connection.execute(
            "SELECT state, runtime_generation FROM operations WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        if outer is None or int(outer["runtime_generation"]) != runtime_generation:
            raise StaleAttemptFence("outer runtime generation fence is stale")
        if str(outer["state"]) not in {
            OperationState.RUNNING.value,
            OperationState.INDETERMINATE.value,
            OperationState.SUCCEEDED.value,
            OperationState.FAILED.value,
            OperationState.CANCELLED.value,
            OperationState.TIMED_OUT.value,
        }:
            raise StaleAttemptFence("outer operation fence is stale")
        return attempt, lease

    def _validate_closed_fence_unlocked(
        self,
        operation: OperationRef,
        attempt: sqlite3.Row,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
    ) -> None:
        """Validate an already committed attempt without requiring a live lease."""

        if runtime_generation != self._current_runtime_generation_unlocked():
            raise StaleAttemptFence("runtime generation fence is stale")
        if int(attempt["runtime_generation"]) != runtime_generation:
            raise StaleAttemptFence("attempt runtime generation fence is stale")
        if int(attempt["dispatcher_generation"]) != dispatcher_generation:
            raise StaleAttemptFence("dispatcher generation fence is stale")
        attempt_no = int(attempt["attempt_no"])
        lease = self._connection.execute(
            """
            SELECT * FROM operation_leases
            WHERE operation_id = ? AND attempt_no = ? AND lease_epoch = ?
            """,
            (operation.value, attempt_no, lease_epoch),
        ).fetchone()
        if lease is None or str(lease["owner_digest"]) != str(owner_digest):
            raise StaleAttemptFence("closed lease ownership fence is stale")
        if lease["released_at_unix_ms"] is None:
            raise StaleAttemptFence("closed attempt still has a live lease")
        outer = self._connection.execute(
            "SELECT state, runtime_generation FROM operations WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        if outer is None or int(outer["runtime_generation"]) != runtime_generation:
            raise StaleAttemptFence("outer runtime generation fence is stale")
        outer_state = OperationState(outer["state"])
        if (
            outer_state not in TERMINAL_STATES
            and outer_state is not OperationState.INDETERMINATE
        ):
            raise StaleAttemptFence("outer operation is not closed")
        dispatch = self._connection.execute(
            """
            SELECT state, current_attempt_no FROM operation_dispatch
            WHERE operation_id = ?
            """,
            (operation.value,),
        ).fetchone()
        expected_dispatch = (
            _DISPATCH_PARKED
            if outer_state is OperationState.INDETERMINATE
            else _DISPATCH_COMPLETED
        )
        if (
            dispatch is None
            or str(dispatch["state"]) != expected_dispatch
            or int(dispatch["current_attempt_no"]) != attempt_no
        ):
            raise StaleAttemptFence("closed dispatch fence is stale")

    def _insert_event(
        self,
        operation_id: str,
        state: OperationState,
        certainty: OutcomeCertainty,
        revision: int,
        now: int,
        note: str,
        *,
        attempt_no: int | None = None,
        event_kind: str | None = None,
        payload: Any = None,
    ) -> None:
        if event_kind is None:
            event_kind = note
        if payload is None:
            payload = {"note": note}
        payload_json = canonical_json_bytes(payload).decode()
        payload_digest = canonical_sha256(payload)
        self._connection.execute(
            """
            INSERT INTO operation_events(
                operation_id, state, certainty, record_revision, at_unix_ms, note,
                attempt_no, event_kind, payload_json, payload_digest
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                operation_id,
                state.value,
                certainty.value,
                revision,
                now,
                note,
                attempt_no,
                event_kind,
                payload_json,
                payload_digest,
            ),
        )

    def _record_from_operation_id(self, operation: OperationRef) -> OperationRecord:
        row = self._connection.execute(
            "SELECT * FROM operations WHERE operation_id = ?", (operation.value,)
        ).fetchone()
        assert row is not None
        return self._record(row)

    @staticmethod
    def _record(row: sqlite3.Row) -> OperationRecord:
        failure = None
        if row["failure_json"] is not None:
            failure = FailureEnvelope.model_validate_json(row["failure_json"], strict=True)
        return OperationRecord(
            operation=OperationRef(value=str(row["operation_id"])),
            host_value=str(row["host_value"]),
            principal_value=str(row["principal_value"]),
            idempotency_key=str(row["idempotency_key"]),
            input_digest=str(row["input_digest"]),
            state=OperationState(row["state"]),
            certainty=OutcomeCertainty(row["certainty"]),
            runtime_generation=int(row["runtime_generation"]),
            record_revision=int(row["record_revision"]),
            reconciliation_required=bool(row["reconciliation_required"]),
            request_json=str(row["request_json"]),
            payload_json=str(row["payload_json"]),
            result_json=None if row["result_json"] is None else str(row["result_json"]),
            failure=failure,
            created_at_unix_ms=int(row["created_at_unix_ms"]),
            updated_at_unix_ms=int(row["updated_at_unix_ms"]),
        )

    @staticmethod
    def _lease_model(
        operation: OperationRef,
        row: sqlite3.Row,
        attempt_row: sqlite3.Row,
    ) -> OperationLeaseRecordV1:
        attempt_ref = OperationAttemptRefV1(
            operation=operation,
            attempt_no=int(row["attempt_no"]),
            attempt_id=str(attempt_row["attempt_id"]),
        )
        return OperationLeaseRecordV1(
            attempt=attempt_ref,
            runtime_generation=int(attempt_row["runtime_generation"]),
            dispatcher_generation=int(attempt_row["dispatcher_generation"]),
            lease_epoch=int(row["lease_epoch"]),
            owner_digest=str(row["owner_digest"]),
            acquired_at_unix_ms=int(row["acquired_at_unix_ms"]),
            heartbeat_at_unix_ms=int(row["heartbeat_at_unix_ms"]),
            expires_at_unix_ms=int(row["expires_at_unix_ms"]),
            released_at_unix_ms=row["released_at_unix_ms"],
        )

    @staticmethod
    def _control_model(
        operation: OperationRef,
        row: sqlite3.Row | None = None,
    ) -> OperationControlStateV1:
        if row is None:
            return OperationControlStateV1(
                operation=operation,
                control_revision=0,
                cancellation_requested=False,
            )
        return OperationControlStateV1(
            operation=operation,
            control_revision=int(row["control_revision"]),
            cancellation_requested=bool(row["cancellation_requested"]),
            requested_at_unix_ms=row["requested_at_unix_ms"],
            requested_by_digest=row["requested_by_digest"],
            reason_code=row["reason_code"],
        )

    @staticmethod
    def _dispatch_model(
        operation: OperationRef,
        row: sqlite3.Row,
    ) -> OperationDispatchRecordV1:
        return OperationDispatchRecordV1(
            operation=operation,
            kind=str(row["kind"]),
            state=str(row["state"]),
            queued_at_unix_ms=row["queued_at_unix_ms"],
            running_at_unix_ms=row["running_at_unix_ms"],
            finished_at_unix_ms=row["finished_at_unix_ms"],
            current_attempt_no=row["current_attempt_no"],
        )

    @staticmethod
    def _recovery_policy_binding(
        operation: OperationRef,
        row: sqlite3.Row,
    ) -> OperationRecoveryPolicyBindingV1:
        policy = OperationRecoveryPolicyV1.model_validate_json(
            str(row["policy_json"]), strict=True
        )
        return OperationRecoveryPolicyBindingV1(
            operation=operation,
            operation_kind=str(row["operation_kind"]),
            policy=policy,
            policy_digest=str(row["policy_digest"]),
            environment_digest=str(row["environment_digest"]),
            created_at_unix_ms=int(row["created_at_unix_ms"]),
        )

    def _attempt_ref_for_no(
        self,
        operation: OperationRef,
        attempt_no: int | None,
    ) -> Any | None:
        if attempt_no is None:
            return None
        row = self._connection.execute(
            """
            SELECT * FROM operation_attempts
            WHERE operation_id = ? AND attempt_no = ?
            """,
            (operation.value, attempt_no),
        ).fetchone()
        if row is None:
            return None
        return OperationAttemptRefV1(
            operation=operation,
            attempt_no=int(row["attempt_no"]),
            attempt_id=str(row["attempt_id"]),
        )


    def _event_envelope(self, operation: OperationRef, row: sqlite3.Row) -> Any:
        payload_json = row["payload_json"]
        event_kind = row["event_kind"] or "legacy_lifecycle"
        if payload_json is None:
            payload_json = canonical_json_bytes({"note": str(row["note"])}).decode()
        payload = json.loads(payload_json)
        if not isinstance(payload, dict):
            payload = {"value": payload}
            payload_json = canonical_json_bytes(payload).decode()
        payload_digest = row["payload_digest"] or canonical_sha256(payload)
        attempt = self._attempt_ref_for_no(operation, row["attempt_no"])
        return OperationEventEnvelopeV1(
            sequence=int(row["sequence"]),
            operation=operation,
            attempt=attempt,
            event_kind=str(event_kind),
            state=OperationState(row["state"]),
            certainty=OutcomeCertainty(row["certainty"]),
            record_revision=int(row["record_revision"]),
            at_unix_ms=int(row["at_unix_ms"]),
            payload=payload,
            payload_digest=str(payload_digest),
        )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = [
    "IdempotencyConflict",
    "InvalidTransition",
    "OperationDispatchClaim",
    "OperationDispatchRecordV1",
    "OperationRegistry",
    "RegistryError",
    "StaleAttemptFence",
    "StaleRuntimeGeneration",
    "UnsupportedRegistrySchema",
]
