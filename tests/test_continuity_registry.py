from __future__ import annotations

import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.registry import (
    InvalidTransition,
    OperationRegistry,
    StaleAttemptFence,
    StaleRuntimeGeneration,
    UnsupportedRegistrySchema,
)
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    Budget,
    HostRef,
    LaneRef,
    OperationRef,
    OperationState,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
)


class ManualClock:
    def __init__(self, now: int = 1_700_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now

    def advance(self, milliseconds: int) -> None:
        self.now += milliseconds


def envelope(
    clock: ManualClock,
    *,
    key: str = "continuity-idem-0001",
    runtime_generation: int = 1,
    deadline_delta_ms: int = 10_000,
) -> RequestEnvelope:
    principal = PrincipalRef(value="principal-continuity")
    return RequestEnvelope(
        request_id=f"request-{key}",
        idempotency_key=key,
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="rlm"),
        session=SessionRef(value="session-continuity"),
        runtime_generation=runtime_generation,
        capability_digest=canonical_sha256({"capabilities": "continuity-test"}),
        deadline_unix_ms=clock.now + deadline_delta_ms,
        grants=(),
        budget=Budget(wall_time_ms=max(deadline_delta_ms, 0)),
        trace_id=f"trace-{key}",
        input_digest=canonical_sha256({"key": key}),
    )


def accept_dispatch(
    registry: OperationRegistry,
    clock: ManualClock,
    *,
    key: str = "continuity-idem-0001",
    deadline_delta_ms: int = 10_000,
):
    request = envelope(
        clock,
        key=key,
        runtime_generation=registry.current_runtime_generation(),
        deadline_delta_ms=deadline_delta_ms,
    )
    payload_json = canonical_json_bytes({"kind": "rlm.execute", "key": key}).decode()
    record, created = registry.accept(request, payload_json)
    assert created is True
    registry.request_dispatch(record.operation)
    return record


def owner_digest(label: str) -> str:
    return canonical_sha256({"owner": label})


def open_registry(path: Path, clock: ManualClock) -> OperationRegistry:
    registry = OperationRegistry(path, clock)
    if registry.current_runtime_generation() == 0:
        registry.start_runtime()
    return registry


def table_count(path: Path, table: str) -> int:
    connection = sqlite3.connect(path)
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        assert row is not None
        return int(row[0])
    finally:
        connection.close()


def create_legacy_v1_database(path: Path, clock: ManualClock) -> tuple[str, str, str]:
    request = envelope(clock, key="legacy-idem-0001")
    operation_id = "op-legacy-continuity"
    request_json = canonical_json_bytes(request).decode()
    payload_json = canonical_json_bytes({"legacy": True}).decode()
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE runtime_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                generation INTEGER NOT NULL
            );
            INSERT INTO runtime_meta(singleton, generation) VALUES (1, 1);
            CREATE TABLE operations (
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
            CREATE TABLE operation_events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                operation_id TEXT NOT NULL,
                state TEXT NOT NULL,
                certainty TEXT NOT NULL,
                record_revision INTEGER NOT NULL,
                at_unix_ms INTEGER NOT NULL,
                note TEXT NOT NULL
            );
            """
        )
        connection.execute(
            """
            INSERT INTO operations(
                operation_id, host_value, principal_value, idempotency_key,
                input_digest, state, certainty, runtime_generation,
                record_revision, reconciliation_required, request_json,
                payload_json, result_json, failure_json,
                created_at_unix_ms, updated_at_unix_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)
            """,
            (
                operation_id,
                request.host.value,
                request.principal.value,
                request.idempotency_key,
                request.input_digest,
                OperationState.ACCEPTED.value,
                "certain",
                request.runtime_generation,
                0,
                0,
                request_json,
                payload_json,
                clock.now,
                clock.now,
            ),
        )
        connection.execute(
            """
            INSERT INTO operation_events(
                operation_id, state, certainty, record_revision,
                at_unix_ms, note
            ) VALUES (?, ?, ?, 0, ?, ?)
            """,
            (
                operation_id,
                OperationState.ACCEPTED.value,
                "certain",
                clock.now,
                "intent_persisted",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    return operation_id, request_json, payload_json


def test_additive_migration_preserves_legacy_bytes_and_rollback_copy(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "legacy.sqlite3"
    operation_id, request_json, payload_json = create_legacy_v1_database(database, clock)
    rollback_copy = tmp_path / "legacy.rollback.sqlite3"
    shutil.copy2(database, rollback_copy)
    rollback_digest = hashlib.sha256(rollback_copy.read_bytes()).hexdigest()

    registry = OperationRegistry(database, clock)
    assert registry.schema_versions() == (1, 2, 3, 4)
    migrated = registry.get(OperationRef(value=operation_id))
    assert migrated.request_json == request_json
    assert migrated.payload_json == payload_json
    connection = sqlite3.connect(database)
    try:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "operation_recovery_policies" in tables
        assert "operation_rlm_boundaries" in tables
        decision_columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(operation_recovery_decisions)"
            ).fetchall()
        }
        assert {
            "policy_version",
            "policy_digest",
            "effect_receipt_digest",
            "continuation_boundary_digest",
        } <= decision_columns
    finally:
        connection.close()
    registry.close()

    assert hashlib.sha256(rollback_copy.read_bytes()).hexdigest() == rollback_digest
    legacy_reader = sqlite3.connect(rollback_copy)
    try:
        legacy_row = legacy_reader.execute(
            "SELECT request_json, payload_json FROM operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()
        assert legacy_row == (request_json, payload_json)
        columns = {
            row[1]
            for row in legacy_reader.execute("PRAGMA table_info(operation_events)").fetchall()
        }
        assert "event_kind" not in columns
    finally:
        legacy_reader.close()


@pytest.mark.parametrize("tamper", ["newer", "digest"])
def test_schema_registry_fails_closed_on_newer_or_tampered_migration(
    tmp_path: Path,
    tamper: str,
) -> None:
    clock = ManualClock()
    database = tmp_path / f"{tamper}.sqlite3"
    registry = OperationRegistry(database, clock)
    registry.close()
    connection = sqlite3.connect(database)
    try:
        if tamper == "newer":
            connection.execute(
                """
                INSERT INTO schema_migrations(version, applied_at_unix_ms, migration_digest)
                VALUES (5, ?, ?)
                """,
                (clock.now, canonical_sha256({"unknown": 5})),
            )
        else:
            connection.execute(
                "UPDATE schema_migrations SET migration_digest = ? WHERE version = 2",
                (canonical_sha256({"tampered": True}),),
            )
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(UnsupportedRegistrySchema):
        OperationRegistry(database, clock)


def test_claim_heartbeat_and_atomic_terminal_commit_are_fenced(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "claim.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    events_before_replay = len(registry.events(record.operation))
    registry.request_dispatch(record.operation)
    assert len(registry.events(record.operation)) == events_before_replay

    owner = owner_digest("dispatcher-1")
    claim = registry.claim_next(1, 1, owner, 1_000)
    assert claim is not None
    assert claim.attempt.attempt_no == 1
    assert claim.lease_epoch == 1
    with pytest.raises(StaleAttemptFence):
        registry.heartbeat(claim.attempt, 1, 1, 1, owner_digest("stale"), 1_000)
    clock.advance(100)
    lease = registry.heartbeat(claim.attempt, 1, 1, 1, owner, 1_000)
    assert lease.heartbeat_at_unix_ms == clock.now

    result_json = canonical_json_bytes({"ok": True}).decode()
    terminal = registry.transition_claimed(
        claim.attempt,
        1,
        1,
        1,
        owner,
        state=OperationState.SUCCEEDED,
        result_json=result_json,
        note="execution_succeeded",
    )
    assert terminal.state is OperationState.SUCCEEDED
    assert terminal.result_json == result_json
    assert registry.finish_attempt(claim.attempt, 1, 1, 1, owner) == terminal
    snapshot = registry.continuity_snapshot(record.operation)
    assert snapshot.current_attempt is None
    assert snapshot.last_attempt == claim.attempt
    registry.close()


def test_cancellation_control_is_idempotent_and_survives_reopen(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "cancel.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    requester = owner_digest("requester")
    control = registry.request_cancel(record.operation, requester, "user_requested")
    events_after_first = len(registry.events(record.operation))
    assert control.cancellation_requested is True
    registry.request_cancel(record.operation, requester, "user_requested")
    assert len(registry.events(record.operation)) == events_after_first
    registry.close()

    reopened = OperationRegistry(database, clock)
    assert reopened.cancellation_requested(record.operation) is True
    assert reopened.continuity_snapshot(record.operation).control.cancellation_requested is True
    reopened.close()


def test_checkpoint_binding_is_attempt_and_environment_bound(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "checkpoint.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    owner = owner_digest("checkpoint")
    claim = registry.claim_next(1, 1, owner, 1_000)
    assert claim is not None
    checkpoint = ArtifactReference(
        artifact=ArtifactIdRef(value="artifact-checkpoint-1"),
        digest=canonical_sha256({"checkpoint": 1}),
        media_type="application/json",
        size_bytes=2,
        created_by=record.operation,
        redacted=False,
    )
    environment_digest = canonical_sha256({"environment": "test"})
    binding = registry.bind_checkpoint(
        claim.attempt,
        1,
        1,
        1,
        owner,
        checkpoint,
        environment_digest,
    )
    assert binding.checkpoint == checkpoint
    assert binding.environment_digest == environment_digest
    event_count = len(registry.events(record.operation))
    assert (
        registry.bind_checkpoint(
            claim.attempt,
            1,
            1,
            1,
            owner,
            checkpoint,
            environment_digest,
        )
        == binding
    )
    assert len(registry.events(record.operation)) == event_count
    assert registry.checkpoint_bindings(record.operation) == (binding,)
    registry.close()


def test_successor_attempt_rejects_late_attempt_one_writer(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "successor.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    owner = owner_digest("dispatcher")
    first = registry.claim_next(1, 1, owner, 1_000)
    assert first is not None
    registry.park_attempt(first.attempt, 1, 1, 1, owner, note="simulated_process_loss")
    registry.requeue_indeterminate(
        record.operation,
        1,
        reason_code="safe_rlm_replay",
        input_digest=record.input_digest,
    )
    second = registry.claim_next(1, 2, owner, 1_000)
    assert second is not None
    assert second.attempt.attempt_no == 2
    assert second.lease_epoch == 2
    with pytest.raises(StaleAttemptFence):
        registry.heartbeat(first.attempt, 1, 1, 1, owner, 1_000)
    registry.close()


def test_event_cursor_pages_have_no_gaps_duplicates_or_read_writes(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "events.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    owner = owner_digest("events")
    claim = registry.claim_next(1, 1, owner, 1_000)
    assert claim is not None
    registry.request_cancel(record.operation, owner_digest("requester"), "user_requested")
    registry.transition_claimed(
        claim.attempt,
        1,
        1,
        1,
        owner,
        state=OperationState.CANCELLED,
        result_json=canonical_json_bytes({"cancelled": True}).decode(),
        note="cancelled",
    )
    expected = [event.sequence for event in registry.events(record.operation)]
    operation_count = table_count(database, "operations")
    event_count = table_count(database, "operation_events")

    cursor = 0
    seen: list[int] = []
    terminal_snapshot = None
    while True:
        page = registry.event_page(record.operation, after_sequence=cursor, limit=2)
        seen.extend(event.sequence for event in page.events)
        cursor = page.next_sequence
        terminal_snapshot = page.terminal_snapshot
        if not page.has_more:
            break
    assert seen == expected
    assert len(seen) == len(set(seen))
    assert terminal_snapshot is not None
    assert terminal_snapshot.operation_state is OperationState.CANCELLED
    assert table_count(database, "operations") == operation_count
    assert table_count(database, "operation_events") == event_count
    registry.close()


def test_deadline_before_claim_times_out_without_creating_attempt(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "deadline.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock, deadline_delta_ms=1)
    clock.advance(1)
    claim = registry.claim_next(1, 1, owner_digest("deadline"), 1_000)
    assert claim is None
    assert registry.get(record.operation).state is OperationState.TIMED_OUT
    assert table_count(database, "operation_attempts") == 0
    registry.close()


def test_restart_and_expired_lease_fence_old_writer_before_successor(tmp_path: Path) -> None:
    clock = ManualClock()
    database = tmp_path / "restart.sqlite3"
    registry = open_registry(database, clock)
    record = accept_dispatch(registry, clock)
    owner = owner_digest("restart")
    first = registry.claim_next(1, 1, owner, 100)
    assert first is not None
    clock.advance(100)
    fenced = registry.fence_expired_attempts(1)
    assert [item.operation for item in fenced] == [record.operation]
    with pytest.raises(StaleAttemptFence):
        registry.heartbeat(first.attempt, 1, 1, 1, owner, 100)
    registry.requeue_indeterminate(record.operation, 1, reason_code="lease_expired_safe_replay")
    second = registry.claim_next(1, 2, owner, 100)
    assert second is not None
    assert second.attempt.attempt_no == 2
    registry.close()

    reopened = OperationRegistry(database, clock)
    assert reopened.start_runtime() == 2
    assert reopened.get(record.operation).state is OperationState.INDETERMINATE
    with pytest.raises(StaleAttemptFence):
        reopened.heartbeat(second.attempt, 1, 2, 2, owner, 100)
    assert [item.operation for item in reopened.list_recovery_candidates()] == [record.operation]
    reopened.close()


def test_supervisor_and_worker_receipts_are_exactly_generation_fenced(tmp_path: Path) -> None:
    clock = ManualClock()
    registry = OperationRegistry(tmp_path / "supervisor.sqlite3", clock)
    generation = registry.start_runtime()

    ready = registry.record_supervisor_ready(
        runtime_generation=generation,
        dispatcher_generation=generation,
        pid=1234,
        process_start_identity="linux:test-boot:55",
        capability_digest=canonical_sha256({"capabilities": 1}),
        runtime_home_digest=canonical_sha256({"runtime_home": "test"}),
        endpoint_kind="unix",
        discovery_digest=canonical_sha256({"discovery": 1}),
    )
    assert ready.state == "ready"
    with pytest.raises(InvalidTransition):
        registry.transition_supervisor_run(
            runtime_generation=generation,
            pid=1234,
            process_start_identity="linux:test-boot:55",
            state="stopped",
        )
    draining = registry.transition_supervisor_run(
        runtime_generation=generation,
        pid=1234,
        process_start_identity="linux:test-boot:55",
        state="draining",
    )
    assert draining.draining_at_unix_ms == clock.now

    worker = registry.register_worker_binding(
        worker_id="worker-ipython-1",
        runtime_generation=generation,
        worker_kind="ipython",
        workspace_id="workspace-test",
        workspace_generation=1,
        pid=5678,
        process_start_identity="linux:test-boot:77",
        capability_digest=canonical_sha256({"worker": "ipython"}),
        environment_digest=canonical_sha256({"environment": "test"}),
    )
    assert worker.state == "ready"
    busy = registry.heartbeat_worker_binding(
        worker_id=worker.worker_id,
        runtime_generation=generation,
        process_start_identity=worker.process_start_identity,
        state="busy",
        operation_id="op-test",
        last_event_sequence=2,
    )
    assert busy.operation_id == "op-test"
    with pytest.raises(StaleAttemptFence):
        registry.heartbeat_worker_binding(
            worker_id=worker.worker_id,
            runtime_generation=generation,
            process_start_identity=worker.process_start_identity,
            state="busy",
            last_event_sequence=1,
        )
    terminal = registry.finish_worker_binding(
        worker_id=worker.worker_id,
        runtime_generation=generation,
        process_start_identity=worker.process_start_identity,
        state="terminated",
        termination_receipt_json='{"reason":"supervisor-drain"}',
    )
    assert terminal.state == "terminated"
    assert registry.active_worker_bindings() == ()

    stopped = registry.transition_supervisor_run(
        runtime_generation=generation,
        pid=1234,
        process_start_identity="linux:test-boot:55",
        state="stopped",
        terminal_reason="clean_shutdown",
    )
    assert stopped.terminal_reason == "clean_shutdown"
    with pytest.raises(StaleRuntimeGeneration):
        registry.register_worker_binding(
            worker_id="worker-stale",
            runtime_generation=generation + 1,
            worker_kind="ipython",
            workspace_id="workspace-stale",
            workspace_generation=1,
            pid=9999,
            process_start_identity="linux:test-boot:99",
            capability_digest=canonical_sha256({"worker": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )
    registry.close()
