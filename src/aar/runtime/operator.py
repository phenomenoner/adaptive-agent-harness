"""Read-only provider-ready operator inspection and planning.

This module deliberately avoids ``OperationRegistry`` and ``ReferenceHost``: both
constructors can mutate SQLite state or allocate a runtime generation.  The B1
operator boundary uses read-only/query-only SQLite handles and emits canonical
contract bytes only.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
import sys
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_models import (
    HostActivationIntent,
    HostActivationProfile,
    ProviderReadyCandidate,
)
from aar.provider_ready_operator_models import (
    CUTOVER_PLAN_SCHEMA_VERSION,
    ActivationGenerationAuthority,
    CandidateBinding,
    CutoverPlan,
    DatabaseChecks,
    EpochOwnerBinding,
    FileArtifact,
    NonterminalCounts,
    SidecarObservation,
    SnapshotPlan,
)
from aar.provider_ready_runtime_models import (
    ACTIVATION_READBACK_SCHEMA_VERSION,
    ActivationReadback,
    BackendAvailability,
    PlannerReadback,
)
from aar.rlm_workbench_models import build_workbench_capability
from aar.runtime.process_identity import current_process_identity


class OperatorError(RuntimeError):
    """A read-only operator precondition failed without authorizing mutation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class RuntimeHomePaths:
    runtime_home: Path
    database: Path
    authority: Path
    stable_lock: Path
    predecessor_lock: Path

    @classmethod
    def resolve(cls, runtime_home: Path) -> RuntimeHomePaths:
        home = runtime_home.expanduser().resolve()
        authority = home / "authority"
        return cls(
            runtime_home=home,
            database=home / "reference.sqlite3",
            authority=authority,
            stable_lock=authority / "runtime-owner.lock",
            predecessor_lock=Path(f"{home / 'reference.sqlite3'}.runtime.lock"),
        )


@dataclass(frozen=True, slots=True)
class RegistryObservation:
    schema_version: int
    schema_digest: str
    row_set_digest: str
    database: FileArtifact
    wal: SidecarObservation
    shm: SidecarObservation
    checks: DatabaseChecks
    nonterminal_counts: NonterminalCounts


def _digest_bytes(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _file_identity(path: Path, *, stat_result: os.stat_result | None = None) -> str:
    observed = path.stat() if stat_result is None else stat_result
    return f"dev-{observed.st_dev}-ino-{observed.st_ino}"


def _file_artifact(path: Path) -> FileArtifact:
    observed = path.stat()
    content = path.read_bytes()
    return FileArtifact(
        artifact_id=_file_identity(path, stat_result=observed),
        digest=_digest_bytes(content),
        size_bytes=len(content),
    )


def _sidecar(path: Path) -> SidecarObservation:
    if not path.exists():
        return SidecarObservation(
            state="absent",
            file_identity=None,
            size_bytes=None,
            digest=None,
        )
    observed = path.stat()
    content = path.read_bytes()
    return SidecarObservation(
        state="present",
        file_identity=_file_identity(path, stat_result=observed),
        size_bytes=len(content),
        digest=_digest_bytes(content),
    )


def _sqlite_read_only(database: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(database), safe='/:')}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    denied_actions = {
        sqlite3.SQLITE_ALTER_TABLE,
        sqlite3.SQLITE_ANALYZE,
        sqlite3.SQLITE_ATTACH,
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DETACH,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_REINDEX,
        sqlite3.SQLITE_TRANSACTION,
        sqlite3.SQLITE_UPDATE,
    }

    def authorize(
        action: int,
        argument1: str | None,
        _argument2: str | None,
        _database_name: str | None,
        _trigger_name: str | None,
    ) -> int:
        if action in denied_actions:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_PRAGMA and argument1 == "wal_checkpoint":
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    connection.set_authorizer(authorize)
    return connection


def _table_names(connection: sqlite3.Connection) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    )


def _schema_digest(connection: sqlite3.Connection) -> str:
    rows = [
        {
            "name": str(row[0]),
            "sql": None if row[1] is None else str(row[1]),
            "table": str(row[2]),
            "type": str(row[3]),
        }
        for row in connection.execute(
            "SELECT name, sql, tbl_name, type FROM sqlite_schema "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        )
    ]
    return canonical_sha256(rows)


def _row_set_digest(connection: sqlite3.Connection, tables: tuple[str, ...]) -> str:
    payload: list[dict[str, object]] = []
    for table in tables:
        escaped = table.replace('"', '""')
        columns = [
            str(row[1]) for row in connection.execute(f'PRAGMA table_info("{escaped}")')
        ]
        encoded_rows: list[list[object]] = []
        for row in connection.execute(f'SELECT * FROM "{escaped}"'):
            encoded_rows.append(
                [
                    {"bytes_hex": bytes(value).hex()}
                    if isinstance(value, (bytes, bytearray))
                    else value
                    for value in row
                ]
            )
        encoded_rows.sort(key=lambda value: canonical_json_bytes(value))
        payload.append({"table": table, "columns": columns, "rows": encoded_rows})
    return canonical_sha256(payload)


def _count_if_present(
    connection: sqlite3.Connection,
    tables: set[str],
    table: str,
    predicate: str,
) -> int:
    if table not in tables:
        return 0
    row = connection.execute(
        f'SELECT COUNT(*) FROM "{table}" WHERE {predicate}'
    ).fetchone()
    return int(row[0])


def _nonterminal_counts(
    connection: sqlite3.Connection, tables: tuple[str, ...]
) -> NonterminalCounts:
    present = set(tables)
    return NonterminalCounts(
        operations=_count_if_present(
            connection,
            present,
            "operations",
            "state NOT IN ('succeeded','failed','cancelled','timed_out','indeterminate')",
        ),
        attempts=_count_if_present(
            connection,
            present,
            "operation_attempts",
            "state NOT IN ('succeeded','failed','cancelled','timed_out','indeterminate')",
        ),
        workbench_jobs=_count_if_present(
            connection,
            present,
            "rlm_workbench_jobs",
            "phase NOT IN ('succeeded','failed','cancelled','timed_out')",
        ),
        caller_tickets=_count_if_present(
            connection,
            present,
            "caller_work_tickets",
            "state NOT IN ('settled_success','settled_failure','cancelled_before_send',"
            "'cancelled_certain','quarantined')",
        ),
        workers=_count_if_present(
            connection,
            present,
            "worker_bindings",
            "state NOT IN ('closed','lost','stopped')",
        ),
    )


def _assert_no_live_owner(connection: sqlite3.Connection, tables: tuple[str, ...]) -> None:
    if "supervisor_runs" not in tables:
        return
    count = int(
        connection.execute(
            "SELECT COUNT(*) FROM supervisor_runs "
            "WHERE state IN ('ready','degraded','draining')"
        ).fetchone()[0]
    )
    if count:
        raise OperatorError("CUTOVER_OWNER_ACTIVE", "registry has a nonterminal supervisor owner")


def _probe_stable_lock(path: Path) -> None:
    if not path.exists():
        return
    if os.name == "nt":
        # Windows lock acquisition is owned by B2. A visible stable owner file is
        # not safely classifiable by a read-only B1 probe.
        raise OperatorError(
            "CUTOVER_OWNER_ACTIVE",
            "stable runtime-owner lock exists and requires native Windows ownership verification",
        )
    import fcntl

    with path.open("rb") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise OperatorError("CUTOVER_OWNER_ACTIVE", "runtime-home lock is held") from error
        else:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def inspect_registry(paths: RuntimeHomePaths) -> RegistryObservation:
    """Inspect an exact registry without creating/checkpointing any path."""

    if not paths.database.is_file():
        raise OperatorError("REGISTRY_NOT_FOUND", f"registry does not exist: {paths.database}")
    _probe_stable_lock(paths.stable_lock)
    _probe_stable_lock(paths.predecessor_lock)
    before = (
        _file_artifact(paths.database),
        _sidecar(Path(f"{paths.database}-wal")),
        _sidecar(Path(f"{paths.database}-shm")),
    )
    connection = _sqlite_read_only(paths.database)
    try:
        tables = _table_names(connection)
        if "schema_migrations" not in tables:
            raise OperatorError("REGISTRY_VERSION_UNSUPPORTED", "schema_migrations is absent")
        versions = tuple(
            int(row[0])
            for row in connection.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        )
        supported_versions = (tuple(range(1, 6)), tuple(range(1, 7)))
        if versions not in supported_versions:
            raise OperatorError(
                "REGISTRY_VERSION_UNSUPPORTED",
                "expected contiguous registry versions 1..5 or 1..6, "
                f"observed {versions!r}",
            )
        _assert_no_live_owner(connection, tables)
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        if integrity != "ok" or foreign_key_violations:
            raise OperatorError(
                "CUTOVER_RECOVERY_REQUIRED",
                "registry failed read-only SQLite integrity/foreign-key checks",
            )
        observation = RegistryObservation(
            schema_version=versions[-1],
            schema_digest=_schema_digest(connection),
            row_set_digest=_row_set_digest(connection, tables),
            database=before[0],
            wal=before[1],
            shm=before[2],
            checks=DatabaseChecks(
                integrity_result="ok",
                foreign_key_violation_count=0,
            ),
            nonterminal_counts=_nonterminal_counts(connection, tables),
        )
    finally:
        connection.close()
    after = (
        _file_artifact(paths.database),
        _sidecar(Path(f"{paths.database}-wal")),
        _sidecar(Path(f"{paths.database}-shm")),
    )
    if before[:2] != after[:2]:
        raise OperatorError(
            "CUTOVER_PLAN_STALE",
            "database or WAL observations changed during read-only planning",
        )
    before_shm = before[2]
    after_shm = after[2]
    if (
        before_shm.state != after_shm.state
        or before_shm.file_identity != after_shm.file_identity
        or before_shm.size_bytes != after_shm.size_bytes
    ):
        raise OperatorError(
            "CUTOVER_PLAN_STALE",
            "SHM identity, presence, or size changed during read-only planning",
        )
    observation = replace(observation, shm=after_shm)
    if any(observation.nonterminal_counts.model_dump(mode="python").values()):
        raise OperatorError(
            "CUTOVER_OWNER_ACTIVE",
            "registry contains nonterminal operation, attempt, workbench, caller, or worker state",
        )
    return observation


def load_activation_intent(path: Path) -> HostActivationIntent:
    try:
        return HostActivationIntent.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValidationError, ValueError) as error:
        raise OperatorError(
            "ACTIVATION_PROFILE_INVALID", f"invalid activation intent: {error}"
        ) from error


def load_activation_profile(path: Path) -> HostActivationProfile:
    try:
        return HostActivationProfile.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValidationError, ValueError) as error:
        raise OperatorError(
            "ACTIVATION_PROFILE_INVALID", f"invalid activation profile: {error}"
        ) from error


def _migration_sql_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    return root / "docs" / "sdd" / "aar-rlm-native-workbench-v2" / "migration-v6.sql"


def build_cutover_plan(
    runtime_home: Path,
    intent_path: Path,
    profile_output: Path,
    *,
    now_ms: int | None = None,
) -> CutoverPlan:
    """Build one strict plan while leaving the runtime home byte-identical."""

    paths = RuntimeHomePaths.resolve(runtime_home)
    intent = load_activation_intent(intent_path)
    observation = inspect_registry(paths)
    runtime_home_digest = canonical_sha256(
        {"database": str(paths.database), "runtime_home": str(paths.runtime_home)}
    )
    if intent.runtime.runtime_home_digest != runtime_home_digest:
        raise OperatorError(
            "ACTIVATION_BINDING_MISMATCH",
            "activation intent runtime_home_digest does not match the selected runtime home",
        )
    if intent.runtime.database_identity != observation.database.artifact_id:
        raise OperatorError(
            "ACTIVATION_BINDING_MISMATCH",
            "activation intent database_identity does not match the selected registry",
        )
    migration_sql = _migration_sql_path().read_bytes()
    created = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    epoch = f"cutover-{secrets.token_hex(16)}"
    snapshot_id = f"snapshot-{secrets.token_hex(16)}"
    final_profile = profile_output.expanduser().resolve()
    identity = current_process_identity()
    return CutoverPlan.issue(
        schema_version=CUTOVER_PLAN_SCHEMA_VERSION,
        cutover_epoch=epoch,
        runtime_home_digest=runtime_home_digest,
        database_identity=observation.database.artifact_id,
        database_file=observation.database,
        owner=EpochOwnerBinding(
            authority_store_id=intent.cutover_authority_store_id,
            operator_identity_digest=canonical_sha256(identity.model_dump(mode="json")),
            runtime_owner_state="absent",
            exclusive_lock_state="available",
        ),
        source_registry_version=5,
        source_registry_schema_digest=observation.schema_digest,
        canonical_v5_row_set_digest=observation.row_set_digest,
        wal=observation.wal,
        shm=observation.shm,
        database_checks=observation.checks,
        nonterminal_counts=observation.nonterminal_counts,
        snapshot=SnapshotPlan(
            snapshot_id=snapshot_id,
            destination=str(paths.runtime_home / "snapshots" / f"{snapshot_id}.sqlite3"),
            backup_mode="sqlite_backup",
        ),
        candidate=CandidateBinding.model_validate(
            intent.candidate.model_dump(mode="python"), strict=True
        ),
        migration_sql_digest=_digest_bytes(migration_sql),
        activation_intent_digest=intent.intent_digest,
        final_profile_output=str(final_profile),
        profile_id=intent.profile_id,
        proposed_activation_generation=intent.activation_generation,
        previous_activation_authority_digest=intent.previous_activation_authority_digest,
        authority_store_id=intent.cutover_authority_store_id,
        allowed_recovery_actions=("abort", "apply", "status"),
        created_at_unix_ms=created,
        expires_at_unix_ms=created + 900_000,
    )


def _unconfigured_methods() -> tuple[BackendAvailability, ...]:
    capability = build_workbench_capability({})
    return tuple(
        BackendAvailability.model_validate(row, strict=True)
        for row in capability.root["methods"]
    )


def activation_status(runtime_home: Path, *, now_ms: int | None = None) -> ActivationReadback:
    """Return a strict read-only projection without fabricating C1/C2 verification."""

    paths = RuntimeHomePaths.resolve(runtime_home)
    observed = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    common: dict[str, Any] = {
        "schema_version": ACTIVATION_READBACK_SCHEMA_VERSION,
        "observed_at_unix_ms": observed,
        "runtime_generation": None,
        "supervisor_process_identity_digest": None,
        "candidate": None,
        "migration_attestation_digest": None,
        "profile_id": None,
        "activation_generation": None,
        "intent_digest": None,
        "profile_digest": None,
        "previous_activation_authority_digest": None,
        "activation_authority_digest": None,
        "grant_set_digest": None,
        "capability_digest": None,
        "broker_catalog_digest": None,
        "tool_surface_digest": None,
        "methods": _unconfigured_methods(),
        "planner": PlannerReadback(
            mode=None,
            ready=False,
            factory_id=None,
            factory_digest=None,
            method_manifest_digest=None,
        ),
        "route_catalog_digest": None,
        "route_profile_ids": (),
        "authority_store_id": None,
        "authority_history_tip_digest": None,
        "operator_epoch_kind": None,
        "operator_epoch": None,
        "latest_operator_receipt_digest": None,
    }
    if not paths.database.exists():
        return ActivationReadback.issue(
            **common,
            state="unconfigured",
            reason_code="none",
            registry_schema_version=None,
            registry_schema_digest=None,
            evidence_sources=(),
        )
    observation = inspect_registry(paths)
    if observation.schema_version == 5:
        return ActivationReadback.issue(
            **common,
            state="migration_required",
            reason_code="REGISTRY_VERSION_UNSUPPORTED",
            registry_schema_version=5,
            registry_schema_digest=observation.schema_digest,
            evidence_sources=("registry",),
        )
    return ActivationReadback.issue(
        **common,
        state="recovery_required",
        reason_code="RECONCILE_INPUT_REQUIRED",
        registry_schema_version=6,
        registry_schema_digest=observation.schema_digest,
        evidence_sources=("registry",),
    )


def verify_activation_profile(
    runtime_home: Path,
    *,
    supplied_profile: HostActivationProfile,
    installed_profile: HostActivationProfile,
    installed_authority: ActivationGenerationAuthority,
    installed_candidate: ProviderReadyCandidate,
    now_ms: int | None = None,
) -> ActivationReadback:
    """Join supplied profile bytes to the exact immutable installed authority."""

    if supplied_profile != installed_profile:
        raise OperatorError(
            "ACTIVATION_BINDING_MISMATCH",
            "supplied activation profile does not belong to the installed runtime",
        )
    observation = inspect_registry(RuntimeHomePaths.resolve(runtime_home))
    if observation.schema_version != 6:
        raise OperatorError(
            "ACTIVATION_BINDING_MISMATCH",
            "installed activation profile requires a v6 registry",
        )
    intent = installed_profile.intent
    authority = installed_authority
    candidate = CandidateBinding.model_validate(
        installed_candidate.model_dump(mode="python"), strict=True
    )
    observed = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    return ActivationReadback.issue(
        schema_version=ACTIVATION_READBACK_SCHEMA_VERSION,
        observed_at_unix_ms=observed,
        state="profile_verified",
        reason_code="none",
        runtime_generation=None,
        supervisor_process_identity_digest=None,
        candidate=candidate,
        registry_schema_version=6,
        registry_schema_digest=observation.schema_digest,
        migration_attestation_digest=installed_profile.migration_attestation_digest,
        profile_id=intent.profile_id,
        activation_generation=intent.activation_generation,
        intent_digest=intent.intent_digest,
        profile_digest=installed_profile.profile_digest,
        previous_activation_authority_digest=authority.previous_activation_authority_digest,
        activation_authority_digest=authority.authority_digest,
        grant_set_digest=None,
        capability_digest=None,
        broker_catalog_digest=None,
        tool_surface_digest=None,
        methods=_unconfigured_methods(),
        planner=PlannerReadback(
            mode=None,
            ready=False,
            factory_id=None,
            factory_digest=None,
            method_manifest_digest=None,
        ),
        route_catalog_digest=intent.routes.catalog_digest,
        route_profile_ids=intent.routes.allowed_profile_ids,
        authority_store_id=authority.authority_store_id,
        authority_history_tip_digest=authority.authority_digest,
        operator_epoch_kind=None,
        operator_epoch=None,
        latest_operator_receipt_digest=None,
        evidence_sources=(
            "activation_current",
            "activation_history",
            "installed_assets",
            "registry",
        ),
    )


def emit_canonical(document: Any, *, stream: Any = None) -> None:
    target = sys.stdout.buffer if stream is None else stream
    target.write(canonical_json_bytes(document))
    target.write(b"\n")
