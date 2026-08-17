"""Separately supervised IPython implementation of the shared workspace contract."""

from __future__ import annotations

import base64
import contextlib
import importlib.metadata
import json
import queue
import sqlite3
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TextIO

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import WorkspaceBrokerFrame
from aar.runtime.process_identity import (
    ExactChild,
    ProcessIdentityMismatch,
    ProcessIdentityUnavailable,
    bind_exact_child,
)
from aar.runtime.programming import (
    ArtifactSink,
    WorkspaceCheckpointRejected,
    WorkspaceClosed,
    WorkspaceOperationConflict,
)
from aar.runtime.python_child import exact_module_command
from aar.runtime.sqlite_repository import SQLiteConnectionFactory
from aar.runtime.worker_manager import ManagedWorker, WorkerManager
from aar.runtime.workspace import (
    StaleWorkspaceGeneration,
    WorkspaceNotFound,
    WorkspaceRevisionConflict,
    WorkspaceSessionMismatch,
)
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    ProgrammableWorkspaceSnapshot,
    WorkspaceBackendDescriptor,
    WorkspaceCheckpointExclusion,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceCheckpointValue,
    WorkspaceCloseResult,
    WorkspaceEnvironmentFingerprint,
    WorkspaceEvent,
    WorkspaceHealth,
    WorkspaceInterruptResult,
    WorkspaceProgramResult,
    WorkspaceProgramSpec,
    WorkspaceReconciliationResult,
    WorkspaceRestoreSpec,
    WorkspaceVariableSummary,
)
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


class WorkspaceWorkerLost(WorkspaceClosed):
    pass


class WorkspaceWorkerProtocolError(WorkspaceClosed):
    pass


class _WorkerTimeout(TimeoutError):
    pass


@dataclass(slots=True)
class _WorkerState:
    session: SessionRef
    generation: int
    process: subprocess.Popen[str]
    exact_child: ExactChild
    managed: ManagedWorker | None = None
    managed_finished: bool = False
    managed_event_sequence: int = 0
    revision: int = 0
    closed: bool = False
    lost: bool = False
    lost_reason: str | None = None
    running_operation: OperationRef | None = None
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    lock: threading.RLock = field(default_factory=threading.RLock)
    io_lock: threading.Lock = field(default_factory=threading.Lock)


class SupervisedIPythonWorkspaceBackend:
    """One isolated IPython subprocess per workspace generation."""

    def __init__(
        self,
        *,
        artifact_sink: ArtifactSink | None = None,
        startup_timeout_s: float = 15.0,
        worker_manager: WorkerManager | None = None,
    ) -> None:
        self._artifact_sink = artifact_sink
        self._startup_timeout_s = startup_timeout_s
        self._worker_manager = worker_manager
        ipython_version = importlib.metadata.version("ipython")
        self._descriptor = WorkspaceBackendDescriptor.issue(
            kind="ipython",
            version=ipython_version,
            checkpoint_formats=("aar.workspace-checkpoint.v1",),
            features=(
                "checkpoint.json-subset",
                "events.rich-display",
                "events.structured",
                "interrupt.worker-terminating",
                "process.isolated-mode",
                "state.persistent",
                "timeout.worker-terminating",
            ),
        )
        self._environment = WorkspaceEnvironmentFingerprint.current(
            extra=(("ipython_version", ipython_version),)
        )
        self._states: dict[str, _WorkerState] = {}
        self._receipts: dict[str, WorkspaceProgramResult] = {}
        self._lock = threading.RLock()

    @property
    def descriptor(self) -> WorkspaceBackendDescriptor:
        return self._descriptor

    @property
    def environment(self) -> WorkspaceEnvironmentFingerprint:
        return self._environment

    def create(
        self, workspace: WorkspaceRef, session: SessionRef
    ) -> ProgrammableWorkspaceHandle:
        with self._lock:
            state = self._states.get(workspace.value)
            if state is None:
                state = self._spawn(workspace, session, generation=1)
                self._states[workspace.value] = state
            elif state.session != session:
                raise WorkspaceSessionMismatch("workspace is bound to another session")
            elif state.closed or state.lost:
                raise WorkspaceWorkerLost(
                    "workspace worker is unavailable; restore a checkpoint into a new generation"
                )
            return self._handle(workspace, state)

    def attach(
        self,
        handle: ProgrammableWorkspaceHandle,
        session: SessionRef,
    ) -> ProgrammableWorkspaceHandle:
        state = self._state(handle.workspace)
        if state.session != session:
            raise WorkspaceSessionMismatch("workspace is bound to another session")
        self._check_handle(state, handle)
        self._assert_available(state)
        return handle

    def execute(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
    ) -> WorkspaceProgramResult:
        with self._lock:
            prior = self._receipts.get(operation.value)
            if prior is not None:
                if (
                    prior.workspace != handle.workspace
                    or prior.generation != handle.generation
                    or prior.revision_before != handle.revision
                ):
                    raise WorkspaceOperationConflict(
                        "operation already belongs to another workspace generation or revision"
                    )
                return prior

        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            self._assert_available(state)
            if state.running_operation is not None:
                raise WorkspaceOperationConflict("another operation is already running")
            state.running_operation = operation
            state.cancel_requested.clear()
            self._managed_heartbeat(state)
            try:
                response = self._request(
                    state,
                    {
                        "command": "execute",
                        "code": spec.code,
                        "max_output_chars": spec.max_output_chars,
                        "max_events": spec.max_events,
                        "artifact_enabled": self._artifact_sink is not None,
                    },
                    timeout_s=(spec.wall_time_ms / 1_000) + 0.25,
                )
                try:
                    result = self._result_from_response(operation, handle, spec, response)
                except WorkspaceWorkerProtocolError:
                    raise
                except Exception as error:
                    raise WorkspaceWorkerProtocolError(
                        "worker execution response failed validation"
                    ) from error
            except (_WorkerTimeout, WorkspaceWorkerLost, WorkspaceWorkerProtocolError) as error:
                status = "interrupted" if state.cancel_requested.is_set() else (
                    "timed_out" if isinstance(error, _WorkerTimeout) else "failed"
                )
                self._mark_lost(state, str(error))
                result = self._lost_result(operation, handle, status, str(error), spec.max_events)
            finally:
                state.running_operation = None
            if not state.lost:
                state.managed_event_sequence += len(result.events)
                self._managed_heartbeat(state)
            state.revision = result.revision_after
            with self._lock:
                self._receipts[operation.value] = result
            return result

    def inspect(self, handle: ProgrammableWorkspaceHandle) -> ProgrammableWorkspaceSnapshot:
        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            self._assert_available(state)
            response = self._request(state, {"command": "inspect"}, timeout_s=5.0)
            variables = tuple(
                WorkspaceVariableSummary.model_validate(item, strict=True)
                for item in response["variables"]
            )
            return ProgrammableWorkspaceSnapshot(
                workspace=handle.workspace,
                backend=self.descriptor,
                generation=state.generation,
                revision=state.revision,
                variables=variables,
            )

    def interrupt(
        self, handle: ProgrammableWorkspaceHandle, operation: OperationRef
    ) -> WorkspaceInterruptResult:
        state = self._state(handle.workspace)
        self._check_handle(state, handle)
        if state.running_operation != operation or state.process.poll() is not None:
            return WorkspaceInterruptResult(
                operation=operation,
                accepted=False,
                reason="operation is not running",
            )
        state.cancel_requested.set()
        self._managed_mark_terminating(state)
        self._terminate(state)
        return WorkspaceInterruptResult(
            operation=operation,
            accepted=True,
            reason="worker termination requested",
        )

    def checkpoint(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        policy: WorkspaceCheckpointPolicy,
        trace_id: str,
    ) -> WorkspaceCheckpointManifest:
        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            self._assert_available(state)
            response = self._request(
                state,
                {"command": "checkpoint", "policy": policy.model_dump(mode="json")},
                timeout_s=5.0,
            )
            values = tuple(
                WorkspaceCheckpointValue.model_validate(item, strict=True)
                for item in response["values"]
            )
            exclusions = tuple(
                WorkspaceCheckpointExclusion.model_validate(item, strict=True)
                for item in response["exclusions"]
            )
            return WorkspaceCheckpointManifest.issue(
                source_handle=self._handle(handle.workspace, state),
                creation_operation=operation,
                trace_id=trace_id,
                environment=self._environment,
                values=values,
                exclusions=exclusions,
            )

    def restore(
        self, manifest: WorkspaceCheckpointManifest, spec: WorkspaceRestoreSpec
    ) -> ProgrammableWorkspaceHandle:
        manifest = WorkspaceCheckpointManifest.model_validate(
            manifest.model_dump(mode="python"), strict=True
        )
        if manifest.schema_version not in self.descriptor.checkpoint_formats:
            raise WorkspaceCheckpointRejected(
                f"backend does not support checkpoint format {manifest.schema_version}"
            )
        with self._lock:
            current = self._states.get(spec.workspace.value)

        def restored_state(
            generation: int,
            *,
            defer_managed_registration: bool = False,
        ) -> _WorkerState:
            state = self._spawn(
                spec.workspace,
                spec.session,
                generation=generation,
                register_managed=not defer_managed_registration,
            )
            try:
                self._request(
                    state,
                    {
                        "command": "restore",
                        "values": [item.model_dump(mode="json") for item in manifest.values],
                    },
                    timeout_s=5.0,
                )
            except BaseException:
                self._terminate(state)
                self._managed_finish(
                    state,
                    disposition="terminated",
                    reason="checkpoint_restore_failed",
                )
                raise
            return state

        def install_replacement(
            current_state: _WorkerState,
            state: _WorkerState,
            *,
            disposition: Literal["lost", "terminated"],
            reason: str,
        ) -> None:
            deferred = self._worker_manager is not None and state.managed is None
            with self._lock:
                binding_unchanged = self._states.get(spec.workspace.value) is current_state
            if not binding_unchanged:
                self._terminate(state)
                self._managed_finish(
                    state,
                    disposition="terminated",
                    reason="checkpoint_restore_conflict",
                )
                raise WorkspaceOperationConflict("workspace changed during restore")
            try:
                if disposition == "terminated":
                    self._managed_mark_terminating(current_state)
                self._terminate(current_state)
                current_state.closed = True
                self._managed_finish(
                    current_state,
                    disposition=disposition,
                    reason=reason,
                )
                if deferred:
                    self._register_managed(spec.workspace, state)
                with self._lock:
                    if self._states.get(spec.workspace.value) is not current_state:
                        raise WorkspaceOperationConflict(
                            "workspace changed during restore handoff"
                        )
                    self._states[spec.workspace.value] = state
            except BaseException:
                self._terminate(state)
                self._managed_finish(
                    state,
                    disposition="terminated",
                    reason="checkpoint_restore_handoff_failed",
                )
                raise

        if current is None:
            if spec.expected_handle is not None and not spec.recover_lost_generation:
                raise WorkspaceNotFound(spec.workspace.value)
            state = restored_state(
                spec.expected_handle.generation + 1
                if spec.expected_handle is not None
                else 1
            )
            with self._lock:
                if self._states.get(spec.workspace.value) is not None:
                    self._terminate(state)
                    self._managed_finish(
                        state,
                        disposition="terminated",
                        reason="checkpoint_restore_conflict",
                    )
                    raise WorkspaceOperationConflict("workspace changed during restore")
                self._states[spec.workspace.value] = state
            return self._handle(spec.workspace, state)

        with current.lock:
            with self._lock:
                if self._states.get(spec.workspace.value) is not current:
                    raise WorkspaceOperationConflict("workspace changed during restore")
            if current.session != spec.session:
                raise WorkspaceSessionMismatch("workspace is bound to another session")
            if spec.expected_handle is None:
                raise StaleWorkspaceGeneration(
                    "restoring an existing workspace requires an expected handle"
                )
            if (
                spec.recover_lost_generation
                and current.generation == spec.expected_handle.generation + 1
            ):
                if current.closed or current.revision != 0:
                    raise WorkspaceOperationConflict(
                        "existing recovery generation cannot be reused"
                    )
                if current.running_operation is not None:
                    raise WorkspaceOperationConflict(
                        "cannot reconcile restore while an operation is running"
                    )
                if current.process.poll() is None and not current.lost:
                    response = self._request(
                        current,
                        {
                            "command": "checkpoint",
                            "policy": WorkspaceCheckpointPolicy(
                                max_values=1_024,
                                max_bytes=16_777_216,
                                max_depth=32,
                                max_collection_items=65_536,
                            ).model_dump(mode="json"),
                        },
                        timeout_s=5.0,
                    )
                    values = tuple(
                        WorkspaceCheckpointValue.model_validate(item, strict=True)
                        for item in response["values"]
                    )
                    exclusions = tuple(
                        WorkspaceCheckpointExclusion.model_validate(item, strict=True)
                        for item in response["exclusions"]
                    )
                    if values != manifest.values or exclusions:
                        raise WorkspaceOperationConflict(
                            "existing recovery generation does not match checkpoint"
                        )
                    return self._handle(spec.workspace, current)
                state = restored_state(
                    current.generation,
                    defer_managed_registration=self._worker_manager is not None,
                )
                install_replacement(
                    current,
                    disposition="lost",
                    reason="checkpoint_restore_retry",
                    state=state,
                )
                return self._handle(spec.workspace, state)
            if spec.recover_lost_generation and current.lost:
                if spec.expected_handle.backend != self.descriptor:
                    raise WorkspaceRevisionConflict(
                        "workspace backend capability identity does not match this backend"
                    )
                if current.generation != spec.expected_handle.generation:
                    raise StaleWorkspaceGeneration(
                        "lost workspace generation does not match the recovery predecessor"
                    )
                if current.revision != spec.expected_handle.revision + 1:
                    raise WorkspaceRevisionConflict(
                        "lost workspace revision does not follow the recovery predecessor"
                    )
            else:
                self._check_handle(current, spec.expected_handle)
            if current.running_operation is not None:
                raise WorkspaceOperationConflict("cannot restore while an operation is running")
            state = restored_state(
                current.generation + 1,
                defer_managed_registration=self._worker_manager is not None,
            )
            install_replacement(
                current,
                disposition="terminated",
                reason="checkpoint_restore_successor",
                state=state,
            )
            return self._handle(spec.workspace, state)

    def health(self, handle: ProgrammableWorkspaceHandle) -> WorkspaceHealth:
        state = self._state(handle.workspace)
        self._check_handle(state, handle)
        if state.process.poll() is not None and not state.closed:
            state.lost = True
            state.lost_reason = f"worker exited with code {state.process.returncode}"
            self._managed_finish(
                state,
                disposition="lost",
                reason=state.lost_reason,
            )
        status = "lost" if state.lost else (
            "closed" if state.closed else ("busy" if state.running_operation else "ready")
        )
        return WorkspaceHealth(
            workspace=handle.workspace,
            backend=self.descriptor,
            generation=state.generation,
            revision=state.revision,
            status=status,
            running_operation=state.running_operation,
        )

    def reconcile(
        self, handle: ProgrammableWorkspaceHandle, operation: OperationRef
    ) -> WorkspaceReconciliationResult:
        state = self._state(handle.workspace)
        self._check_handle(state, handle)
        with self._lock:
            result = self._receipts.get(operation.value)
        if result is not None:
            return WorkspaceReconciliationResult(
                operation=operation,
                backend=self.descriptor,
                state="completed",
                observed_revision=state.revision,
                result=result,
            )
        status = "running" if state.running_operation == operation else "lost"
        return WorkspaceReconciliationResult(
            operation=operation,
            backend=self.descriptor,
            state=status,
            observed_revision=state.revision,
        )

    def retire_lost_receipt(
        self, operation: OperationRef, handle: ProgrammableWorkspaceHandle
    ) -> None:
        """Retire only the exact lost-attempt receipt before a verified successor."""

        with self._lock:
            result = self._receipts.get(operation.value)
            if result is None:
                return
            if (
                result.workspace != handle.workspace
                or result.generation != handle.generation
                or result.revision_before != handle.revision
            ):
                raise WorkspaceOperationConflict(
                    "lost receipt does not match the predecessor workspace boundary"
                )
            if not result.workspace_lost:
                raise WorkspaceOperationConflict("only a lost workspace receipt may be retired")
            del self._receipts[operation.value]

    def close(
        self, handle: ProgrammableWorkspaceHandle, *, reason: str
    ) -> WorkspaceCloseResult:
        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            if state.running_operation is not None:
                raise WorkspaceOperationConflict("cannot close while an operation is running")
            result = WorkspaceCloseResult(
                handle=self._handle(handle.workspace, state),
                closed=True,
                reason=reason,
            )
            if state.process.poll() is None:
                with contextlib.suppress(WorkspaceClosed, _WorkerTimeout):
                    self._request(state, {"command": "close"}, timeout_s=2.0)
            self._managed_mark_terminating(state)
            self._terminate(state)
            self._managed_finish(
                state,
                disposition="terminated",
                reason=f"workspace_close:{reason}",
            )
            state.closed = True
            return result

    def shutdown(self) -> None:
        with self._lock:
            states = tuple(self._states.values())
        for state in states:
            if state.running_operation is not None:
                state.cancel_requested.set()
            self._managed_mark_terminating(state)
            self._terminate(state)
            self._managed_finish(
                state,
                disposition="terminated",
                reason="backend_shutdown",
            )
            state.closed = True

    def _spawn(
        self,
        workspace: WorkspaceRef,
        session: SessionRef,
        *,
        generation: int,
        register_managed: bool = True,
    ) -> _WorkerState:
        process = subprocess.Popen(
            exact_module_command(
                "aar.runtime.ipython_worker",
                isolated=True,
            ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        try:
            exact_child = bind_exact_child(
                process,
                owner_kind="ipython-worker",
                owner_generation=f"{workspace.value}:{generation}",
            )
        except (ProcessIdentityMismatch, ProcessIdentityUnavailable) as error:
            if process.poll() is not None:
                process.wait()
            raise WorkspaceWorkerLost(
                "IPython worker child could not be bound to an exact native handle; "
                "numeric-PID cleanup was refused"
            ) from error
        state = _WorkerState(
            session=session,
            generation=generation,
            process=process,
            exact_child=exact_child,
        )
        try:
            ready = self._read_response(state, timeout_s=self._startup_timeout_s)
            if not ready.get("ok") or not ready.get("ready"):
                raise WorkspaceWorkerProtocolError("IPython worker did not report ready")
            if register_managed:
                self._register_managed(workspace, state)
        except BaseException:
            self._terminate(state)
            raise
        return state

    def _register_managed(self, workspace: WorkspaceRef, state: _WorkerState) -> None:
        if self._worker_manager is None or state.managed is not None:
            return
        state.managed = self._worker_manager.register(
            worker_kind="ipython",
            workspace_id=workspace.value,
            workspace_generation=state.generation,
            pid=state.process.pid,
            bound_identity=state.exact_child.identity,
            capability_digest=self.descriptor.capability_digest,
            environment_digest=self._environment.digest,
        )

    def _request(
        self, state: _WorkerState, payload: dict[str, Any], *, timeout_s: float
    ) -> dict[str, Any]:
        self._assert_available(state)
        with state.io_lock:
            stream = state.process.stdin
            if stream is None:
                raise WorkspaceWorkerLost("worker stdin is unavailable")
            try:
                stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                stream.flush()
            except (BrokenPipeError, OSError) as error:
                raise WorkspaceWorkerLost(f"worker input failed: {error}") from error
            response = self._read_response(state, timeout_s=timeout_s)
        if not response.get("ok"):
            raise WorkspaceWorkerProtocolError(
                f"worker rejected command: {response.get('error_type')}: {response.get('message')}"
            )
        return response

    def _read_response(self, state: _WorkerState, *, timeout_s: float) -> dict[str, Any]:
        stream = state.process.stdout
        if stream is None:
            raise WorkspaceWorkerLost("worker stdout is unavailable")
        line = self._readline(stream, timeout_s)
        if line is None:
            raise _WorkerTimeout("IPython worker response deadline expired")
        if line == "":
            message = self._stderr_tail(state.process)
            raise WorkspaceWorkerLost(
                f"IPython worker exited with code {state.process.poll()}: {message}"
            )
        try:
            response = json.loads(line)
        except json.JSONDecodeError as error:
            raise WorkspaceWorkerProtocolError("worker emitted invalid JSON") from error
        if not isinstance(response, dict):
            raise WorkspaceWorkerProtocolError("worker response is not an object")
        return response

    def _result_from_response(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
        response: dict[str, Any],
    ) -> WorkspaceProgramResult:
        artifacts = response.get("artifacts", [])
        events: list[WorkspaceEvent] = []
        status = str(response["status"])
        artifact_error: BaseException | None = None
        for raw in response["events"]:
            kind = raw["kind"]
            try:
                if kind == "artifact":
                    if self._artifact_sink is None:
                        raise WorkspaceWorkerProtocolError(
                            "worker emitted artifact without a host sink"
                        )
                    item = artifacts[int(raw["artifact_index"])]
                    reference = self._artifact_sink(
                        base64.b64decode(item["content_base64"], validate=True),
                        str(item["media_type"]),
                        operation,
                    )
                    event = WorkspaceEvent(
                        sequence=len(events) + 1,
                        kind="artifact",
                        artifact=reference,
                    )
                elif kind in {"stdout", "stderr"}:
                    event = WorkspaceEvent(
                        sequence=len(events) + 1,
                        kind=kind,
                        text=str(raw["text"]),
                        truncated=bool(raw.get("truncated", False)),
                    )
                else:
                    event = WorkspaceEvent(
                        sequence=len(events) + 1,
                        kind=kind,
                        data=raw["data"],
                        truncated=bool(raw.get("truncated", False)),
                    )
                events.append(event)
            except BaseException as error:
                artifact_error = error
                break
        if artifact_error is not None:
            status = "failed"
            if len(events) >= spec.max_events - 1:
                events.pop()
            events.append(
                WorkspaceEvent(
                    sequence=len(events) + 1,
                    kind="exception",
                    data={
                        "type": type(artifact_error).__name__,
                        "message": str(artifact_error)[:2_048],
                    },
                )
            )
            events.append(
                WorkspaceEvent(
                    sequence=len(events) + 1,
                    kind="progress",
                    data={"phase": "finished", "status": "failed"},
                )
            )
        return WorkspaceProgramResult(
            operation=operation,
            workspace=handle.workspace,
            backend=self.descriptor,
            generation=handle.generation,
            revision_before=handle.revision,
            revision_after=handle.revision + 1,
            status=status,
            result=response.get("result") if status == "succeeded" else None,
            result_excluded_reason=(
                response.get("result_excluded_reason") if status == "succeeded" else None
            ),
            events=tuple(events),
        )

    def _lost_result(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        status: str,
        message: str,
        max_events: int,
    ) -> WorkspaceProgramResult:
        del max_events
        return WorkspaceProgramResult(
            operation=operation,
            workspace=handle.workspace,
            backend=self.descriptor,
            generation=handle.generation,
            revision_before=handle.revision,
            revision_after=handle.revision + 1,
            status=status,
            workspace_lost=True,
            events=(
                WorkspaceEvent(sequence=1, kind="progress", data={"phase": "started"}),
                WorkspaceEvent(
                    sequence=2,
                    kind="exception",
                    data={
                        "type": "WorkspaceWorkerLost",
                        "message": message[-2_048:],
                    },
                ),
                WorkspaceEvent(
                    sequence=3,
                    kind="progress",
                    data={"phase": "finished", "status": status},
                ),
            ),
        )

    def _state(self, workspace: WorkspaceRef) -> _WorkerState:
        with self._lock:
            state = self._states.get(workspace.value)
        if state is None:
            raise WorkspaceNotFound(workspace.value)
        return state

    def _check_handle(
        self, state: _WorkerState, handle: ProgrammableWorkspaceHandle
    ) -> None:
        if handle.backend != self.descriptor:
            raise WorkspaceRevisionConflict(
                "workspace backend capability identity does not match this backend"
            )
        if state.generation != handle.generation:
            raise StaleWorkspaceGeneration(
                f"workspace generation is {state.generation}, not {handle.generation}"
            )
        if state.revision != handle.revision:
            raise WorkspaceRevisionConflict(
                f"workspace revision is {state.revision}, not {handle.revision}"
            )

    def _handle(
        self, workspace: WorkspaceRef, state: _WorkerState
    ) -> ProgrammableWorkspaceHandle:
        return ProgrammableWorkspaceHandle(
            workspace=workspace,
            backend=self.descriptor,
            generation=state.generation,
            revision=state.revision,
        )

    @staticmethod
    def _assert_available(state: _WorkerState) -> None:
        if state.closed:
            raise WorkspaceClosed("workspace is closed")
        if state.lost or state.process.poll() is not None:
            raise WorkspaceWorkerLost(state.lost_reason or "workspace worker is lost")

    def _mark_lost(self, state: _WorkerState, reason: str) -> None:
        self._terminate(state)
        state.lost = True
        state.lost_reason = reason
        cancelled = state.cancel_requested.is_set()
        self._managed_finish(
            state,
            disposition="terminated" if cancelled else "lost",
            reason=f"cancelled:{reason}" if cancelled else reason,
        )

    def _managed_heartbeat(self, state: _WorkerState) -> None:
        if self._worker_manager is None or state.managed is None or state.managed_finished:
            return
        binding = self._worker_manager.heartbeat(
            state.managed,
            operation=state.running_operation,
            last_event_sequence=state.managed_event_sequence,
        )
        if binding.state in {"lost", "quarantined", "terminated"}:
            state.managed_finished = True
            state.lost = True
            state.lost_reason = "durable worker identity is no longer live"
            raise WorkspaceWorkerLost(state.lost_reason)

    def _managed_mark_terminating(self, state: _WorkerState) -> None:
        if self._worker_manager is None or state.managed is None or state.managed_finished:
            return
        self._worker_manager.mark_terminating(state.managed)

    def _managed_finish(
        self,
        state: _WorkerState,
        *,
        disposition: Literal["lost", "quarantined", "terminated"],
        reason: str,
    ) -> None:
        if self._worker_manager is None or state.managed is None or state.managed_finished:
            return
        self._worker_manager.finish(
            state.managed,
            disposition=disposition,
            reason=reason,
        )
        state.managed_finished = True

    @staticmethod
    def _readline(stream: TextIO, timeout_s: float) -> str | None:
        result: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

        def read() -> None:
            try:
                result.put(stream.readline())
            except BaseException as error:
                result.put(error)

        thread = threading.Thread(target=read, daemon=True)
        thread.start()
        try:
            value = result.get(timeout=timeout_s)
        except queue.Empty:
            return None
        if isinstance(value, BaseException):
            raise WorkspaceWorkerLost(f"worker output failed: {value}") from value
        return value

    @staticmethod
    def _stderr_tail(process: subprocess.Popen[str]) -> str:
        if process.poll() is None or process.stderr is None:
            return "no worker stderr available"
        try:
            return process.stderr.read()[-2_048:].strip()
        except OSError:
            return "worker stderr read failed"

    @staticmethod
    def _terminate(state: _WorkerState) -> None:
        try:
            state.exact_child.terminalize(2)
        except ProcessIdentityUnavailable as error:
            raise WorkspaceWorkerLost(
                "IPython worker exact child could not reach a verified terminal state"
            ) from error


class WorkerFrameError(RuntimeError):
    """Base class for a worker frame that cannot be safely handled."""


class WorkerFrameConflict(WorkerFrameError):
    """A durable sequence or broker identity was reused with different bytes."""


class WorkerFrameIndeterminate(WorkerFrameError):
    """A prior dispatch started but has no durable terminal receipt."""


class WorkspaceBrokerSession:
    """Persist typed worker intents and exact supervisor receipts around dispatch."""

    def __init__(
        self,
        database_path: Path,
        *,
        operation_id: str,
        attempt_id: str,
        dispatch: Callable[[dict[str, Any]], dict[str, Any]],
    ) -> None:
        self._factory = SQLiteConnectionFactory(database_path)
        self._operation_id = operation_id
        self._attempt_id = attempt_id
        self._attempt_identity = canonical_sha256(
            {"operation_id": operation_id, "attempt_id": attempt_id}
        )
        self._dispatch = dispatch
        with self._factory.transaction(write=True) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_calls (
                    operation_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    method TEXT NOT NULL,
                    grant_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_digest TEXT NOT NULL,
                    request_json TEXT,
                    state TEXT NOT NULL,
                    response_digest TEXT,
                    response_model TEXT NOT NULL,
                    response_json TEXT,
                    usage_json TEXT NOT NULL,
                    failure_code TEXT,
                    reconciliation_action TEXT,
                    authority_digest TEXT,
                    reconciliation_json TEXT,
                    compensation_json TEXT,
                    reconciled_at_unix_ms INTEGER,
                    control_revision INTEGER,
                    PRIMARY KEY(operation_id, sequence),
                    UNIQUE(operation_id, idempotency_key)
                )
                """
            )

    def handle_frame(self, frame: WorkspaceBrokerFrame) -> WorkspaceBrokerFrame:
        document = frame.model_dump(mode="json")
        self._validate_identity(document)
        sequence = document["frame_sequence"]
        request_digest = canonical_sha256(document)
        request_json = canonical_json_bytes(document).decode("utf-8")
        payload = document["payload"]
        context = payload["context"]

        with self._factory.transaction(write=True) as connection:
            existing = self._row(connection, sequence)
            if existing is not None:
                return self._replay(existing, request_digest)
            duplicate_identity = connection.execute(
                """
                SELECT * FROM broker_calls
                WHERE operation_id=? AND idempotency_key=?
                """,
                (self._operation_id, context["idempotency_key"]),
            ).fetchone()
            if duplicate_identity is not None:
                raise WorkerFrameConflict(
                    "broker identity already binds a different frame sequence"
                )
            connection.execute(
                """
                INSERT INTO broker_calls(
                    operation_id, sequence, method, grant_id, idempotency_key,
                    request_digest, request_json, state, response_digest,
                    response_model, response_json, usage_json, failure_code,
                    authority_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'started', NULL,
                          'aar.workspace-broker-frame.v1', NULL, '{}', NULL, ?)
                """,
                (
                    self._operation_id,
                    sequence,
                    payload["method"],
                    context["grant_id"],
                    context["idempotency_key"],
                    request_digest,
                    request_json,
                    self._attempt_identity,
                ),
            )

        receipt_payload = self._dispatch(payload)
        response = WorkspaceBrokerFrame.model_validate(
            self._receipt_document(document, receipt_payload),
            strict=True,
        )
        response_document = response.model_dump(mode="json")
        response_json = canonical_json_bytes(response_document).decode("utf-8")
        response_digest = canonical_sha256(response_document)

        with self._factory.transaction(write=True) as connection:
            current = self._row(connection, sequence)
            if current is None or current["request_digest"] != request_digest:
                raise WorkerFrameConflict("worker frame identity changed during dispatch")
            if current["state"] == "succeeded":
                return self._replay(current, request_digest)
            if current["state"] != "started":
                raise WorkerFrameIndeterminate(
                    "worker frame has no authoritative terminal receipt"
                )
            connection.execute(
                """
                UPDATE broker_calls
                SET state='succeeded', response_digest=?, response_json=?
                WHERE operation_id=? AND sequence=? AND state='started'
                """,
                (response_digest, response_json, self._operation_id, sequence),
            )
        return response

    @property
    def broker_call_count(self) -> int:
        with self._factory.transaction(write=False) as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM broker_calls
                    WHERE operation_id=? AND authority_digest=?
                    """,
                    (self._operation_id, self._attempt_identity),
                ).fetchone()[0]
            )

    @property
    def event_count(self) -> int:
        with self._factory.transaction(write=False) as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM broker_calls
                    WHERE operation_id=? AND authority_digest=? AND state='succeeded'
                    """,
                    (self._operation_id, self._attempt_identity),
                ).fetchone()[0]
            )

    def close(self) -> None:
        self._factory.close()

    def _validate_identity(self, document: dict[str, Any]) -> None:
        if document["operation"]["value"] != self._operation_id:
            raise WorkerFrameConflict("worker frame operation identity mismatch")
        if document["attempt_id"] != self._attempt_id:
            raise WorkerFrameConflict("worker frame attempt identity mismatch")
        if (
            document["kind"] != "broker_intent"
            or document["direction"] != "worker_to_supervisor"
            or document["committed"] is not False
        ):
            raise WorkerFrameConflict("worker frame is not an uncommitted broker intent")

    def _row(self, connection: sqlite3.Connection, sequence: int) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM broker_calls WHERE operation_id=? AND sequence=?",
            (self._operation_id, sequence),
        ).fetchone()

    @staticmethod
    def _replay(row: sqlite3.Row, request_digest: str) -> WorkspaceBrokerFrame:
        if row["request_digest"] != request_digest:
            raise WorkerFrameConflict("worker frame sequence digest conflict")
        if row["state"] != "succeeded" or row["response_json"] is None:
            raise WorkerFrameIndeterminate(
                "worker frame dispatch started without a terminal receipt"
            )
        response = WorkspaceBrokerFrame.model_validate_json(
            str(row["response_json"]), strict=True
        )
        if row["response_digest"] != canonical_sha256(response.model_dump(mode="json")):
            raise WorkerFrameConflict("stored worker receipt digest mismatch")
        return response

    @staticmethod
    def _receipt_document(
        intent: dict[str, Any],
        receipt_payload: dict[str, Any],
    ) -> dict[str, Any]:
        payload_bytes = canonical_json_bytes(receipt_payload)
        return {
            "schema_version": "aar.workspace-broker-frame.v1",
            "kind": "broker_receipt",
            "direction": "supervisor_to_worker",
            "committed": True,
            "operation": intent["operation"],
            "attempt_id": intent["attempt_id"],
            "attempt_fence": intent["attempt_fence"],
            "workspace": intent["workspace"],
            "workspace_generation": intent["workspace_generation"],
            "workspace_revision": intent["workspace_revision"],
            "cell_execution_id": intent["cell_execution_id"],
            "frame_sequence": intent["frame_sequence"] + 1,
            "broker_call_ordinal": intent["broker_call_ordinal"],
            "deadline_unix_ms": intent["deadline_unix_ms"],
            "payload": receipt_payload,
            "payload_digest": canonical_sha256(receipt_payload),
            "payload_bytes": len(payload_bytes),
        }
