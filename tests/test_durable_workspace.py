from __future__ import annotations

from pathlib import Path
from typing import Literal, overload

import pytest

from aar.canonical import canonical_json_bytes
from aar.runtime.continuity import (
    WORKSPACE_PROGRAM_OPERATION_KIND,
    workspace_checkpoint_boundary_policy,
    workspace_recovery_environment_digest,
)
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.workspace import StaleWorkspaceGeneration
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceProgramResult,
    WorkspaceProgramSpec,
)
from aar.schemas import OperationRef, OperationState, PrincipalRef, SessionRef, WorkspaceRef

NOW_MS = 1_700_000_000_000


def open_host(
    database: Path,
    *,
    durable: bool,
    backend: str = "plain",
) -> ReferenceHost:
    return ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend=backend,
        enable_durable_dispatch=durable,
        dispatcher_concurrency=1,
    )


def test_durable_workspace_execute_uses_attempt_and_kind_router(tmp_path: Path) -> None:
    host = open_host(tmp_path / "workspace.sqlite3", durable=True)
    session = SessionRef(value="session-durable-workspace")
    handle = host.program_workspace.create(
        WorkspaceRef(value="workspace-durable"),
        session,
    )
    spec = WorkspaceProgramSpec(code="answer = 6 * 7\nanswer")
    envelope = host.request_program_workspace_envelope(
        request_id="request-durable-workspace",
        idempotency_key="idempotency-durable-workspace",
        principal=PrincipalRef(value="principal-durable-workspace"),
        session=session,
        handle=handle,
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
    )
    try:
        accepted = host.submit_program_workspace_durable(envelope, handle, spec)
        completed = host.wait_program_workspace(accepted.operation, timeout_s=2)

        assert completed.state is OperationState.SUCCEEDED
        result = WorkspaceProgramResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert result.result == 42
        snapshot = host.registry.continuity_snapshot(accepted.operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 1
        assert snapshot.dispatcher_state == "completed"
    finally:
        host.close()


def catalog_checkpoint(host: ReferenceHost, handle, session: SessionRef):
    marker_spec = WorkspaceProgramSpec(code="0")
    envelope = host.request_program_workspace_envelope(
        request_id="request-workspace-checkpoint",
        idempotency_key="idempotency-workspace-checkpoint",
        principal=PrincipalRef(value="principal-durable-workspace"),
        session=session,
        handle=handle,
        spec=marker_spec,
        deadline_unix_ms=NOW_MS + 60_000,
    )
    payload = {
        "handle": handle,
        "kind": "workspace.program.checkpoint",
        "policy": WorkspaceCheckpointPolicy(),
    }
    record, _created = host.registry.accept(
        envelope, canonical_json_bytes(payload).decode()
    )
    host.registry.begin(record.operation, host.runtime_generation)
    manifest = host.program_workspace.checkpoint(
        record.operation,
        handle,
        WorkspaceCheckpointPolicy(),
        envelope.trace_id,
    )
    host.registry.succeed(
        record.operation,
        canonical_json_bytes(manifest).decode(),
        host.runtime_generation,
    )
    host.registry.record_workspace_checkpoint(manifest)
    return manifest


@overload
def prepare_lost_workspace_operation(
    database: Path,
    *,
    backend: str = "plain",
    real_worker_loss: bool = False,
    keep_host: Literal[False] = False,
) -> tuple[
    SessionRef,
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    OperationRef,
]: ...


@overload
def prepare_lost_workspace_operation(
    database: Path,
    *,
    backend: str = "plain",
    real_worker_loss: bool = False,
    keep_host: Literal[True],
) -> tuple[
    SessionRef,
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    OperationRef,
    ReferenceHost,
]: ...


def prepare_lost_workspace_operation(
    database: Path,
    *,
    backend: str = "plain",
    real_worker_loss: bool = False,
    keep_host: bool = False,
) -> tuple[
    SessionRef,
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    OperationRef,
] | tuple[
    SessionRef,
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    OperationRef,
    ReferenceHost,
]:
    session = SessionRef(value="session-workspace-recovery")
    host1 = open_host(database, durable=False, backend=backend)
    handle = host1.program_workspace.create(
        WorkspaceRef(value="workspace-recovery"),
        session,
    )
    setup = host1.program_workspace.execute(
        OperationRef(value="op-workspace-setup"),
        handle,
        WorkspaceProgramSpec(code="answer = 41\nanswer"),
    )
    handle = host1.program_workspace.attach(
        type(handle)(
            workspace=handle.workspace,
            backend=handle.backend,
            generation=handle.generation,
            revision=setup.revision_after,
        ),
        session,
    )
    manifest = catalog_checkpoint(host1, handle, session)
    assert not manifest.exclusions

    spec = WorkspaceProgramSpec(
        code="answer += 1\nanswer",
        checkpoint_replay_safe=True,
    )
    envelope = host1.request_program_workspace_envelope(
        request_id="request-workspace-recovery",
        idempotency_key="idempotency-workspace-recovery",
        principal=PrincipalRef(value="principal-durable-workspace"),
        session=session,
        handle=handle,
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
    )
    payload = {
        "handle": handle,
        "kind": WORKSPACE_PROGRAM_OPERATION_KIND,
        "spec": spec,
    }
    accepted, _created = host1.registry.accept(
        envelope, canonical_json_bytes(payload).decode()
    )
    policy = workspace_checkpoint_boundary_policy()
    host1.registry.bind_recovery_policy(
        accepted.operation,
        operation_kind=WORKSPACE_PROGRAM_OPERATION_KIND,
        policy=policy,
        environment_digest=workspace_recovery_environment_digest(
            backend=host1.program_workspace.descriptor,
            environment=host1.program_workspace.environment,
            capability_digest=host1.capabilities.digest,
            policy=policy,
        ),
    )
    host1.registry.request_dispatch(
        accepted.operation,
        kind=WORKSPACE_PROGRAM_OPERATION_KIND,
    )
    claim = host1.registry.claim_next(
        host1.runtime_generation,
        77,
        "sha256:" + "1" * 64,
        30_000,
    )
    assert claim is not None
    original_execute = host1.program_workspace.execute

    def lose_worker(operation, current_handle, _spec):
        if real_worker_loss:
            return original_execute(
                operation,
                current_handle,
                WorkspaceProgramSpec(code="import os\nos._exit(17)"),
            )
        return WorkspaceProgramResult(
            operation=operation,
            workspace=current_handle.workspace,
            backend=current_handle.backend,
            generation=current_handle.generation,
            revision_before=current_handle.revision,
            revision_after=current_handle.revision + 1,
            status="failed",
            workspace_lost=True,
            result_excluded_reason="worker_lost",
            events=(),
        )

    host1.program_workspace.execute = lose_worker  # type: ignore[method-assign]
    fence = AttemptFence(
        dispatcher_generation=claim.dispatcher_generation,
        lease_epoch=claim.lease_epoch,
        owner_digest=claim.owner_digest,
    )
    lost = host1.run_claimed_program_workspace(claim.attempt.operation, claim.attempt, fence)
    assert lost.state is OperationState.INDETERMINATE
    host1.registry.finish_attempt(
        claim.attempt,
        host1.runtime_generation,
        fence.dispatcher_generation,
        fence.lease_epoch,
        fence.owner_digest,
    )
    host1.program_workspace.execute = original_execute  # type: ignore[method-assign]
    operation = accepted.operation
    if keep_host:
        return session, handle, manifest, operation, host1
    host1.close()
    return session, handle, manifest, operation


def test_worker_loss_restores_checkpoint_into_successor_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workspace-recovery.sqlite3"
    session, handle, manifest, operation = prepare_lost_workspace_operation(database)

    host2 = open_host(database, durable=True)
    try:
        completed = host2.wait_program_workspace(operation, timeout_s=3)
        assert completed.state is OperationState.SUCCEEDED
        result = WorkspaceProgramResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert result.result == 42
        assert result.generation == handle.generation + 1
        boundaries = host2.registry.workspace_checkpoint_boundaries(operation)
        assert len(boundaries) == 1
        assert boundaries[0].checkpoint_manifest_digest == manifest.content_digest
        assert boundaries[0].restored_handle.generation == handle.generation + 1
        notes = [event.note for event in host2.registry.events(operation)]
        assert notes.index("checkpoint_selected") < notes.index("checkpoint_restored")
        assert notes.index("checkpoint_restored") < notes.index("successor_started")
        snapshot = host2.registry.continuity_snapshot(operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
        with pytest.raises(StaleWorkspaceGeneration):
            host2.program_workspace.attach(handle, session)
    finally:
        host2.close()


def test_real_subprocess_worker_loss_restores_and_runs_one_successor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ipython-workspace-recovery.sqlite3"
    session, handle, manifest, operation = prepare_lost_workspace_operation(
        database,
        backend="ipython",
        real_worker_loss=True,
    )

    recovered = open_host(database, durable=True, backend="ipython")
    try:
        completed = recovered.wait_program_workspace(operation, timeout_s=10)
        assert completed.state is OperationState.SUCCEEDED
        result = WorkspaceProgramResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert result.result == 42
        assert result.generation == handle.generation + 1
        boundaries = recovered.registry.workspace_checkpoint_boundaries(operation)
        assert len(boundaries) == 1
        assert boundaries[0].checkpoint_manifest_digest == manifest.content_digest
        notes = [event.note for event in recovered.registry.events(operation)]
        assert notes.count("checkpoint_selected") == 1
        assert notes.count("checkpoint_restored") == 1
        assert notes.count("successor_started") == 1
        assert notes.index("checkpoint_selected") < notes.index("checkpoint_restored")
        assert notes.index("checkpoint_restored") < notes.index("successor_started")
        snapshot = recovered.registry.continuity_snapshot(operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
        with pytest.raises(StaleWorkspaceGeneration):
            recovered.program_workspace.attach(handle, session)
    finally:
        recovered.close()


def test_real_worker_loss_recovers_on_same_live_host_without_receipt_conflict(
    tmp_path: Path,
) -> None:
    database = tmp_path / "ipython-workspace-same-host-recovery.sqlite3"
    session, handle, manifest, operation, host = prepare_lost_workspace_operation(
        database,
        backend="ipython",
        real_worker_loss=True,
        keep_host=True,
    )

    try:
        host.start_durable_dispatch()
        completed = host.wait_program_workspace(operation, timeout_s=10)
        assert completed.state is OperationState.SUCCEEDED
        result = WorkspaceProgramResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert result.result == 42
        assert result.generation == handle.generation + 1
        boundaries = host.registry.workspace_checkpoint_boundaries(operation)
        assert len(boundaries) == 1
        assert boundaries[0].checkpoint_manifest_digest == manifest.content_digest
        notes = [event.note for event in host.registry.events(operation)]
        assert notes.count("checkpoint_selected") == 1
        assert notes.count("checkpoint_restored") == 1
        assert notes.count("successor_started") == 1
        with pytest.raises(StaleWorkspaceGeneration):
            host.program_workspace.attach(handle, session)
    finally:
        host.close()


def test_restart_after_restore_before_admission_reuses_selection_and_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "workspace-restore-gap.sqlite3"
    _session, handle, manifest, operation = prepare_lost_workspace_operation(database)
    recovering = open_host(database, durable=False)
    original_mark = recovering.registry.mark_workspace_checkpoint_restored

    class SimulatedRecoveryProcessLoss(BaseException):
        pass

    def lose_after_restore(*_args, **_kwargs):
        raise SimulatedRecoveryProcessLoss()

    recovering.registry.mark_workspace_checkpoint_restored = (  # type: ignore[method-assign]
        lose_after_restore
    )
    with pytest.raises(SimulatedRecoveryProcessLoss):
        recovering.recover_durable_workspaces()
    selection = recovering.registry.workspace_checkpoint_selection(operation, 1)
    assert selection is not None
    assert selection.checkpoint_manifest_digest == manifest.content_digest
    assert not recovering.registry.workspace_checkpoint_boundaries(operation)
    notes = [event.note for event in recovering.registry.events(operation)]
    assert notes.count("checkpoint_selected") == 1
    assert "checkpoint_restored" not in notes
    assert "successor_started" not in notes
    recovering.registry.mark_workspace_checkpoint_restored = (  # type: ignore[method-assign]
        original_mark
    )
    recovering.close()

    resumed = open_host(database, durable=True)
    try:
        completed = resumed.wait_program_workspace(operation, timeout_s=3)
        assert completed.state is OperationState.SUCCEEDED
        result = WorkspaceProgramResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert result.result == 42
        assert result.generation == handle.generation + 1
        boundaries = resumed.registry.workspace_checkpoint_boundaries(operation)
        assert len(boundaries) == 1
        assert boundaries[0].restored_handle.generation == handle.generation + 1
        notes = [event.note for event in resumed.registry.events(operation)]
        assert notes.count("checkpoint_selected") == 1
        assert notes.count("checkpoint_restored") == 1
        assert notes.count("successor_started") == 1
        snapshot = resumed.registry.continuity_snapshot(operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
    finally:
        resumed.close()
