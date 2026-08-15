from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from aar.runtime.brokers import FakeArtifactBroker
from aar.runtime.ipython_backend import (
    SupervisedIPythonWorkspaceBackend,
    WorkspaceWorkerLost,
)
from aar.runtime.programming import PlainPythonWorkspaceBackend, WorkspaceBackend
from aar.runtime.registry import OperationRegistry
from aar.runtime.worker_manager import WorkerManager
from aar.runtime.workspace import StaleWorkspaceGeneration
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointPolicy,
    WorkspaceProgramSpec,
    WorkspaceRestoreSpec,
)
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


def refs(suffix: str) -> tuple[WorkspaceRef, SessionRef]:
    return WorkspaceRef(value=f"workspace-ipython-{suffix}"), SessionRef(
        value=f"session-ipython-{suffix}"
    )


def current(result) -> ProgrammableWorkspaceHandle:
    return ProgrammableWorkspaceHandle(
        workspace=result.workspace,
        backend=result.backend,
        generation=result.generation,
        revision=result.revision_after,
    )


def test_worker_terminate_treats_lookup_race_as_exit_and_reaps() -> None:
    events: list[str] = []

    class ExitedDuringTerminate:
        returncode: int | None = None

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            events.append("terminate-missing")
            raise ProcessLookupError("worker exited before terminate")

        def kill(self) -> None:
            events.append("kill")

        def wait(self, timeout: float | None = None) -> int:
            events.append("wait")
            self.returncode = 0
            return self.returncode

    process = ExitedDuringTerminate()

    SupervisedIPythonWorkspaceBackend._terminate(process)  # type: ignore[arg-type]

    assert events == ["terminate-missing", "wait"]
    assert process.returncode == 0


def test_supervised_ipython_executes_real_cells_and_persists_namespace() -> None:
    artifacts = FakeArtifactBroker()
    backend = SupervisedIPythonWorkspaceBackend(artifact_sink=artifacts.put)
    assert isinstance(backend, WorkspaceBackend)
    workspace, session = refs("execute")
    handle = backend.create(workspace, session)
    latest_handle = handle
    try:
        first = backend.execute(
            OperationRef(value="operation-ipython-first"),
            handle,
            WorkspaceProgramSpec(
                code=(
                    "import IPython\n"
                    "from IPython.display import display\n"
                    "answer = 6 * 7\n"
                    "print('hello from IPython')\n"
                    "display({'native': True})\n"
                    "aar_display({'answer': answer})\n"
                    "aar_artifact('answer.txt', str(answer), 'text/plain')\n"
                    "{'answer': answer, 'ipython': IPython.__version__}"
                )
            ),
        )
        assert first.status == "succeeded"
        assert first.workspace_lost is False
        assert first.result["answer"] == 42
        assert first.result["ipython"] == "9.16.1"
        assert {event.kind for event in first.events} >= {
            "stdout",
            "display",
            "artifact",
            "progress",
        }
        artifact = next(event.artifact for event in first.events if event.kind == "artifact")
        assert artifact is not None
        assert artifacts.read(artifact) == b"42"

        second_handle = current(first)
        latest_handle = second_handle
        second = backend.execute(
            OperationRef(value="operation-ipython-second"),
            second_handle,
            WorkspaceProgramSpec(code="answer += 1\nanswer"),
        )
        latest_handle = current(second)
        assert second.result == 43
        snapshot = backend.inspect(latest_handle)
        variables = {item.name: item for item in snapshot.variables}
        assert variables["answer"].preview == "43"
        assert variables["IPython"].portable is False
    finally:
        if backend.health(latest_handle).status != "lost":
            backend.close(latest_handle, reason="test complete")


def test_ipython_checkpoint_restores_only_explicit_portable_values(monkeypatch) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("restore")
    handle = backend.create(workspace, session)
    executed = backend.execute(
        OperationRef(value="operation-ipython-checkpoint"),
        handle,
        WorkspaceProgramSpec(
            code=(
                "portable = {'answer': 42, 'items': [True, None]}\n"
                "import math\n"
                "portable"
            )
        ),
    )
    checkpoint_handle = current(executed)
    manifest = backend.checkpoint(
        OperationRef(value="operation-ipython-checkpoint-create"),
        checkpoint_handle,
        WorkspaceCheckpointPolicy(),
        "trace-ipython-checkpoint-create",
    )

    assert [item.name for item in manifest.values] == ["portable"]
    assert [item.name for item in manifest.exclusions] == ["math"]

    original_spawn = backend._spawn

    def fail_spawn(*_args, **_kwargs):
        raise RuntimeError("simulated spawn failure")

    monkeypatch.setattr(backend, "_spawn", fail_spawn)
    with pytest.raises(RuntimeError, match="simulated spawn failure"):
        backend.restore(
            manifest,
            WorkspaceRestoreSpec(
                workspace=workspace,
                session=session,
                expected_handle=checkpoint_handle,
            ),
        )
    assert backend.health(checkpoint_handle).status == "ready"
    assert {item.name for item in backend.inspect(checkpoint_handle).variables} >= {"portable"}
    monkeypatch.setattr(backend, "_spawn", original_spawn)

    restored = backend.restore(
        manifest,
        WorkspaceRestoreSpec(
            workspace=workspace,
            session=session,
            expected_handle=checkpoint_handle,
        ),
    )
    try:
        assert restored.generation == 2
        assert restored.revision == 0
        assert {item.name for item in backend.inspect(restored).variables} == {"portable"}
        with pytest.raises(StaleWorkspaceGeneration):
            backend.inspect(checkpoint_handle)
    finally:
        backend.close(restored, reason="test complete")


def test_managed_checkpoint_restore_stages_successor_before_binding_handoff(
    tmp_path: Path,
) -> None:
    registry = OperationRegistry(tmp_path / "managed-restore.sqlite3", lambda: 1_700_000_000_000)
    runtime_generation = registry.start_runtime()
    manager = WorkerManager(
        registry,
        runtime_generation=runtime_generation,
        now_ms=lambda: 1_700_000_000_000,
    )
    backend = SupervisedIPythonWorkspaceBackend(worker_manager=manager)
    workspace, session = refs("managed-restore")
    restored = None
    try:
        handle = backend.create(workspace, session)
        executed = backend.execute(
            OperationRef(value="operation-managed-restore-execute"),
            handle,
            WorkspaceProgramSpec(code="answer = 42\nanswer"),
        )
        checkpoint_handle = current(executed)
        manifest = backend.checkpoint(
            OperationRef(value="operation-managed-restore-checkpoint"),
            checkpoint_handle,
            WorkspaceCheckpointPolicy(),
            "trace-managed-restore-checkpoint",
        )

        restored = backend.restore(
            manifest,
            WorkspaceRestoreSpec(
                workspace=workspace,
                session=session,
                expected_handle=checkpoint_handle,
            ),
        )

        assert restored.generation == 2
        active = registry.active_worker_bindings()
        assert len(active) == 1
        assert active[0].workspace_id == workspace.value
        assert active[0].workspace_generation == 2
    finally:
        if restored is not None:
            backend.close(restored, reason="test complete")
        else:
            backend.shutdown()
        registry.close()


def test_json_subset_checkpoint_restores_across_backend_kinds() -> None:
    plain = PlainPythonWorkspaceBackend()
    source_workspace = WorkspaceRef(value="workspace-plain-cross-backend")
    session = SessionRef(value="session-cross-backend")
    source = plain.create(source_workspace, session)
    executed = plain.execute(
        OperationRef(value="operation-cross-backend-execute"),
        source,
        WorkspaceProgramSpec(code="portable = {'answer': 42}\nportable"),
    )
    source_handle = current(executed)
    manifest = plain.checkpoint(
        OperationRef(value="operation-cross-backend-checkpoint"),
        source_handle,
        WorkspaceCheckpointPolicy(),
        "trace-cross-backend-checkpoint",
    )

    ipython = SupervisedIPythonWorkspaceBackend()
    target_workspace = WorkspaceRef(value="workspace-ipython-cross-backend")
    restored = ipython.restore(
        manifest,
        WorkspaceRestoreSpec(workspace=target_workspace, session=session),
    )
    try:
        assert manifest.source_handle.backend.kind == "plain-python"
        assert restored.backend.kind == "ipython"
        variables = {item.name: item for item in ipython.inspect(restored).variables}
        assert variables["portable"].preview == '{"answer":42}'
    finally:
        ipython.close(restored, reason="test complete")


def test_ipython_timeout_classifies_worker_loss_and_checkpoint_restores() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("timeout")
    handle = backend.create(workspace, session)
    manifest = backend.checkpoint(
        OperationRef(value="operation-ipython-timeout-checkpoint"),
        handle,
        WorkspaceCheckpointPolicy(),
        "trace-ipython-timeout-checkpoint",
    )
    timed_out = backend.execute(
        OperationRef(value="operation-ipython-timeout"),
        handle,
        WorkspaceProgramSpec(code="while True:\n    pass", wall_time_ms=50),
    )

    assert timed_out.status == "timed_out"
    assert timed_out.workspace_lost is True
    lost_handle = current(timed_out)
    assert backend.health(lost_handle).status == "lost"
    with pytest.raises(WorkspaceWorkerLost):
        backend.checkpoint(
            OperationRef(value="operation-ipython-lost-checkpoint"),
            lost_handle,
            WorkspaceCheckpointPolicy(),
            "trace-ipython-lost-checkpoint",
        )

    restored = backend.restore(
        manifest,
        WorkspaceRestoreSpec(
            workspace=workspace,
            session=session,
            expected_handle=lost_handle,
        ),
    )
    try:
        assert restored.generation == 2
        assert backend.health(restored).status == "ready"
    finally:
        backend.close(restored, reason="test complete")


def test_ipython_interrupt_terminates_worker_and_returns_loss_receipt() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("interrupt")
    handle = backend.create(workspace, session)
    operation = OperationRef(value="operation-ipython-interrupt")
    captured: list = []

    def run() -> None:
        captured.append(
            backend.execute(
                operation,
                handle,
                WorkspaceProgramSpec(code="import time\ntime.sleep(60)"),
            )
        )

    thread = threading.Thread(target=run)
    thread.start()
    deadline = time.monotonic() + 5
    while backend.health(handle).status != "busy" and time.monotonic() < deadline:
        time.sleep(0.01)
    interrupted = backend.interrupt(handle, operation)
    thread.join(timeout=5)

    assert interrupted.accepted is True
    assert not thread.is_alive()
    assert captured[0].status == "interrupted"
    assert captured[0].workspace_lost is True
    updated = current(captured[0])
    assert backend.health(updated).status == "lost"
    assert backend.reconcile(updated, operation).result == captured[0]


def test_unexpected_ipython_process_exit_is_not_reported_as_success() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("crash")
    handle = backend.create(workspace, session)
    crashed = backend.execute(
        OperationRef(value="operation-ipython-crash"),
        handle,
        WorkspaceProgramSpec(code="import os\nos._exit(17)"),
    )
    assert crashed.status == "failed"
    assert crashed.workspace_lost is True
    exception = next(event for event in crashed.events if event.kind == "exception")
    assert exception.data["type"] == "WorkspaceWorkerLost"


def test_ipython_output_and_result_payloads_are_bounded_in_the_worker() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("bounded")
    handle = backend.create(workspace, session)
    result = backend.execute(
        OperationRef(value="operation-ipython-bounded"),
        handle,
        WorkspaceProgramSpec(
            code="print('x' * 100_000)\nlarge = 'y' * 70_000\nlarge",
            max_output_chars=1_024,
        ),
    )
    try:
        assert result.status == "succeeded"
        stdout = next(event for event in result.events if event.kind == "stdout")
        assert len(stdout.text) == 1_024
        assert stdout.truncated is True
        assert result.result is None
        assert result.result_excluded_reason == "portable result exceeds 65536 bytes"
    finally:
        backend.close(current(result), reason="test complete")
