from __future__ import annotations

import threading
import time

import pytest
from pydantic import ValidationError

from aar.runtime.brokers import FakeArtifactBroker
from aar.runtime.programming import (
    PlainPythonWorkspaceBackend,
    WorkspaceBackend,
    WorkspaceOperationConflict,
)
from aar.runtime.workspace import (
    StaleWorkspaceGeneration,
    WorkspaceRevisionConflict,
    WorkspaceSessionMismatch,
)
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceBackendDescriptor,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceProgramSpec,
    WorkspaceRestoreSpec,
)
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


def refs() -> tuple[WorkspaceRef, SessionRef]:
    return WorkspaceRef(value="workspace-programmable"), SessionRef(value="session-programmable")


def current(result) -> ProgrammableWorkspaceHandle:
    return ProgrammableWorkspaceHandle(
        workspace=result.workspace,
        backend=result.backend,
        generation=result.generation,
        revision=result.revision_after,
    )


def test_plain_backend_implements_shared_contract_and_emits_bounded_events() -> None:
    artifacts = FakeArtifactBroker()
    backend = PlainPythonWorkspaceBackend(artifact_sink=artifacts.put)
    assert isinstance(backend, WorkspaceBackend)
    workspace, session = refs()
    handle = backend.create(workspace, session)

    result = backend.execute(
        OperationRef(value="operation-program-1"),
        handle,
        WorkspaceProgramSpec(
            code=(
                "import sys\n"
                "answer = 6 * 7\n"
                "print('hello')\n"
                "print('warning', file=sys.stderr)\n"
                "aar_progress('half', completed=1, total=2)\n"
                "aar_display({'answer': answer})\n"
                "aar_artifact('answer.txt', str(answer), 'text/plain')\n"
                "answer"
            )
        ),
    )

    assert result.status == "succeeded"
    assert result.result == 42
    assert result.revision_before == 0
    assert result.revision_after == 1
    assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
    assert {event.kind for event in result.events} >= {
        "stdout",
        "stderr",
        "display",
        "progress",
        "artifact",
    }
    artifact = next(event.artifact for event in result.events if event.kind == "artifact")
    assert artifact is not None
    assert artifacts.read(artifact) == b"42"

    updated = current(result)
    snapshot = backend.inspect(updated)
    variables = {item.name: item for item in snapshot.variables}
    assert variables["answer"].preview == "42"
    assert variables["sys"].portable is False
    assert backend.health(updated).status == "ready"
    assert backend.attach(updated, session) == updated


def test_exception_is_structured_and_advances_revision_without_false_rollback() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    result = backend.execute(
        OperationRef(value="operation-program-failure"),
        handle,
        WorkspaceProgramSpec(code="before_failure = 7\nraise ValueError('broken')"),
    )

    assert result.status == "failed"
    exception = next(event for event in result.events if event.kind == "exception")
    assert exception.data["type"] == "ValueError"
    assert exception.data["message"] == "broken"
    updated = current(result)
    assert {item.name for item in backend.inspect(updated).variables} == {"before_failure"}
    with pytest.raises(WorkspaceRevisionConflict):
        backend.inspect(handle)


def test_checkpoint_is_deterministic_and_reports_unsupported_live_values() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    first = backend.create(workspace, session)
    executed = backend.execute(
        OperationRef(value="operation-checkpoint"),
        first,
        WorkspaceProgramSpec(
            code=(
                "portable = {'answer': 42, 'items': [True, None]}\n"
                "unsupported = object()\n"
                "not_portable = 1.5"
            )
        ),
    )
    handle = current(executed)
    policy = WorkspaceCheckpointPolicy()
    checkpoint_operation = OperationRef(value="operation-checkpoint-create")
    manifest = backend.checkpoint(
        checkpoint_operation, handle, policy, "trace-checkpoint-create"
    )
    repeated = backend.checkpoint(
        checkpoint_operation, handle, policy, "trace-checkpoint-create"
    )

    assert manifest == repeated
    assert manifest.source_handle == handle
    assert manifest.creation_operation == checkpoint_operation
    assert manifest.trace_id == "trace-checkpoint-create"
    assert manifest.environment.digest.startswith("sha256:")
    assert [item.name for item in manifest.values] == ["portable"]
    assert [item.name for item in manifest.exclusions] == ["not_portable", "unsupported"]
    reasons = {item.name: item.reason for item in manifest.exclusions}
    assert reasons == {
        "not_portable": "floating-point values are not portable",
        "unsupported": "unsupported portable value type: object",
    }
    assert WorkspaceCheckpointManifest.model_validate(
        manifest.model_dump(mode="python"), strict=True
    ) == manifest
    transported = manifest.model_dump(mode="json")
    assert isinstance(transported["values"], list)
    assert WorkspaceCheckpointManifest.model_validate(transported, strict=True) == manifest
    tampered = manifest.model_dump(mode="python")
    tampered["values"][0]["value"]["answer"] = 43
    with pytest.raises(ValidationError, match="checkpoint content digest mismatch"):
        WorkspaceCheckpointManifest.model_validate(tampered, strict=True)

    restored = backend.restore(
        manifest,
        WorkspaceRestoreSpec(
            workspace=workspace,
            session=session,
            expected_handle=handle,
        ),
    )
    assert restored.generation == 2
    assert restored.revision == 0
    names = {item.name for item in backend.inspect(restored).variables}
    assert names == {"portable"}
    with pytest.raises(StaleWorkspaceGeneration):
        backend.inspect(handle)
    with pytest.raises(WorkspaceOperationConflict):
        backend.execute(
            OperationRef(value="operation-checkpoint"),
            restored,
            WorkspaceProgramSpec(code="1"),
        )


def test_restore_and_attach_fail_closed_across_sessions_and_generations() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    manifest = backend.checkpoint(
        OperationRef(value="operation-restore-checkpoint"),
        handle,
        WorkspaceCheckpointPolicy(),
        "trace-restore-checkpoint",
    )

    with pytest.raises(ValidationError, match="must target the requested workspace"):
        WorkspaceRestoreSpec(
            workspace=WorkspaceRef(value="workspace-other"),
            session=session,
            expected_handle=handle,
        )

    with pytest.raises(WorkspaceSessionMismatch):
        backend.restore(
            manifest,
            WorkspaceRestoreSpec(
                workspace=workspace,
                session=SessionRef(value="session-other"),
                expected_handle=handle,
            ),
        )
    stale_handle = handle.model_copy(update={"generation": 2})
    with pytest.raises(StaleWorkspaceGeneration):
        backend.restore(
            manifest,
            WorkspaceRestoreSpec(
                workspace=workspace,
                session=session,
                expected_handle=stale_handle,
            ),
        )
    with pytest.raises(WorkspaceSessionMismatch):
        backend.attach(handle, SessionRef(value="session-other"))


def test_programmable_handle_rejects_a_valid_but_foreign_backend_descriptor() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    foreign_backend = WorkspaceBackendDescriptor.issue(
        kind="ipython",
        version="9.16.1",
        checkpoint_formats=("aar.workspace-checkpoint.v1",),
        features=("state.persistent",),
    )
    foreign_handle = ProgrammableWorkspaceHandle(
        workspace=handle.workspace,
        backend=foreign_backend,
        generation=handle.generation,
        revision=handle.revision,
    )

    with pytest.raises(WorkspaceRevisionConflict, match="backend capability identity"):
        backend.inspect(foreign_handle)


def test_host_interrupt_stops_cooperative_python_and_reconcile_returns_receipt() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    manifest = backend.checkpoint(
        OperationRef(value="operation-interrupt-checkpoint"),
        handle,
        WorkspaceCheckpointPolicy(),
        "trace-interrupt-checkpoint",
    )
    operation = OperationRef(value="operation-interrupt")
    captured: list = []

    def run() -> None:
        captured.append(
            backend.execute(
                operation,
                handle,
                WorkspaceProgramSpec(code="counter = 0\nwhile True:\n    counter += 1"),
            )
        )

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 2
    while backend.health(handle).status != "busy" and time.monotonic() < deadline:
        time.sleep(0.005)
    with pytest.raises(WorkspaceOperationConflict, match="operation is running"):
        backend.restore(
            manifest,
            WorkspaceRestoreSpec(
                workspace=workspace,
                session=session,
                expected_handle=handle,
            ),
        )
    interrupted = backend.interrupt(handle, operation)
    thread.join(timeout=2)

    assert interrupted.accepted is True
    assert not thread.is_alive()
    assert captured[0].status == "interrupted"
    updated = current(captured[0])
    reconciled = backend.reconcile(updated, operation)
    assert reconciled.state == "completed"
    assert reconciled.result == captured[0]


def test_timeout_close_and_lost_reconcile_are_explicit() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    timed_out = backend.execute(
        OperationRef(value="operation-timeout"),
        handle,
        WorkspaceProgramSpec(code="while True:\n    pass", wall_time_ms=10),
    )
    assert timed_out.status == "timed_out"
    updated = current(timed_out)
    lost = backend.reconcile(updated, OperationRef(value="operation-missing"))
    assert lost.state == "lost"
    with pytest.raises(ValidationError):
        backend.close(updated, reason="")
    assert backend.health(updated).status == "ready"
    closed = backend.close(updated, reason="test complete")
    assert closed.closed is True
    assert backend.health(updated).status == "closed"


def test_plain_backend_excludes_oversized_portable_result() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace, session = refs()
    handle = backend.create(workspace, session)
    result = backend.execute(
        OperationRef(value="operation-large-result"),
        handle,
        WorkspaceProgramSpec(code="large = 'x' * 70_000\nlarge"),
    )
    assert result.status == "succeeded"
    assert result.result is None
    assert result.result_excluded_reason == "portable result exceeds 65536 bytes"
