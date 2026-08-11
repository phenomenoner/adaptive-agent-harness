"""Shared AR-1 programmable workspace contract and plain-Python backend."""

from __future__ import annotations

import ast
import contextlib
import json
import sys
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, TextIO, runtime_checkable

from pydantic import JsonValue

from aar.canonical import canonical_json_bytes
from aar.runtime.workspace import (
    StaleWorkspaceGeneration,
    WorkspaceError,
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
from aar.schemas import ArtifactReference, OperationRef, SessionRef, WorkspaceRef

ArtifactSink = Callable[[bytes, str, OperationRef], ArtifactReference]


class WorkspaceClosed(WorkspaceError):
    pass


class WorkspaceOperationConflict(WorkspaceError):
    pass


class WorkspaceCheckpointRejected(WorkspaceError):
    pass


class WorkspaceArtifactUnavailable(WorkspaceError):
    pass


@runtime_checkable
class WorkspaceBackend(Protocol):
    """Backend-neutral lifecycle implemented by plain Python and supervised IPython."""

    @property
    def descriptor(self) -> WorkspaceBackendDescriptor: ...

    def create(
        self, workspace: WorkspaceRef, session: SessionRef
    ) -> ProgrammableWorkspaceHandle: ...

    def attach(
        self,
        handle: ProgrammableWorkspaceHandle,
        session: SessionRef,
    ) -> ProgrammableWorkspaceHandle: ...

    def execute(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
    ) -> WorkspaceProgramResult: ...

    def inspect(self, handle: ProgrammableWorkspaceHandle) -> ProgrammableWorkspaceSnapshot: ...

    def interrupt(
        self, handle: ProgrammableWorkspaceHandle, operation: OperationRef
    ) -> WorkspaceInterruptResult: ...

    def checkpoint(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        policy: WorkspaceCheckpointPolicy,
        trace_id: str,
    ) -> WorkspaceCheckpointManifest: ...

    def restore(
        self, manifest: WorkspaceCheckpointManifest, spec: WorkspaceRestoreSpec
    ) -> ProgrammableWorkspaceHandle: ...

    def health(self, handle: ProgrammableWorkspaceHandle) -> WorkspaceHealth: ...

    def reconcile(
        self, handle: ProgrammableWorkspaceHandle, operation: OperationRef
    ) -> WorkspaceReconciliationResult: ...

    def close(
        self, handle: ProgrammableWorkspaceHandle, *, reason: str
    ) -> WorkspaceCloseResult: ...

    def shutdown(self) -> None: ...


@dataclass(slots=True)
class _WorkspaceState:
    session: SessionRef
    generation: int
    revision: int = 0
    namespace: dict[str, Any] = field(default_factory=dict)
    closed: bool = False
    lock: threading.RLock = field(default_factory=threading.RLock)


class _ExecutionInterrupted(BaseException):
    pass


class _ExecutionTimedOut(BaseException):
    pass


class _PortableValueError(ValueError):
    pass


class _EventCollector:
    def __init__(self, max_events: int, max_output_chars: int) -> None:
        self._max_events = max_events
        self._remaining_output_chars = max_output_chars
        self._events: list[WorkspaceEvent] = []
        self._dropped_events = False

    def _append(
        self,
        kind: str,
        *,
        text: str | None = None,
        data: JsonValue | None = None,
        artifact: ArtifactReference | None = None,
        truncated: bool = False,
        force: bool = False,
    ) -> None:
        if len(self._events) >= self._max_events - 1:
            self._dropped_events = True
            if not force:
                return
            self._events.pop()
        self._events.append(
            WorkspaceEvent(
                sequence=len(self._events) + 1,
                kind=kind,
                text=text,
                data=data,
                artifact=artifact,
                truncated=truncated,
            )
        )

    def stream(self, kind: str, text: str) -> None:
        if not text:
            return
        allowed = min(len(text), self._remaining_output_chars, 8_192)
        chunk = text[:allowed]
        truncated = allowed < len(text)
        self._remaining_output_chars -= allowed
        if chunk:
            self._append(kind, text=chunk, truncated=truncated)
        elif text:
            self._dropped_events = True

    def structured(self, kind: str, data: JsonValue) -> None:
        self._append(kind, data=data, force=kind == "exception")

    def artifact(self, reference: ArtifactReference) -> None:
        self._append("artifact", artifact=reference)

    def finish(self, status: str) -> tuple[WorkspaceEvent, ...]:
        data: dict[str, JsonValue] = {"phase": "finished", "status": status}
        if self._dropped_events:
            data["events_truncated"] = True
        if len(self._events) < self._max_events:
            self._events.append(
                WorkspaceEvent(
                    sequence=len(self._events) + 1,
                    kind="progress",
                    data=data,
                    truncated=self._dropped_events,
                )
            )
        return tuple(self._events)


class _StreamWriter(TextIO):
    def __init__(self, collector: _EventCollector, kind: str) -> None:
        self._collector = collector
        self._kind = kind
        self._buffer = ""

    def write(self, value: str) -> int:
        self._buffer += value
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._collector.stream(self._kind, f"{line}\n")
        return len(value)

    def flush(self) -> None:
        if self._buffer:
            self._collector.stream(self._kind, self._buffer)
            self._buffer = ""


class PlainPythonWorkspaceBackend:
    """Deterministic in-process conformance backend; it is not a security sandbox."""

    _RESERVED_NAMES = frozenset({"aar_artifact", "aar_display", "aar_progress"})

    def __init__(self, *, artifact_sink: ArtifactSink | None = None) -> None:
        self._artifact_sink = artifact_sink
        self._descriptor = WorkspaceBackendDescriptor.issue(
            kind="plain-python",
            version="aar.plain-python.v1",
            checkpoint_formats=("aar.workspace-checkpoint.v1",),
            features=(
                "checkpoint.json-subset",
                "events.structured",
                "interrupt.cooperative",
                "state.persistent",
                "timeout.cooperative",
            ),
        )
        self._environment = WorkspaceEnvironmentFingerprint.current()
        self._states: dict[str, _WorkspaceState] = {}
        self._running: dict[str, tuple[str, threading.Event]] = {}
        self._receipts: dict[str, WorkspaceProgramResult] = {}
        self._lock = threading.RLock()

    @property
    def descriptor(self) -> WorkspaceBackendDescriptor:
        return self._descriptor

    def create(
        self, workspace: WorkspaceRef, session: SessionRef
    ) -> ProgrammableWorkspaceHandle:
        with self._lock:
            state = self._states.get(workspace.value)
            if state is None:
                state = _WorkspaceState(session=session, generation=1)
                self._states[workspace.value] = state
            elif state.session != session:
                raise WorkspaceSessionMismatch("workspace is bound to another session")
            elif state.closed:
                raise WorkspaceClosed(
                    "workspace is closed; restore a checkpoint into a new generation"
                )
            return self._handle(workspace, state)

    def attach(
        self,
        handle: ProgrammableWorkspaceHandle,
        session: SessionRef,
    ) -> ProgrammableWorkspaceHandle:
        state = self._state(handle.workspace)
        with state.lock:
            if state.session != session:
                raise WorkspaceSessionMismatch("workspace is bound to another session")
            self._check_handle(state, handle)
            if state.closed:
                raise WorkspaceClosed("workspace is closed")
            return self._handle(handle.workspace, state)

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
            if operation.value in self._running:
                raise WorkspaceOperationConflict("operation is already running")
            cancel_event = threading.Event()
            self._running[operation.value] = (handle.workspace.value, cancel_event)

        state = self._state(handle.workspace)
        try:
            with state.lock:
                self._check_handle(state, handle)
                if state.closed:
                    raise WorkspaceClosed("workspace is closed")
                result = self._execute_locked(operation, handle, spec, state, cancel_event)
                state.revision = result.revision_after
                with self._lock:
                    self._receipts[operation.value] = result
                return result
        finally:
            with self._lock:
                self._running.pop(operation.value, None)

    def _execute_locked(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
        state: _WorkspaceState,
        cancel_event: threading.Event,
    ) -> WorkspaceProgramResult:
        collector = _EventCollector(spec.max_events, spec.max_output_chars)
        collector.structured("progress", {"phase": "started"})
        stdout = _StreamWriter(collector, "stdout")
        stderr = _StreamWriter(collector, "stderr")
        deadline = time.monotonic() + (spec.wall_time_ms / 1_000)
        previous_trace = sys.gettrace()
        sentinel = object()
        previous_reserved = {
            name: state.namespace.get(name, sentinel) for name in self._RESERVED_NAMES
        }

        def display(value: Any, media_type: str = "application/json") -> None:
            portable = _portable_value(value)
            collector.structured("display", {"media_type": media_type, "value": portable})

        def progress(
            message: str,
            *,
            completed: int | None = None,
            total: int | None = None,
        ) -> None:
            data: dict[str, JsonValue] = {"message": str(message)}
            if completed is not None:
                data["completed"] = completed
            if total is not None:
                data["total"] = total
            collector.structured("progress", data)

        def artifact(
            name: str,
            content: str | bytes,
            media_type: str = "application/octet-stream",
        ) -> ArtifactReference:
            if self._artifact_sink is None:
                raise WorkspaceArtifactUnavailable("no host artifact sink is configured")
            payload = content.encode("utf-8") if isinstance(content, str) else bytes(content)
            reference = self._artifact_sink(payload, media_type, operation)
            collector.artifact(reference)
            collector.structured("progress", {"artifact_name": str(name)})
            return reference

        state.namespace.update(
            {
                "aar_artifact": artifact,
                "aar_display": display,
                "aar_progress": progress,
            }
        )

        def trace(frame: Any, event: str, arg: Any) -> Any:
            del frame, arg
            if event in {"call", "line"}:
                if cancel_event.is_set():
                    raise _ExecutionInterrupted()
                if time.monotonic() >= deadline:
                    raise _ExecutionTimedOut()
            return trace

        status = "succeeded"
        value: Any = None
        try:
            sys.settrace(trace)
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                value = _execute_with_last_expression(
                    spec.code,
                    state.namespace,
                    f"<aar-workspace:{handle.workspace.value}>",
                )
        except _ExecutionInterrupted:
            status = "interrupted"
            collector.structured(
                "exception",
                {
                    "type": "WorkspaceInterrupted",
                    "message": "host cancellation requested",
                },
            )
        except _ExecutionTimedOut:
            status = "timed_out"
            collector.structured(
                "exception",
                {
                    "type": "WorkspaceTimedOut",
                    "message": "workspace wall-time limit expired",
                },
            )
        except BaseException as error:
            status = "failed"
            collector.structured(
                "exception",
                {
                    "type": type(error).__name__,
                    "message": str(error)[:2_048],
                    "traceback": "".join(
                        traceback.format_exception(type(error), error, error.__traceback__)
                    )[-8_192:],
                },
            )
        finally:
            sys.settrace(previous_trace)
            stdout.flush()
            stderr.flush()
            for name, prior in previous_reserved.items():
                if prior is sentinel:
                    state.namespace.pop(name, None)
                else:
                    state.namespace[name] = prior

        portable_result: JsonValue | None = None
        excluded_reason: str | None = None
        if status == "succeeded" and value is not None:
            try:
                portable_result = _portable_value(value)
                if len(canonical_json_bytes(portable_result)) > 65_536:
                    raise _PortableValueError("portable result exceeds 65536 bytes")
            except _PortableValueError as error:
                portable_result = None
                excluded_reason = str(error)
        return WorkspaceProgramResult(
            operation=operation,
            workspace=handle.workspace,
            backend=self.descriptor,
            generation=state.generation,
            revision_before=state.revision,
            revision_after=state.revision + 1,
            status=status,
            result=portable_result,
            result_excluded_reason=excluded_reason,
            events=collector.finish(status),
        )

    def inspect(self, handle: ProgrammableWorkspaceHandle) -> ProgrammableWorkspaceSnapshot:
        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            variables = tuple(
                self._variable_summary(name, value)
                for name, value in sorted(state.namespace.items())
                if self._is_user_name(name)
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
        with self._lock:
            running = self._running.get(operation.value)
            if running is None or running[0] != handle.workspace.value:
                return WorkspaceInterruptResult(
                    operation=operation,
                    accepted=False,
                    reason="operation is not running",
                )
            running[1].set()
            return WorkspaceInterruptResult(
                operation=operation,
                accepted=True,
                reason="cancellation requested",
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
            if state.closed:
                raise WorkspaceClosed("workspace is closed")
            values: list[WorkspaceCheckpointValue] = []
            exclusions: list[WorkspaceCheckpointExclusion] = []
            consumed_bytes = 0
            for name, value in sorted(state.namespace.items()):
                if not self._is_user_name(name):
                    continue
                if len(values) >= policy.max_values:
                    exclusions.append(
                        self._exclusion(name, value, "checkpoint value limit exceeded")
                    )
                    continue
                try:
                    portable = _portable_value(
                        value,
                        max_depth=policy.max_depth,
                        max_collection_items=policy.max_collection_items,
                    )
                    encoded = canonical_json_bytes(portable)
                    if consumed_bytes + len(encoded) > policy.max_bytes:
                        raise _PortableValueError("checkpoint byte limit exceeded")
                    consumed_bytes += len(encoded)
                    values.append(WorkspaceCheckpointValue(name=name, value=portable))
                except _PortableValueError as error:
                    exclusions.append(self._exclusion(name, value, str(error)))
            return WorkspaceCheckpointManifest.issue(
                source_handle=self._handle(handle.workspace, state),
                creation_operation=operation,
                trace_id=trace_id,
                environment=self._environment,
                values=tuple(values),
                exclusions=tuple(exclusions),
            )

    def restore(
        self, manifest: WorkspaceCheckpointManifest, spec: WorkspaceRestoreSpec
    ) -> ProgrammableWorkspaceHandle:
        # Revalidation detects any caller-created object whose digest does not match its payload.
        manifest = WorkspaceCheckpointManifest.model_validate(
            manifest.model_dump(mode="python"), strict=True
        )
        if manifest.schema_version not in self.descriptor.checkpoint_formats:
            raise WorkspaceCheckpointRejected(
                f"backend does not support checkpoint format {manifest.schema_version}"
            )
        namespace = {
            item.name: json.loads(canonical_json_bytes(item.value)) for item in manifest.values
        }
        with self._lock:
            current = self._states.get(spec.workspace.value)
            running = any(
                workspace == spec.workspace.value
                for workspace, _event in self._running.values()
            )
        if running:
            raise WorkspaceOperationConflict("cannot restore while an operation is running")
        if current is None:
            if spec.expected_handle is not None:
                raise WorkspaceNotFound(spec.workspace.value)
            state = _WorkspaceState(
                session=spec.session,
                generation=1,
                revision=0,
                namespace=namespace,
            )
            with self._lock:
                if self._states.get(spec.workspace.value) is not None:
                    raise WorkspaceOperationConflict("workspace changed during restore")
                self._states[spec.workspace.value] = state
            return self._handle(spec.workspace, state)

        with current.lock:
            if current.session != spec.session:
                raise WorkspaceSessionMismatch("workspace is bound to another session")
            if spec.expected_handle is None:
                raise StaleWorkspaceGeneration(
                    "restoring an existing workspace requires an expected handle"
                )
            self._check_handle(current, spec.expected_handle)
            with self._lock:
                if self._states.get(spec.workspace.value) is not current:
                    raise WorkspaceOperationConflict("workspace changed during restore")
                if any(
                    workspace == spec.workspace.value
                    for workspace, _event in self._running.values()
                ):
                    raise WorkspaceOperationConflict(
                        "cannot restore while an operation is running"
                    )
                state = _WorkspaceState(
                    session=spec.session,
                    generation=current.generation + 1,
                    revision=0,
                    namespace=namespace,
                )
                self._states[spec.workspace.value] = state
            current.closed = True
            return self._handle(spec.workspace, state)

    def health(self, handle: ProgrammableWorkspaceHandle) -> WorkspaceHealth:
        state = self._state(handle.workspace)
        self._check_handle(state, handle)
        with self._lock:
            running = next(
                (
                    OperationRef(value=operation)
                    for operation, (workspace, _event) in self._running.items()
                    if workspace == handle.workspace.value
                ),
                None,
            )
        return WorkspaceHealth(
            workspace=handle.workspace,
            backend=self.descriptor,
            generation=state.generation,
            revision=state.revision,
            status="closed" if state.closed else ("busy" if running else "ready"),
            running_operation=running,
        )

    def reconcile(
        self, handle: ProgrammableWorkspaceHandle, operation: OperationRef
    ) -> WorkspaceReconciliationResult:
        state = self._state(handle.workspace)
        self._check_handle(state, handle)
        with self._lock:
            result = self._receipts.get(operation.value)
            running = operation.value in self._running
        if result is not None:
            return WorkspaceReconciliationResult(
                operation=operation,
                backend=self.descriptor,
                state="completed",
                observed_revision=state.revision,
                result=result,
            )
        return WorkspaceReconciliationResult(
            operation=operation,
            backend=self.descriptor,
            state="running" if running else "lost",
            observed_revision=state.revision,
        )

    def close(
        self, handle: ProgrammableWorkspaceHandle, *, reason: str
    ) -> WorkspaceCloseResult:
        state = self._state(handle.workspace)
        with state.lock:
            self._check_handle(state, handle)
            with self._lock:
                if any(
                    workspace == handle.workspace.value
                    for workspace, _event in self._running.values()
                ):
                    raise WorkspaceOperationConflict("cannot close while an operation is running")
            result = WorkspaceCloseResult(
                handle=self._handle(handle.workspace, state),
                closed=True,
                reason=reason,
            )
            state.closed = True
            return result

    def shutdown(self) -> None:
        with self._lock:
            running = tuple(self._running.values())
            states = tuple(self._states.values())
        for _workspace, cancel_event in running:
            cancel_event.set()
        for state in states:
            with state.lock:
                state.closed = True

    def _state(self, workspace: WorkspaceRef) -> _WorkspaceState:
        with self._lock:
            state = self._states.get(workspace.value)
        if state is None:
            raise WorkspaceNotFound(workspace.value)
        return state

    def _check_handle(
        self, state: _WorkspaceState, handle: ProgrammableWorkspaceHandle
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
        self, workspace: WorkspaceRef, state: _WorkspaceState
    ) -> ProgrammableWorkspaceHandle:
        return ProgrammableWorkspaceHandle(
            workspace=workspace,
            backend=self.descriptor,
            generation=state.generation,
            revision=state.revision,
        )

    @classmethod
    def _is_user_name(cls, name: str) -> bool:
        return not name.startswith("__") and name not in cls._RESERVED_NAMES and name != "_"

    @staticmethod
    def _exclusion(name: str, value: Any, reason: str) -> WorkspaceCheckpointExclusion:
        return WorkspaceCheckpointExclusion(
            name=name,
            type_name=type(value).__name__[:128],
            reason=reason[:256],
        )

    @classmethod
    def _variable_summary(cls, name: str, value: Any) -> WorkspaceVariableSummary:
        try:
            portable = _portable_value(value)
            preview = canonical_json_bytes(portable).decode()[:256]
            is_portable = True
        except _PortableValueError:
            preview = f"<{type(value).__name__}>"[:256]
            is_portable = False
        return WorkspaceVariableSummary(
            name=name,
            type_name=type(value).__name__[:128],
            portable=is_portable,
            preview=preview,
        )


def _execute_with_last_expression(code: str, namespace: dict[str, Any], filename: str) -> Any:
    tree = ast.parse(code, filename=filename, mode="exec")
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        prefix = ast.Module(body=tree.body[:-1], type_ignores=[])
        if prefix.body:
            exec(compile(prefix, filename, "exec"), namespace, namespace)
        expression = ast.Expression(body=tree.body[-1].value)
        value = eval(compile(expression, filename, "eval"), namespace, namespace)
        namespace["_"] = value
        return value
    exec(compile(tree, filename, "exec"), namespace, namespace)
    return None


def _portable_value(
    value: Any,
    *,
    max_depth: int = 12,
    max_collection_items: int = 4_096,
) -> JsonValue:
    remaining = [max_collection_items]
    seen: set[int] = set()

    def convert(item: Any, depth: int) -> JsonValue:
        if depth > max_depth:
            raise _PortableValueError("portable value depth limit exceeded")
        if item is None or isinstance(item, (str, bool)):
            return item
        if isinstance(item, int) and not isinstance(item, bool):
            if not -(2**63) <= item <= 2**63 - 1:
                raise _PortableValueError("integer is outside signed 64-bit range")
            return item
        if isinstance(item, float):
            raise _PortableValueError("floating-point values are not portable")
        if isinstance(item, (list, dict)):
            identity = id(item)
            if identity in seen:
                raise _PortableValueError("cyclic values are not portable")
            seen.add(identity)
            try:
                if isinstance(item, list):
                    remaining[0] -= len(item)
                    if remaining[0] < 0:
                        raise _PortableValueError("portable collection item limit exceeded")
                    return [convert(child, depth + 1) for child in item]
                if any(not isinstance(key, str) for key in item):
                    raise _PortableValueError("portable mappings require string keys")
                remaining[0] -= len(item)
                if remaining[0] < 0:
                    raise _PortableValueError("portable collection item limit exceeded")
                return {key: convert(item[key], depth + 1) for key in sorted(item)}
            finally:
                seen.remove(identity)
        raise _PortableValueError(f"unsupported portable value type: {type(item).__name__}")

    portable = convert(value, 0)
    canonical_json_bytes(portable)
    return portable
