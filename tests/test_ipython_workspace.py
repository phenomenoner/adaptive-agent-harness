from __future__ import annotations

import contextlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest

from aar.runtime import ipython_backend as ipython_backend_module
from aar.runtime.brokers import FakeArtifactBroker
from aar.runtime.ipython_backend import (
    SupervisedIPythonWorkspaceBackend,
    WorkspaceWorkerLost,
)
from aar.runtime.process_identity import ProcessStartIdentity
from aar.runtime.programming import (
    PlainPythonWorkspaceBackend,
    WorkspaceBackend,
    WorkspaceOperationConflict,
)
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
    process = _PidReuseWorker(999, terminal=True)
    state = ipython_backend_module._WorkerState(
        session=refs("already-terminal")[1],
        generation=1,
        process=process,  # type: ignore[arg-type]
        exact_child=_FakeExactChild(process),  # type: ignore[arg-type]
    )

    SupervisedIPythonWorkspaceBackend._terminate(state)

    assert process.exact_signal_count == 0
    assert process.exact_wait_count == 1
    assert process.terminal_receipt_count == 1
    assert process.wait_count == 1


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


class _PidReuseWorker:
    """A reaped child whose numeric PID now designates a foreign replacement."""

    def __init__(self, pid: int, *, terminal: bool = False) -> None:
        self.pid = pid
        self.returncode: int | None = 0 if terminal else None
        self.stdin = StringIO()
        self.stdout = StringIO()
        self.stderr = StringIO()
        self.replacement_signal_count = 0
        self.numeric_pid_reopen_count = 0
        self.wait_count = 0
        self.exact_signal_count = 0
        self.exact_wait_count = 0
        self.terminal_receipt_count = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.replacement_signal_count += 1
        self.returncode = -15

    def kill(self) -> None:
        self.replacement_signal_count += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_count += 1
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


class _FakeExactChild:
    def __init__(self, process: _PidReuseWorker) -> None:
        self.process = process
        self.identity = ProcessStartIdentity(
            pid=process.pid,
            platform="linux",
            start_time=max(1, process.pid),
            boot_id="test-boot",
        )
        self._lock = threading.Lock()
        self._receipt: object | None = None

    def terminalize(self, timeout_sec: float) -> object:
        assert timeout_sec > 0
        with self._lock:
            if self._receipt is not None:
                return self._receipt
            self.process.exact_wait_count += 1
            if self.process.returncode is None:
                self.process.exact_signal_count += 1
                self.process.returncode = -15
            self.process.wait(timeout=timeout_sec)
            self.process.terminal_receipt_count += 1
            self._receipt = object()
            return self._receipt

def _install_worker_state(
    backend: SupervisedIPythonWorkspaceBackend,
    workspace: WorkspaceRef,
    session: SessionRef,
    process: _PidReuseWorker,
    *,
    generation: int = 1,
):
    state = ipython_backend_module._WorkerState(
        session=session,
        generation=generation,
        process=process,
        exact_child=_FakeExactChild(process),  # type: ignore[arg-type]
    )
    backend._states[workspace.value] = state
    return state, backend._handle(workspace, state)


def _assert_no_numeric_replacement_signal(process: _PidReuseWorker, seam: str) -> None:
    assert process.replacement_signal_count == 0, (
        f"{seam} signalled the PID-reuse replacement through Popen instead of the retained "
        "exact child handle"
    )
    assert process.numeric_pid_reopen_count == 0
    assert process.exact_signal_count == 1, f"{seam} did not signal the retained handle"
    assert process.exact_wait_count == 1, f"{seam} did not wait through the retained handle"
    assert process.terminal_receipt_count == 1, f"{seam} did not publish one terminal result"


def _portable_manifest(session: SessionRef):
    plain = PlainPythonWorkspaceBackend()
    source = WorkspaceRef(value=f"manifest-source-{session.value}")
    handle = plain.create(source, session)
    executed = plain.execute(
        OperationRef(value=f"manifest-execute-{session.value}"),
        handle,
        WorkspaceProgramSpec(code="portable = {'answer': 42}\nportable"),
    )
    return plain.checkpoint(
        OperationRef(value=f"manifest-checkpoint-{session.value}"),
        current(executed),
        WorkspaceCheckpointPolicy(),
        f"trace-{session.value}",
    )


def test_interrupt_uses_exact_worker_handle_across_pid_reuse() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-interrupt")
    process = _PidReuseWorker(1_001)
    state, handle = _install_worker_state(backend, workspace, session, process)
    operation = OperationRef(value="operation-red-interrupt")
    state.running_operation = operation

    backend.interrupt(handle, operation)

    _assert_no_numeric_replacement_signal(process, "interrupt")


def test_operation_timeout_uses_exact_worker_handle_across_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-timeout")
    process = _PidReuseWorker(1_002)
    _state, handle = _install_worker_state(backend, workspace, session, process)

    def timeout(*_args, **_kwargs):
        raise ipython_backend_module._WorkerTimeout("injected worker deadline")

    monkeypatch.setattr(backend, "_read_response", timeout)
    backend.execute(
        OperationRef(value="operation-red-timeout"),
        handle,
        WorkspaceProgramSpec(code="pass", wall_time_ms=1),
    )

    _assert_no_numeric_replacement_signal(process, "operation timeout")


def test_worker_loss_uses_exact_worker_handle_across_pid_reuse() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-loss")
    process = _PidReuseWorker(1_003)
    state, _handle = _install_worker_state(backend, workspace, session, process)

    backend._mark_lost(state, "injected protocol loss")

    _assert_no_numeric_replacement_signal(process, "worker loss")


def test_close_uses_exact_worker_handle_across_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-close")
    process = _PidReuseWorker(1_004)
    _state, handle = _install_worker_state(backend, workspace, session, process)
    monkeypatch.setattr(backend, "_request", lambda *_args, **_kwargs: {"ok": True})

    backend.close(handle, reason="red exact-child close")

    _assert_no_numeric_replacement_signal(process, "workspace close")


def test_shutdown_serializes_exact_worker_terminalizers_across_pid_reuse() -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-shutdown")
    process = _PidReuseWorker(1_005)
    _state, _handle = _install_worker_state(backend, workspace, session, process)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(backend.shutdown) for _ in range(2)]
        for result in results:
            result.result(timeout=3)

    _assert_no_numeric_replacement_signal(process, "concurrent shutdown")


def test_spawn_failure_uses_exact_child_handle_across_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("red-spawn")
    process = _PidReuseWorker(1_006)
    monkeypatch.setattr(ipython_backend_module.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(
        ipython_backend_module,
        "bind_exact_child",
        lambda *_a, **_k: _FakeExactChild(process),
    )
    monkeypatch.setattr(
        backend,
        "_read_response",
        lambda *_a, **_k: (_ for _ in ()).throw(
            WorkspaceWorkerLost("injected spawn readiness failure")
        ),
    )

    with pytest.raises(WorkspaceWorkerLost, match="readiness failure"):
        backend.create(workspace, session)

    _assert_no_numeric_replacement_signal(process, "spawn failure")


def _restore_cleanup_case(
    monkeypatch: pytest.MonkeyPatch,
    *,
    phase: str,
) -> _PidReuseWorker:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs(f"red-{phase}")
    manifest = _portable_manifest(session)
    predecessor = _PidReuseWorker(1_010, terminal=phase in {"registration", "post-registration"})
    staged = _PidReuseWorker(1_011)
    expected_handle = None
    if phase != "none-insert":
        _current_state, expected_handle = _install_worker_state(
            backend, workspace, session, predecessor
        )

    def spawn(*_args, **_kwargs):
        return ipython_backend_module._WorkerState(
            session=session,
            generation=(expected_handle.generation + 1 if expected_handle else 1),
            process=staged,
            exact_child=_FakeExactChild(staged),  # type: ignore[arg-type]
        )

    monkeypatch.setattr(backend, "_spawn", spawn)

    def request(state, payload, **_kwargs):
        if phase == "hydration":
            raise RuntimeError("injected hydration failure")
        if phase == "existing-binding":
            competitor_process = _PidReuseWorker(1_012, terminal=True)
            competitor = ipython_backend_module._WorkerState(
                session=session,
                generation=99,
                process=competitor_process,
                exact_child=_FakeExactChild(competitor_process),  # type: ignore[arg-type]
            )
            backend._states[workspace.value] = competitor
        if phase == "none-insert":
            competitor_process = _PidReuseWorker(1_013, terminal=True)
            competitor = ipython_backend_module._WorkerState(
                session=session,
                generation=99,
                process=competitor_process,
                exact_child=_FakeExactChild(competitor_process),  # type: ignore[arg-type]
            )
            backend._states[workspace.value] = competitor
        return {"ok": True}

    monkeypatch.setattr(backend, "_request", request)
    if phase in {"registration", "post-registration"}:
        class _Manager:
            def finish(self, *_args, **_kwargs) -> None:
                return None

        backend._worker_manager = _Manager()  # type: ignore[assignment]

        def register(_workspace, state) -> None:
            if phase == "registration":
                raise RuntimeError("injected managed registration failure")
            competitor_process = _PidReuseWorker(1_014, terminal=True)
            competitor = ipython_backend_module._WorkerState(
                session=session,
                generation=99,
                process=competitor_process,
                exact_child=_FakeExactChild(competitor_process),  # type: ignore[arg-type]
            )
            backend._states[workspace.value] = competitor
            state.managed = object()  # type: ignore[assignment]

        monkeypatch.setattr(backend, "_register_managed", register)

    spec = WorkspaceRestoreSpec(
        workspace=workspace,
        session=session,
        expected_handle=expected_handle,
    )
    with contextlib.suppress(RuntimeError, WorkspaceOperationConflict):
        backend.restore(manifest, spec)
    return predecessor if phase == "predecessor" else staged


def test_restore_predecessor_handoff_uses_exact_handle_across_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="predecessor")
    _assert_no_numeric_replacement_signal(process, "restore predecessor handoff")


def test_restore_publishes_successor_only_after_predecessor_exact_terminal_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    workspace, session = refs("restore-publication-order")
    manifest = _portable_manifest(session)
    predecessor_process = _PidReuseWorker(1_020)
    predecessor, expected_handle = _install_worker_state(
        backend, workspace, session, predecessor_process
    )
    successor_process = _PidReuseWorker(1_021)
    successor = ipython_backend_module._WorkerState(
        session=session,
        generation=expected_handle.generation + 1,
        process=successor_process,
        exact_child=_FakeExactChild(successor_process),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(backend, "_spawn", lambda *_args, **_kwargs: successor)
    monkeypatch.setattr(backend, "_request", lambda *_args, **_kwargs: {"ok": True})

    original_terminalize = predecessor.exact_child.terminalize

    def terminalize(timeout_sec: float) -> object:
        assert backend._states[workspace.value] is predecessor, (
            "the successor became discoverable before exact predecessor terminal readback"
        )
        return original_terminalize(timeout_sec)

    monkeypatch.setattr(predecessor.exact_child, "terminalize", terminalize)

    restored = backend.restore(
        manifest,
        WorkspaceRestoreSpec(
            workspace=workspace,
            session=session,
            expected_handle=expected_handle,
        ),
    )

    assert restored.generation == successor.generation
    assert backend._states[workspace.value] is successor
    _assert_no_numeric_replacement_signal(
        predecessor_process, "restore predecessor publication order"
    )


def test_restore_hydration_failure_uses_exact_staged_handle_across_pid_reuse(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="hydration")
    _assert_no_numeric_replacement_signal(process, "restore hydration failure")


def test_restore_existing_binding_conflict_uses_exact_staged_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="existing-binding")
    _assert_no_numeric_replacement_signal(process, "restore existing-binding conflict")


def test_restore_none_insert_conflict_uses_exact_staged_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="none-insert")
    _assert_no_numeric_replacement_signal(process, "restore none-insert conflict")


def test_restore_registration_failure_uses_exact_staged_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="registration")
    _assert_no_numeric_replacement_signal(process, "restore registration failure")


def test_restore_post_registration_conflict_uses_exact_successor_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = _restore_cleanup_case(monkeypatch, phase="post-registration")
    _assert_no_numeric_replacement_signal(process, "restore post-registration conflict")
