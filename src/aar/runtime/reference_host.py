"""Direct-SDK reference host for deterministic AR-0B conformance."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from aar.asset_models import AdaptiveAssetBundle
from aar.assets import (
    AdaptiveAssetStore,
    AssetConflict,
    AssetReferenceMissing,
    SimulatedAssetProcessLoss,
)
from aar.broker_models import BrokerReconciliationReport
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.continuity_models import (
    OperationWorkspaceCheckpointBoundaryV1,
    OperationWorkspaceCheckpointSelectionV1,
)
from aar.rlm_models import RlmJobSnapshot, RlmJobSpec
from aar.runtime.brokers import (
    BrokerBudgetExceeded,
    BrokerCallConflict,
    BrokerCallIndeterminate,
    BrokerDeadlineExpired,
    BrokerGrantDenied,
    FakeArtifactBroker,
    FakeEffectBroker,
    FakeEvidenceProvider,
    FakeModelBroker,
    FakeSubagentBroker,
    TypedBrokerFacade,
)
from aar.runtime.continuity import (
    RLM_OPERATION_KIND,
    WORKSPACE_PROGRAM_OPERATION_KIND,
    plan_rlm_step_successor,
    plan_workspace_checkpoint_successor,
    rlm_recovery_environment_digest,
    rlm_step_boundary_policy,
    workspace_checkpoint_boundary_policy,
    workspace_recovery_environment_digest,
)
from aar.runtime.dispatcher import AttemptFence, DispatchHost, DurableDispatcher
from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.models import (
    OperationRecord,
    RuntimeReady,
    WorkspaceExecuteSpec,
    WorkspaceHandle,
    WorkspaceSnapshot,
)
from aar.runtime.programming import (
    PlainPythonWorkspaceBackend,
    WorkspaceBackend,
    WorkspaceCheckpointRejected,
    WorkspaceOperationConflict,
)
from aar.runtime.registry import OperationRegistry, RegistryError, StaleRuntimeGeneration
from aar.runtime.rlm import RlmEngine, RlmExecutionCancelled
from aar.runtime.worker_manager import WorkerManager
from aar.runtime.workspace import (
    DeterministicWorkspace,
    InvalidWorkspaceMutation,
    StaleWorkspaceGeneration,
    WorkspaceNotFound,
    WorkspaceRevisionConflict,
    WorkspaceSessionMismatch,
)
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointPolicy,
    WorkspaceProgramSpec,
    WorkspaceRestoreSpec,
)
from aar.schemas import (
    AccessMode,
    Budget,
    CapabilityDescriptor,
    CapabilityLimit,
    CapabilitySet,
    FailureCategory,
    FailureEnvelope,
    Grant,
    HostRef,
    LaneRef,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PrincipalRef,
    ReconciliationReport,
    RequestEnvelope,
    SessionRef,
    WorkspaceRef,
)


class ReferenceHostError(RuntimeError):
    pass


class CapabilityMismatch(ReferenceHostError):
    pass


class GrantDenied(ReferenceHostError):
    pass


class BudgetDenied(ReferenceHostError):
    pass


class WorkspaceBindingDenied(ReferenceHostError):
    pass


class DeadlineExpired(ReferenceHostError):
    pass


class InputDigestMismatch(ReferenceHostError):
    pass


class RlmStateMissing(ReferenceHostError):
    """An outer RLM operation lacks durable inner state for a read-only projection."""


class SimulatedProcessLoss(BaseException):
    """Test-only abrupt-loss signal that intentionally bypasses normal failure mapping."""


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


class ReferenceHost:
    """Own fake authority inputs and map them onto one persisted operation lifecycle."""

    def __init__(
        self,
        database_path: Path,
        *,
        now_ms: Callable[[], int] = system_now_ms,
        evidence_records: tuple[str, ...] = (),
        programmable_backend: Literal["plain", "ipython"] = "ipython",
        enable_durable_dispatch: bool = False,
        dispatcher_concurrency: int = 2,
    ) -> None:
        if programmable_backend not in {"plain", "ipython"}:
            raise ValueError(f"unsupported programmable backend: {programmable_backend}")
        self._now_ms = now_ms
        self._recovery_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._runtime_resources_closed = False
        self._closed = False
        self._durable_dispatch_enabled = enable_durable_dispatch
        self._dispatcher_concurrency = dispatcher_concurrency
        self.dispatcher: DurableDispatcher | None = None
        self.registry = OperationRegistry(database_path, now_ms)
        self.runtime_generation = self.registry.start_runtime()
        self.worker_manager = WorkerManager(
            self.registry,
            runtime_generation=self.runtime_generation,
            now_ms=now_ms,
        )
        self.worker_manager.recover_orphans()
        self.workspace = DeterministicWorkspace(database_path)
        self.artifacts = FakeArtifactBroker(database_path)
        self.program_workspace: WorkspaceBackend
        if programmable_backend == "plain":
            self.program_workspace = PlainPythonWorkspaceBackend(
                artifact_sink=self.artifacts.put
            )
        else:
            self.program_workspace = SupervisedIPythonWorkspaceBackend(
                artifact_sink=self.artifacts.put,
                worker_manager=self.worker_manager,
            )
        self.models = FakeModelBroker()
        self.subagents = FakeSubagentBroker(database_path)
        self.effects = FakeEffectBroker()
        self.evidence = FakeEvidenceProvider(evidence_records)
        self.brokers = TypedBrokerFacade(
            database_path,
            artifacts=self.artifacts,
            models=self.models,
            subagents=self.subagents,
            effects=self.effects,
            evidence=self.evidence,
            now_ms=now_ms,
        )
        self.rlm = RlmEngine(database_path, self.brokers, now_ms)
        self.adaptive_assets = AdaptiveAssetStore(database_path)
        self.capabilities = CapabilitySet.issue(
            (
                CapabilityDescriptor(name="artifact.read", access=AccessMode.READ),
                CapabilityDescriptor(name="artifact.write", access=AccessMode.WRITE),
                CapabilityDescriptor(name="asset.import", access=AccessMode.WRITE),
                CapabilityDescriptor(name="asset.read", access=AccessMode.READ),
                CapabilityDescriptor(name="effect.propose", access=AccessMode.WRITE),
                CapabilityDescriptor(name="evidence.query", access=AccessMode.READ),
                CapabilityDescriptor(name="model.request", access=AccessMode.WRITE),
                CapabilityDescriptor(name="operation.cancel", access=AccessMode.WRITE),
                CapabilityDescriptor(name="operation.continuity", access=AccessMode.READ),
                CapabilityDescriptor(name="operation.events", access=AccessMode.READ),
                CapabilityDescriptor(name="operation.reconcile", access=AccessMode.WRITE),
                CapabilityDescriptor(name="operation.status", access=AccessMode.READ),
                CapabilityDescriptor(
                    name="rlm.execute",
                    access=AccessMode.WRITE,
                    limits=(
                        CapabilityLimit(name="model_requests", value=16),
                        CapabilityLimit(name="wall_time_ms", value=60_000),
                    ),
                ),
                CapabilityDescriptor(name="rlm.reconcile", access=AccessMode.WRITE),
                CapabilityDescriptor(name="rlm.status", access=AccessMode.READ),
                CapabilityDescriptor(name="subagent.result", access=AccessMode.READ),
                CapabilityDescriptor(name="subagent.submit", access=AccessMode.WRITE),
                CapabilityDescriptor(name="workspace.create", access=AccessMode.WRITE),
                CapabilityDescriptor(
                    name="workspace.execute",
                    access=AccessMode.WRITE,
                    limits=(CapabilityLimit(name="wall_time_ms", value=60_000),),
                ),
                CapabilityDescriptor(name="workspace.inspect", access=AccessMode.READ),
                CapabilityDescriptor(name="workspace.program.attach", access=AccessMode.READ),
                CapabilityDescriptor(
                    name="workspace.program.checkpoint", access=AccessMode.WRITE
                ),
                CapabilityDescriptor(name="workspace.program.close", access=AccessMode.WRITE),
                CapabilityDescriptor(name="workspace.program.create", access=AccessMode.WRITE),
                CapabilityDescriptor(
                    name="workspace.program.execute",
                    access=AccessMode.WRITE,
                    limits=(CapabilityLimit(name="wall_time_ms", value=60_000),),
                ),
                CapabilityDescriptor(name="workspace.program.health", access=AccessMode.READ),
                CapabilityDescriptor(name="workspace.program.inspect", access=AccessMode.READ),
                CapabilityDescriptor(
                    name="workspace.program.interrupt", access=AccessMode.WRITE
                ),
                CapabilityDescriptor(name="workspace.program.reconcile", access=AccessMode.READ),
                CapabilityDescriptor(name="workspace.program.restore", access=AccessMode.WRITE),
            )
        )
        if enable_durable_dispatch:
            self.start_durable_dispatch()

    def ready(self) -> RuntimeReady:
        return RuntimeReady(
            runtime_generation=self.runtime_generation,
            capabilities=self.capabilities,
        )

    def start_durable_dispatch(self) -> DurableDispatcher:
        """Enable claims after a supervisor has published its exact Ready receipt."""

        with self._lifecycle_lock:
            if self._runtime_resources_closed or self._closed:
                raise ReferenceHostError("reference host is closing")
            if self.dispatcher is not None:
                return self.dispatcher
            self.recover_durable()
            dispatcher = DurableDispatcher(
                cast(DispatchHost, self),
                concurrency=self._dispatcher_concurrency,
            )
            dispatcher.start()
            self.dispatcher = dispatcher
            self._durable_dispatch_enabled = True
            return dispatcher

    def now_ms(self) -> int:
        return self._now_ms()

    def create_workspace(self, workspace: WorkspaceRef, session: SessionRef) -> WorkspaceHandle:
        return self.workspace.create(workspace, session)

    def submit_execute(
        self, envelope: RequestEnvelope, spec: WorkspaceExecuteSpec
    ) -> OperationRecord:
        self._validate_request(envelope, spec)
        payload_json = canonical_json_bytes(spec).decode()
        record, _created = self.registry.accept(envelope, payload_json)
        return record

    def run_execute(
        self,
        operation: OperationRef,
        *,
        failpoint: str | None = None,
    ) -> OperationRecord:
        record = self.registry.get(operation)
        if record.state is not OperationState.ACCEPTED:
            return record
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        if self._now_ms() >= envelope.deadline_unix_ms:
            return self.registry.time_out(operation, self.runtime_generation)
        self.registry.begin(operation, self.runtime_generation)
        if failpoint == "process_loss_before_workspace":
            raise SimulatedProcessLoss()
        spec = WorkspaceExecuteSpec.model_validate_json(record.payload_json, strict=True)
        try:
            result = self.workspace.execute(operation, spec)
        except (StaleWorkspaceGeneration, WorkspaceRevisionConflict) as error:
            failure = FailureEnvelope(
                category=FailureCategory.STALE,
                code=(
                    "STALE_WORKSPACE_GENERATION"
                    if isinstance(error, StaleWorkspaceGeneration)
                    else "WORKSPACE_REVISION_CONFLICT"
                ),
                message=str(error),
                retryable=False,
                certainty=OutcomeCertainty.CERTAIN,
                operation=operation,
            )
            return self.registry.fail(operation, failure, self.runtime_generation)
        except (InvalidWorkspaceMutation, WorkspaceNotFound) as error:
            failure = FailureEnvelope(
                category=FailureCategory.VALIDATION,
                code="INVALID_WORKSPACE_MUTATION",
                message=str(error),
                retryable=False,
                certainty=OutcomeCertainty.CERTAIN,
                operation=operation,
            )
            return self.registry.fail(operation, failure, self.runtime_generation)
        if failpoint == "transport_loss_after_workspace_commit":
            return self.registry.mark_indeterminate(
                operation, self.runtime_generation, "transport_loss_after_workspace_commit"
            )
        return self.registry.succeed(
            operation,
            canonical_json_bytes(result).decode(),
            self.runtime_generation,
        )

    def execute(
        self,
        envelope: RequestEnvelope,
        spec: WorkspaceExecuteSpec,
        *,
        failpoint: str | None = None,
    ) -> OperationRecord:
        record = self.submit_execute(envelope, spec)
        if record.state is not OperationState.ACCEPTED:
            return record
        return self.run_execute(record.operation, failpoint=failpoint)

    def cancel(
        self,
        operation: OperationRef,
        *,
        failpoint: str | None = None,
        requested_by_digest: str | None = None,
        reason_code: str = "user_requested",
    ) -> OperationRecord:
        record = self.registry.get(operation)
        if record.state in {
            OperationState.CANCELLED,
            OperationState.SUCCEEDED,
            OperationState.FAILED,
            OperationState.TIMED_OUT,
            OperationState.INDETERMINATE,
        }:
            return record
        continuity = self.registry.continuity_snapshot(operation)
        if continuity.dispatcher_state is not None:
            self.registry.request_cancel(
                operation,
                requested_by_digest
                or canonical_sha256({"actor": "reference-host-cancel"}),
                reason_code,
            )
            record, cancelled = self.registry.cancel_queued_dispatch(
                operation,
                self.runtime_generation,
            )
            if not cancelled:
                return self.registry.get(operation)
        else:
            record = self.registry.cancel(operation, self.runtime_generation)
        if failpoint == "process_loss_after_outer_cancel":
            raise SimulatedProcessLoss()
        if self.rlm.store.exists(operation):
            self.rlm.store.mark(operation, OperationState.CANCELLED)
        return record

    def status(self, operation: OperationRef) -> OperationRecord:
        return self.registry.get(operation)

    def reconcile(self, operation: OperationRef) -> ReconciliationReport:
        record = self.registry.get(operation)
        if record.state is OperationState.INDETERMINATE:
            receipt = self.workspace.receipt(operation)
            if receipt is not None:
                record = self.registry.succeed(
                    operation,
                    canonical_json_bytes(receipt).decode(),
                    self.runtime_generation,
                    reconciled=True,
                )
            else:
                failure = FailureEnvelope(
                    category=FailureCategory.WORKER,
                    code="NO_WORKSPACE_RECEIPT",
                    message="authoritative workspace store contains no execution receipt",
                    retryable=True,
                    certainty=OutcomeCertainty.CERTAIN,
                    operation=operation,
                )
                record = self.registry.fail(
                    operation,
                    failure,
                    self.runtime_generation,
                    reconciled=True,
                )
        result = None if record.result_json is None else json.loads(record.result_json)
        return ReconciliationReport(
            operation=operation,
            state=record.state,
            certainty=record.certainty,
            runtime_generation=self.runtime_generation,
            workspace_generation=None if result is None else result.get("generation"),
            observed_revision=None if result is None else result.get("revision_after"),
            reconciliation_required=record.reconciliation_required,
        )

    def inspect(self, handle: WorkspaceHandle) -> WorkspaceSnapshot:
        return self.workspace.inspect(handle)

    def submit_rlm(self, envelope: RequestEnvelope, spec: RlmJobSpec) -> OperationRecord:
        self._validate_rlm_request(envelope, spec)
        record, _created = self.registry.accept(
            envelope, canonical_json_bytes(spec).decode()
        )
        self.rlm.ensure(record.operation, spec)
        return record

    def submit_rlm_durable(
        self,
        envelope: RequestEnvelope,
        spec: RlmJobSpec,
    ) -> OperationRecord:
        if self.dispatcher is None:
            raise ReferenceHostError("durable RLM dispatch is not enabled")
        record = self.submit_rlm(envelope, spec)
        policy = rlm_step_boundary_policy()
        self.registry.bind_recovery_policy(
            record.operation,
            operation_kind=RLM_OPERATION_KIND,
            policy=policy,
            environment_digest=rlm_recovery_environment_digest(
                spec=spec,
                capability_digest=self.capabilities.digest,
                policy=policy,
            ),
        )
        if record.state is OperationState.ACCEPTED:
            self.dispatcher.notify(record.operation)
        return record

    def wait_rlm(
        self,
        operation: OperationRef,
        *,
        timeout_s: float | None = None,
    ) -> OperationRecord:
        if self.dispatcher is None:
            raise ReferenceHostError("durable RLM dispatch is not enabled")
        return self.dispatcher.wait(operation, timeout_s=timeout_s)

    def submit_program_workspace_durable(
        self,
        envelope: RequestEnvelope,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
    ) -> OperationRecord:
        if self.dispatcher is None:
            raise ReferenceHostError("durable workspace dispatch is not enabled")
        payload = {
            "handle": handle,
            "kind": WORKSPACE_PROGRAM_OPERATION_KIND,
            "spec": spec,
        }
        self._validate_program_workspace_request(envelope, handle, spec, payload)
        record, _created = self.registry.accept(
            envelope, canonical_json_bytes(payload).decode()
        )
        if spec.checkpoint_replay_safe:
            policy = workspace_checkpoint_boundary_policy()
            self.registry.bind_recovery_policy(
                record.operation,
                operation_kind=WORKSPACE_PROGRAM_OPERATION_KIND,
                policy=policy,
                environment_digest=workspace_recovery_environment_digest(
                    backend=self.program_workspace.descriptor,
                    environment=self.program_workspace.environment,
                    capability_digest=self.capabilities.digest,
                    policy=policy,
                ),
            )
        if record.state is OperationState.ACCEPTED:
            self.dispatcher.notify(
                record.operation,
                kind=WORKSPACE_PROGRAM_OPERATION_KIND,
            )
        return record

    def wait_program_workspace(
        self,
        operation: OperationRef,
        *,
        timeout_s: float | None = None,
    ) -> OperationRecord:
        if self.dispatcher is None:
            raise ReferenceHostError("durable workspace dispatch is not enabled")
        return self.dispatcher.wait(operation, timeout_s=timeout_s)

    def run_claimed(
        self,
        kind: str,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
    ) -> OperationRecord:
        if kind != WORKSPACE_PROGRAM_OPERATION_KIND:
            raise ReferenceHostError(f"unsupported durable dispatch kind: {kind}")
        return self.run_claimed_program_workspace(operation, attempt, fence)

    def run_claimed_program_workspace(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
    ) -> OperationRecord:
        if getattr(attempt, "operation", None) != operation:
            raise ReferenceHostError("claimed attempt does not belong to operation")
        record = self.registry.get(operation)
        if record.state is not OperationState.RUNNING:
            return record
        payload = json.loads(record.payload_json)
        if set(payload) != {"handle", "kind", "spec"}:
            raise ReferenceHostError("workspace operation payload shape is invalid")
        if payload["kind"] != WORKSPACE_PROGRAM_OPERATION_KIND:
            raise ReferenceHostError("workspace operation payload kind is invalid")
        handle = ProgrammableWorkspaceHandle.model_validate(payload["handle"], strict=True)
        spec = WorkspaceProgramSpec.model_validate(payload["spec"], strict=True)
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        boundaries = self.registry.workspace_checkpoint_boundaries(operation)
        if getattr(attempt, "attempt_no", 1) > 1:
            if not boundaries:
                raise ReferenceHostError("workspace successor has no restore boundary")
            handle = boundaries[-1].restored_handle

        if self._now_ms() >= envelope.deadline_unix_ms:
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.TIMED_OUT,
                note="deadline_expired",
            )
        if self.registry.cancellation_requested(operation):
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.CANCELLED,
                note="cancelled",
            )

        self.program_workspace.attach(handle, envelope.session)
        stop_heartbeat = threading.Event()

        def heartbeat() -> None:
            while not stop_heartbeat.wait(10):
                self.registry.heartbeat(
                    attempt,
                    self.runtime_generation,
                    fence.dispatcher_generation,
                    fence.lease_epoch,
                    fence.owner_digest,
                    30_000,
                )

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name=f"aar-workspace-heartbeat-{operation.value}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self.program_workspace.execute(operation, handle, spec)
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join(timeout=1)
        result_json = canonical_json_bytes(result).decode()
        if result.status == "succeeded":
            state = OperationState.SUCCEEDED
            failure = None
            note = "execution_succeeded"
        elif result.status == "interrupted":
            state = OperationState.CANCELLED
            failure = None
            note = "cancelled"
        elif result.status == "timed_out":
            state = OperationState.TIMED_OUT
            failure = None
            note = "deadline_expired"
        elif result.workspace_lost and spec.checkpoint_replay_safe:
            state = OperationState.INDETERMINATE
            failure = None
            note = "workspace_worker_lost"
        else:
            state = OperationState.FAILED
            failure = FailureEnvelope(
                category=FailureCategory.WORKER,
                code="WORKSPACE_PROGRAM_FAILED",
                message="programmable workspace execution failed",
                retryable=False,
                certainty=OutcomeCertainty.CERTAIN,
                operation=operation,
            )
            note = "execution_failed"
        return self.registry.transition_claimed(
            attempt,
            self.runtime_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            state=state,
            result_json=result_json,
            failure=failure,
            note=note,
        )

    def run_rlm(
        self,
        operation: OperationRef,
        *,
        failpoint: str | None = None,
    ) -> OperationRecord:
        record = self.registry.get(operation)
        if record.state is not OperationState.ACCEPTED:
            return record
        spec = RlmJobSpec.model_validate_json(record.payload_json, strict=True)
        self.rlm.ensure(operation, spec)
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        if self._now_ms() >= envelope.deadline_unix_ms:
            self.rlm.store.mark(operation, OperationState.TIMED_OUT)
            return self.registry.time_out(operation, self.runtime_generation)
        self.registry.begin(operation, self.runtime_generation)
        try:
            result = self.rlm.run(operation, envelope, failpoint=failpoint)
        except (BrokerDeadlineExpired, TimeoutError):
            self.rlm.store.mark(operation, OperationState.TIMED_OUT)
            snapshot = self.rlm_status(operation, authoritative_state=OperationState.TIMED_OUT)
            return self.registry.time_out(
                operation,
                self.runtime_generation,
                result_json=canonical_json_bytes(snapshot).decode(),
            )
        except BrokerCallIndeterminate:
            self.rlm.store.mark(operation, OperationState.INDETERMINATE)
            return self.registry.mark_indeterminate(
                operation, self.runtime_generation, "broker_call_indeterminate"
            )
        except BrokerBudgetExceeded as error:
            return self._fail_rlm(
                operation,
                category=FailureCategory.BUDGET,
                code="BROKER_BUDGET_EXCEEDED",
                message=str(error),
            )
        except BrokerGrantDenied as error:
            return self._fail_rlm(
                operation,
                category=FailureCategory.AUTHORITY,
                code="BROKER_GRANT_DENIED",
                message=str(error),
            )
        except BrokerCallConflict as error:
            return self._fail_rlm(
                operation,
                category=FailureCategory.CONFLICT,
                code="BROKER_CALL_CONFLICT",
                message=str(error),
            )
        except RuntimeError as error:
            code = (
                "RLM_STEP_BUDGET_EXHAUSTED"
                if "step bound exhausted" in str(error)
                else "RLM_EXECUTION_FAILED"
            )
            category = (
                FailureCategory.BUDGET
                if code == "RLM_STEP_BUDGET_EXHAUSTED"
                else FailureCategory.WORKER
            )
            return self._fail_rlm(
                operation,
                category=category,
                code=code,
                message=str(error),
            )
        except Exception as error:
            return self._fail_rlm(
                operation,
                category=FailureCategory.INTERNAL,
                code="RLM_INTERNAL_ERROR",
                message=str(error) or type(error).__name__,
            )
        return self.registry.succeed(
            operation,
            canonical_json_bytes(result).decode(),
            self.runtime_generation,
        )

    def run_claimed_rlm(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
    ) -> OperationRecord:
        """Execute one durable RLM claim and commit only through its exact fence."""

        if getattr(attempt, "operation", None) != operation:
            raise ReferenceHostError("claimed attempt does not belong to operation")
        record = self.registry.get(operation)
        if record.state is not OperationState.RUNNING:
            return record
        spec = RlmJobSpec.model_validate_json(record.payload_json, strict=True)
        self.rlm.ensure(operation, spec)
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)

        def heartbeat() -> None:
            self.registry.heartbeat(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                30_000,
            )

        def cancellation_requested() -> bool:
            return self.registry.cancellation_requested(operation)

        if self._now_ms() >= envelope.deadline_unix_ms:
            self.rlm.store.mark(operation, OperationState.TIMED_OUT)
            snapshot = self.rlm_status(
                operation,
                authoritative_state=OperationState.TIMED_OUT,
            )
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.TIMED_OUT,
                result_json=canonical_json_bytes(snapshot).decode(),
                note="deadline_expired",
            )
        try:
            result = self.rlm.run(
                operation,
                envelope,
                cancellation_requested=cancellation_requested,
                heartbeat=heartbeat,
            )
        except RlmExecutionCancelled:
            self.rlm.store.mark(operation, OperationState.CANCELLED)
            snapshot = self.rlm_status(
                operation,
                authoritative_state=OperationState.CANCELLED,
            )
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.CANCELLED,
                result_json=canonical_json_bytes(snapshot).decode(),
                note="cancelled",
            )
        except (BrokerDeadlineExpired, TimeoutError):
            self.rlm.store.mark(operation, OperationState.TIMED_OUT)
            snapshot = self.rlm_status(
                operation,
                authoritative_state=OperationState.TIMED_OUT,
            )
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.TIMED_OUT,
                result_json=canonical_json_bytes(snapshot).decode(),
                note="deadline_expired",
            )
        except BrokerCallIndeterminate:
            self.rlm.store.mark(operation, OperationState.INDETERMINATE)
            return self.registry.transition_claimed(
                attempt,
                self.runtime_generation,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                state=OperationState.INDETERMINATE,
                note="broker_call_indeterminate",
            )
        except BrokerBudgetExceeded as error:
            return self._fail_claimed_rlm(
                operation,
                attempt,
                fence,
                category=FailureCategory.BUDGET,
                code="BROKER_BUDGET_EXCEEDED",
                message=str(error),
            )
        except BrokerGrantDenied as error:
            return self._fail_claimed_rlm(
                operation,
                attempt,
                fence,
                category=FailureCategory.AUTHORITY,
                code="BROKER_GRANT_DENIED",
                message=str(error),
            )
        except BrokerCallConflict as error:
            return self._fail_claimed_rlm(
                operation,
                attempt,
                fence,
                category=FailureCategory.CONFLICT,
                code="BROKER_CALL_CONFLICT",
                message=str(error),
            )
        except RuntimeError as error:
            code = (
                "RLM_STEP_BUDGET_EXHAUSTED"
                if "step bound exhausted" in str(error)
                else "RLM_EXECUTION_FAILED"
            )
            category = (
                FailureCategory.BUDGET
                if code == "RLM_STEP_BUDGET_EXHAUSTED"
                else FailureCategory.WORKER
            )
            return self._fail_claimed_rlm(
                operation,
                attempt,
                fence,
                category=category,
                code=code,
                message=str(error),
            )
        except Exception as error:
            return self._fail_claimed_rlm(
                operation,
                attempt,
                fence,
                category=FailureCategory.INTERNAL,
                code="RLM_INTERNAL_ERROR",
                message=str(error) or type(error).__name__,
            )
        return self.registry.transition_claimed(
            attempt,
            self.runtime_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            state=OperationState.SUCCEEDED,
            result_json=canonical_json_bytes(result).decode(),
            note="execution_succeeded",
        )

    def mark_dispatch_failure_for_kind(
        self,
        kind: str,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        error: BaseException,
    ) -> OperationRecord:
        if kind == RLM_OPERATION_KIND:
            return self.mark_dispatch_failure(operation, attempt, fence, error)
        if kind != WORKSPACE_PROGRAM_OPERATION_KIND:
            raise ReferenceHostError(f"unsupported durable dispatch kind: {kind}")
        return self.registry.park_attempt(
            attempt,
            self.runtime_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            note=f"workspace_dispatch_exception:{type(error).__name__}",
        )

    def mark_dispatch_failure(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        error: BaseException,
    ) -> OperationRecord:
        """Park abrupt worker loss without inferring a terminal failure."""

        if self.rlm.store.exists(operation):
            self.rlm.store.mark(operation, OperationState.INDETERMINATE)
        return self.registry.park_attempt(
            attempt,
            self.runtime_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            note=f"dispatcher_worker_exception:{type(error).__name__}",
        )

    def _fail_claimed_rlm(
        self,
        operation: OperationRef,
        attempt: Any,
        fence: AttemptFence,
        *,
        category: FailureCategory,
        code: str,
        message: str,
    ) -> OperationRecord:
        self.rlm.store.mark(operation, OperationState.FAILED)
        snapshot = self.rlm_status(operation, authoritative_state=OperationState.FAILED)
        failure = FailureEnvelope(
            category=category,
            code=code,
            message=message[:512],
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
        return self.registry.transition_claimed(
            attempt,
            self.runtime_generation,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            state=OperationState.FAILED,
            result_json=canonical_json_bytes(snapshot).decode(),
            failure=failure,
            note="execution_failed",
        )

    def execute_rlm(
        self,
        envelope: RequestEnvelope,
        spec: RlmJobSpec,
        *,
        failpoint: str | None = None,
    ) -> OperationRecord:
        record = self.submit_rlm(envelope, spec)
        if record.state is not OperationState.ACCEPTED:
            return record
        return self.run_rlm(record.operation, failpoint=failpoint)

    def rlm_status(
        self,
        operation: OperationRef,
        *,
        authoritative_state: OperationState | None = None,
    ) -> RlmJobSnapshot:
        record = self.registry.get(operation)
        if not self.rlm.store.exists(operation):
            raise RlmStateMissing(
                "RLM operation has no durable job state; use mutation-authorized reconcile"
            )
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        usage = self.brokers.bind(envelope, operation).usage
        return self.rlm.store.snapshot(
            operation,
            authoritative_state=authoritative_state or record.state,
            usage=usage,
        )

    def recover_durable(self) -> None:
        """Reconcile every registered durable operation kind before dispatch claims."""

        self.recover_durable_effects()
        self.recover_durable_rlm()
        self.recover_durable_workspaces()

    def recover_durable_effects(self) -> tuple[BrokerReconciliationReport, ...]:
        """Reconcile broker receipts without deciding any operation successor."""

        reports: list[BrokerReconciliationReport] = []
        for record in self.registry.list_recovery_candidates():
            operation = record.operation
            if not self.brokers.has_unresolved_calls(operation):
                continue
            try:
                envelope = RequestEnvelope.model_validate_json(
                    record.request_json, strict=True
                )
                report = self.brokers.bind(
                    envelope, operation
                ).reconcile_unresolved(
                    current_capability_digest=self.capabilities.digest
                )
            except (BrokerCallConflict, BrokerGrantDenied, ValueError):
                continue
            reports.append(report)
        return tuple(reports)

    def recover_durable_workspaces(self) -> None:
        """Recover replay-safe programmable workspaces from exact checkpoints."""

        with self._recovery_lock:
            self.registry.fence_expired_attempts(self.runtime_generation)
            for record in self.registry.list_recovery_candidates():
                operation = record.operation
                try:
                    policy_binding = self.registry.recovery_policy_binding(operation)
                except ValueError:
                    continue
                if (
                    policy_binding is None
                    or policy_binding.operation_kind != WORKSPACE_PROGRAM_OPERATION_KIND
                ):
                    continue
                prior_fence = self.registry.latest_recovery_attempt_fence(operation)
                if prior_fence is None:
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="recovery_attempt_fence_missing",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                try:
                    payload = json.loads(record.payload_json)
                    if set(payload) != {"handle", "kind", "spec"}:
                        raise ValueError("workspace recovery payload shape is invalid")
                    if payload["kind"] != WORKSPACE_PROGRAM_OPERATION_KIND:
                        raise ValueError("workspace recovery payload kind is invalid")
                    handle = ProgrammableWorkspaceHandle.model_validate(
                        payload["handle"], strict=True
                    )
                    spec = WorkspaceProgramSpec.model_validate(payload["spec"], strict=True)
                    envelope = RequestEnvelope.model_validate_json(
                        record.request_json, strict=True
                    )
                    manifest = self.registry.latest_workspace_checkpoint(handle)
                    continuity = self.registry.continuity_snapshot(operation)
                    plan = plan_workspace_checkpoint_successor(
                        operation=operation,
                        input_digest=record.input_digest,
                        envelope=envelope,
                        handle=handle,
                        spec=spec,
                        prior_attempt=prior_fence.attempt,
                        policy_binding=policy_binding,
                        current_capability_digest=self.capabilities.digest,
                        current_backend=self.program_workspace.descriptor,
                        current_environment=self.program_workspace.environment,
                        manifest=manifest,
                        cancellation_requested=continuity.control.cancellation_requested,
                        now_unix_ms=self.now_ms(),
                    )
                except (TypeError, ValueError):
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="workspace_recovery_evidence_invalid",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                if plan.decision != "restore_checkpoint":
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision=plan.decision,
                        reason_code=plan.reason_code,
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                assert plan.manifest is not None
                manifest = plan.manifest
                selection = OperationWorkspaceCheckpointSelectionV1.issue(
                    operation=operation,
                    prior_attempt=prior_fence.attempt,
                    runtime_generation=prior_fence.runtime_generation,
                    dispatcher_generation=prior_fence.dispatcher_generation,
                    lease_epoch=prior_fence.lease_epoch,
                    policy_digest=policy_binding.policy_digest,
                    input_digest=record.input_digest,
                    checkpoint_operation=manifest.creation_operation,
                    checkpoint_manifest_digest=manifest.content_digest,
                    source_handle=manifest.source_handle,
                    environment_digest=policy_binding.environment_digest,
                    exclusion_count=len(manifest.exclusions),
                    exclusions_digest=canonical_sha256(manifest.exclusions),
                    artifacts_digest=canonical_sha256(manifest.artifacts),
                    deadline_unix_ms=envelope.deadline_unix_ms,
                    selected_at_unix_ms=record.updated_at_unix_ms,
                )
                restored: ProgrammableWorkspaceHandle | None = None
                try:
                    self.registry.select_workspace_checkpoint(
                        selection,
                        self.runtime_generation,
                    )
                    for artifact in manifest.artifacts:
                        self.artifacts.read(artifact)
                    restored = self.program_workspace.restore(
                        manifest,
                        WorkspaceRestoreSpec(
                            workspace=handle.workspace,
                            session=envelope.session,
                            expected_handle=handle,
                            recover_lost_generation=True,
                        ),
                    )
                    verification = self.program_workspace.checkpoint(
                        OperationRef(
                            value=f"verify-{operation.value}-{prior_fence.attempt.attempt_no}"
                        ),
                        restored,
                        WorkspaceCheckpointPolicy(
                            max_values=1_024,
                            max_bytes=16_777_216,
                            max_depth=32,
                            max_collection_items=65_536,
                        ),
                        f"trace-verify-{operation.value}",
                    )
                    if verification.values != manifest.values or verification.exclusions:
                        raise WorkspaceCheckpointRejected(
                            "restored workspace values do not match checkpoint"
                        )
                    boundary = OperationWorkspaceCheckpointBoundaryV1.issue(
                        operation=operation,
                        prior_attempt=prior_fence.attempt,
                        runtime_generation=prior_fence.runtime_generation,
                        dispatcher_generation=prior_fence.dispatcher_generation,
                        lease_epoch=prior_fence.lease_epoch,
                        policy_digest=policy_binding.policy_digest,
                        input_digest=record.input_digest,
                        checkpoint_operation=manifest.creation_operation,
                        checkpoint_manifest_digest=manifest.content_digest,
                        source_handle=manifest.source_handle,
                        restored_handle=restored,
                        environment_digest=policy_binding.environment_digest,
                        exclusion_count=len(manifest.exclusions),
                        exclusions_digest=canonical_sha256(manifest.exclusions),
                        artifacts_digest=canonical_sha256(manifest.artifacts),
                        deadline_unix_ms=envelope.deadline_unix_ms,
                        created_at_unix_ms=self.now_ms(),
                    )
                    self.registry.mark_workspace_checkpoint_restored(
                        selection,
                        restored,
                        self.runtime_generation,
                    )
                    self.program_workspace.retire_lost_receipt(operation, handle)
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision=plan.decision,
                        reason_code=plan.reason_code,
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                        continuation_boundary=boundary,
                    )
                except (
                    KeyError,
                    RegistryError,
                    WorkspaceCheckpointRejected,
                    WorkspaceOperationConflict,
                ):
                    if restored is not None:
                        self.program_workspace.close(
                            restored, reason="workspace recovery transaction failed"
                        )
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="needs_user",
                        reason_code="workspace_restore_failed",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )

    def recover_durable_rlm(self) -> None:
        """Recover fenced RLM dispatches without guessing unresolved effects."""

        with self._recovery_lock:
            self.registry.fence_expired_attempts(self.runtime_generation)
            for record in self.registry.list_recovery_candidates():
                operation = record.operation
                if not self.rlm.store.exists(operation):
                    continue
                inner = self.rlm.store.snapshot(operation)
                result = self.rlm.store.terminal_result(operation)
                if result is not None:
                    self.registry.recover_terminal_from_receipt(
                        operation,
                        self.runtime_generation,
                        state=OperationState.SUCCEEDED,
                        result_json=canonical_json_bytes(result).decode(),
                        reason_code="rlm_terminal_receipt",
                        input_digest=record.input_digest,
                    )
                    continue
                if inner.state in {
                    OperationState.CANCELLED,
                    OperationState.TIMED_OUT,
                }:
                    snapshot = self.rlm_status(
                        operation,
                        authoritative_state=inner.state,
                    )
                    self.registry.recover_terminal_from_receipt(
                        operation,
                        self.runtime_generation,
                        state=inner.state,
                        result_json=canonical_json_bytes(snapshot).decode(),
                        reason_code="rlm_terminal_marker",
                        input_digest=record.input_digest,
                    )
                    continue
                try:
                    policy_binding = self.registry.recovery_policy_binding(operation)
                except ValueError:
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="recovery_policy_invalid",
                        input_digest=record.input_digest,
                    )
                    continue
                if policy_binding is None:
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="recovery_policy_missing",
                        input_digest=record.input_digest,
                    )
                    continue
                if inner.state is OperationState.FAILED:
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="needs_user",
                        reason_code="rlm_failed_marker_without_outer_receipt",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                prior_fence = self.registry.latest_recovery_attempt_fence(operation)
                if prior_fence is None:
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="recovery_attempt_fence_missing",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                try:
                    envelope = RequestEnvelope.model_validate_json(
                        record.request_json, strict=True
                    )
                    if envelope.capability_digest != self.capabilities.digest:
                        self.registry.requeue_indeterminate(
                            operation,
                            self.runtime_generation,
                            decision="needs_user",
                            reason_code="recovery_capability_digest_mismatch",
                            input_digest=record.input_digest,
                            policy_binding=policy_binding,
                        )
                        continue
                    spec = RlmJobSpec.model_validate_json(record.payload_json, strict=True)
                    broker = self.brokers.bind(envelope, operation)
                    continuity = self.registry.continuity_snapshot(operation)
                    reconciliation = broker.reconcile_unresolved(
                        current_capability_digest=self.capabilities.digest,
                        cancellation_requested=(
                            continuity.control.cancellation_requested
                        )
                    )
                    unresolved_calls = reconciliation.unresolved
                    plan = plan_rlm_step_successor(
                        operation=operation,
                        input_digest=record.input_digest,
                        envelope=envelope,
                        spec=spec,
                        prior_attempt=prior_fence.attempt,
                        prior_runtime_generation=prior_fence.runtime_generation,
                        prior_dispatcher_generation=prior_fence.dispatcher_generation,
                        prior_lease_epoch=prior_fence.lease_epoch,
                        policy_binding=policy_binding,
                        current_capability_digest=self.capabilities.digest,
                        steps=inner.steps,
                        traces=broker.traces(),
                        usage=broker.usage,
                        cancellation_requested=continuity.control.cancellation_requested,
                        unresolved_calls=unresolved_calls,
                        now_unix_ms=self.now_ms(),
                    )
                except (BrokerCallConflict, ValueError):
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision="quarantine",
                        reason_code="recovery_evidence_invalid",
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                if plan.decision != "start_successor":
                    self.registry.requeue_indeterminate(
                        operation,
                        self.runtime_generation,
                        decision=plan.decision,
                        reason_code=plan.reason_code,
                        input_digest=record.input_digest,
                        policy_binding=policy_binding,
                    )
                    continue
                assert plan.boundary is not None
                self.rlm.store.resume(operation)
                self.registry.requeue_indeterminate(
                    operation,
                    self.runtime_generation,
                    decision=plan.decision,
                    reason_code=plan.reason_code,
                    input_digest=record.input_digest,
                    policy_binding=policy_binding,
                    continuation_boundary=plan.boundary,
                )

    def reconcile_broker_calls(
        self,
        operation: OperationRef,
        *,
        current_capability_digest: str | None = None,
        current_compensation_grant: Grant | None = None,
        propose_compensation: bool = False,
    ) -> BrokerReconciliationReport:
        record = self.registry.get(operation)
        if record.state is not OperationState.INDETERMINATE:
            raise BrokerCallConflict(
                "broker reconciliation requires an indeterminate operation"
            )
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        broker = self.brokers.bind(envelope, operation)
        return broker.reconcile_unresolved(
            current_capability_digest=(
                current_capability_digest or self.capabilities.digest
            ),
            current_compensation_grant=current_compensation_grant,
            propose_compensation=propose_compensation,
        )

    def reconcile_rlm(self, operation: OperationRef) -> ReconciliationReport:
        record = self.registry.get(operation)
        spec = RlmJobSpec.model_validate_json(record.payload_json, strict=True)
        self.rlm.ensure(operation, spec)
        continuity = self.registry.continuity_snapshot(operation)
        if continuity.dispatcher_state is not None:
            self.recover_durable_rlm()
            record = self.registry.get(operation)
            if record.state is OperationState.ACCEPTED and self.dispatcher is not None:
                self.dispatcher.notify(operation)
        if record.state in {
            OperationState.CANCELLED,
            OperationState.FAILED,
            OperationState.TIMED_OUT,
        }:
            self.rlm.store.mark(operation, record.state)
        elif record.state is OperationState.INDETERMINATE:
            result = self.rlm.store.terminal_result(operation)
            if result is not None:
                record = self.registry.succeed(
                    operation,
                    canonical_json_bytes(result).decode(),
                    self.runtime_generation,
                    reconciled=True,
                )
            else:
                self.rlm.store.mark(operation, OperationState.INDETERMINATE)
        return ReconciliationReport(
            operation=operation,
            state=record.state,
            certainty=record.certainty,
            runtime_generation=self.runtime_generation,
            reconciliation_required=record.reconciliation_required,
        )

    def request_program_workspace_envelope(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        principal: PrincipalRef,
        session: SessionRef,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
        deadline_unix_ms: int,
    ) -> RequestEnvelope:
        payload = {
            "handle": handle,
            "kind": WORKSPACE_PROGRAM_OPERATION_KIND,
            "spec": spec,
        }
        grant = Grant(
            grant_id=f"grant-{idempotency_key}",
            capability=WORKSPACE_PROGRAM_OPERATION_KIND,
            issued_to=principal,
            expires_at_unix_ms=deadline_unix_ms,
        )
        return RequestEnvelope(
            request_id=request_id,
            idempotency_key=idempotency_key,
            host=HostRef(value="reference-host"),
            principal=principal,
            lane=LaneRef(value="program-workspace"),
            session=session,
            workspace=handle.workspace,
            runtime_generation=self.runtime_generation,
            workspace_generation=handle.generation,
            expected_workspace_revision=handle.revision,
            capability_digest=self.capabilities.digest,
            deadline_unix_ms=deadline_unix_ms,
            grants=(grant,),
            budget=Budget(wall_time_ms=max(0, deadline_unix_ms - self._now_ms())),
            trace_id=f"trace-{request_id}",
            input_digest=canonical_sha256(payload),
        )

    def request_rlm_envelope(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        principal: PrincipalRef,
        session: SessionRef,
        spec: RlmJobSpec,
        deadline_unix_ms: int,
        budget: Budget | None = None,
        parent_operation: OperationRef | None = None,
    ) -> RequestEnvelope:
        capabilities = {"model.request", "rlm.execute"}
        if spec.strategy == "evidence_synthesis":
            capabilities.add("evidence.query")
        grants = tuple(
            sorted(
                (
                    Grant(
                        grant_id=f"grant-{capability.replace('.', '-')}",
                        capability=capability,
                        issued_to=principal,
                        expires_at_unix_ms=deadline_unix_ms,
                    )
                    for capability in capabilities
                ),
                key=lambda grant: grant.grant_id,
            )
        )
        return RequestEnvelope(
            request_id=request_id,
            idempotency_key=idempotency_key,
            host=HostRef(value="reference-host"),
            principal=principal,
            lane=LaneRef(value="rlm"),
            session=session,
            parent_operation=parent_operation,
            runtime_generation=self.runtime_generation,
            capability_digest=self.capabilities.digest,
            deadline_unix_ms=deadline_unix_ms,
            grants=grants,
            budget=budget
            or Budget(
                wall_time_ms=max(0, deadline_unix_ms - self._now_ms()),
                model_requests=4,
                input_tokens=16_384,
                output_tokens=4_096,
                child_operations=0,
                artifact_bytes=0,
            ),
            trace_id=f"trace-{request_id}",
            input_digest=canonical_sha256(spec),
        )

    def submit_asset_import(
        self, envelope: RequestEnvelope, bundle: AdaptiveAssetBundle
    ) -> OperationRecord:
        self._validate_asset_import_request(envelope, bundle)
        record, _created = self.registry.accept(
            envelope, canonical_json_bytes(bundle).decode()
        )
        return record

    def run_asset_import(
        self,
        operation: OperationRef,
        *,
        failpoint: str | None = None,
    ) -> OperationRecord:
        record = self.registry.get(operation)
        if record.state is not OperationState.ACCEPTED:
            return record
        bundle = AdaptiveAssetBundle.model_validate_json(record.payload_json, strict=True)
        envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
        if self._now_ms() >= envelope.deadline_unix_ms:
            return self.registry.time_out(operation, self.runtime_generation)
        self.registry.begin(operation, self.runtime_generation)
        try:
            result = self.adaptive_assets.import_bundle(bundle)
            if failpoint == "process_loss_after_atomic_import":
                raise SimulatedAssetProcessLoss()
        except AssetReferenceMissing as error:
            return self._fail_asset_import(
                operation,
                category=FailureCategory.VALIDATION,
                code="ASSET_REFERENCE_MISSING",
                message=str(error),
            )
        except AssetConflict as error:
            return self._fail_asset_import(
                operation,
                category=FailureCategory.CONFLICT,
                code="ASSET_CONFLICT",
                message=str(error),
            )
        except Exception as error:
            return self._fail_asset_import(
                operation,
                category=FailureCategory.INTERNAL,
                code="ASSET_IMPORT_FAILED",
                message=str(error) or type(error).__name__,
            )
        return self.registry.succeed(
            operation,
            canonical_json_bytes(result).decode(),
            self.runtime_generation,
        )

    def execute_asset_import(
        self, envelope: RequestEnvelope, bundle: AdaptiveAssetBundle
    ) -> OperationRecord:
        accepted = self.submit_asset_import(envelope, bundle)
        return self.run_asset_import(accepted.operation)

    def reconcile_asset_import(self, operation: OperationRef) -> ReconciliationReport:
        record = self.registry.get(operation)
        if record.state is OperationState.INDETERMINATE:
            bundle = AdaptiveAssetBundle.model_validate_json(record.payload_json, strict=True)
            try:
                result = self.adaptive_assets.import_bundle(bundle)
            except AssetReferenceMissing as error:
                record = self._fail_asset_import(
                    operation,
                    category=FailureCategory.VALIDATION,
                    code="ASSET_REFERENCE_MISSING",
                    message=str(error),
                    reconciled=True,
                )
            except AssetConflict as error:
                record = self._fail_asset_import(
                    operation,
                    category=FailureCategory.CONFLICT,
                    code="ASSET_CONFLICT",
                    message=str(error),
                    reconciled=True,
                )
            else:
                record = self.registry.succeed(
                    operation,
                    canonical_json_bytes(result).decode(),
                    self.runtime_generation,
                    reconciled=True,
                )
        return ReconciliationReport(
            operation=operation,
            state=record.state,
            certainty=record.certainty,
            runtime_generation=self.runtime_generation,
            reconciliation_required=record.reconciliation_required,
        )

    def _fail_asset_import(
        self,
        operation: OperationRef,
        *,
        category: FailureCategory,
        code: str,
        message: str,
        reconciled: bool = False,
    ) -> OperationRecord:
        failure = FailureEnvelope(
            category=category,
            code=code,
            message=message[:512],
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
        return self.registry.fail(
            operation,
            failure,
            self.runtime_generation,
            reconciled=reconciled,
        )

    def _fail_rlm(
        self,
        operation: OperationRef,
        *,
        category: FailureCategory,
        code: str,
        message: str,
    ) -> OperationRecord:
        self.rlm.store.mark(operation, OperationState.FAILED)
        snapshot = self.rlm_status(operation, authoritative_state=OperationState.FAILED)
        failure = FailureEnvelope(
            category=category,
            code=code,
            message=message[:512],
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
        return self.registry.fail(
            operation,
            failure,
            self.runtime_generation,
            result_json=canonical_json_bytes(snapshot).decode(),
        )

    def request_envelope(
        self,
        *,
        request_id: str,
        idempotency_key: str,
        principal: PrincipalRef,
        session: SessionRef,
        workspace: WorkspaceHandle,
        spec: WorkspaceExecuteSpec,
        deadline_unix_ms: int,
    ) -> RequestEnvelope:
        grant = Grant(
            grant_id=f"grant-{idempotency_key}",
            capability="workspace.execute",
            issued_to=principal,
            expires_at_unix_ms=deadline_unix_ms,
        )
        return RequestEnvelope(
            request_id=request_id,
            idempotency_key=idempotency_key,
            host=HostRef(value="reference-host"),
            principal=principal,
            lane=LaneRef(value="execute"),
            session=session,
            workspace=workspace.workspace,
            runtime_generation=self.runtime_generation,
            workspace_generation=workspace.generation,
            expected_workspace_revision=workspace.revision,
            capability_digest=self.capabilities.digest,
            deadline_unix_ms=deadline_unix_ms,
            grants=(grant,),
            budget=Budget(wall_time_ms=max(0, deadline_unix_ms - self._now_ms())),
            trace_id=f"trace-{request_id}",
            input_digest=canonical_sha256(spec),
        )

    def _validate_request(
        self, envelope: RequestEnvelope, spec: WorkspaceExecuteSpec
    ) -> None:
        if envelope.runtime_generation != self.runtime_generation:
            raise StaleRuntimeGeneration(
                "runtime generation is "
                f"{self.runtime_generation}, not {envelope.runtime_generation}"
            )
        if envelope.capability_digest != self.capabilities.digest:
            raise CapabilityMismatch("capability digest does not match the reference host")
        if self._now_ms() >= envelope.deadline_unix_ms:
            raise DeadlineExpired("request deadline has expired before acceptance")
        if envelope.budget.wall_time_ms == 0:
            raise BudgetDenied("workspace execution requires a positive wall-time budget")
        remaining_ms = envelope.deadline_unix_ms - self._now_ms()
        if remaining_ms > envelope.budget.wall_time_ms:
            raise BudgetDenied("wall-time budget does not cover the request deadline")
        if envelope.budget.wall_time_ms > 60_000:
            raise BudgetDenied("wall-time budget exceeds the reference-host capability limit")
        matching_grants = [
            grant
            for grant in envelope.grants
            if grant.capability == "workspace.execute"
            and grant.issued_to == envelope.principal
            and grant.expires_at_unix_ms >= envelope.deadline_unix_ms
        ]
        if not matching_grants:
            raise GrantDenied("workspace.execute grant is absent or expired")
        if canonical_sha256(spec) != envelope.input_digest:
            raise InputDigestMismatch("execution payload does not match envelope input digest")
        if envelope.workspace != spec.workspace:
            raise InputDigestMismatch("workspace reference differs between envelope and payload")
        if envelope.workspace_generation != spec.expected_generation:
            raise InputDigestMismatch("workspace generation differs between envelope and payload")
        if envelope.expected_workspace_revision != spec.expected_revision:
            raise InputDigestMismatch("workspace revision differs between envelope and payload")
        try:
            self.workspace.assert_session(spec.workspace, envelope.session)
        except (WorkspaceNotFound, WorkspaceSessionMismatch) as error:
            raise WorkspaceBindingDenied(str(error)) from error

    def _validate_program_workspace_request(
        self,
        envelope: RequestEnvelope,
        handle: ProgrammableWorkspaceHandle,
        spec: WorkspaceProgramSpec,
        payload: dict[str, Any],
    ) -> None:
        if envelope.runtime_generation != self.runtime_generation:
            raise StaleRuntimeGeneration(
                "runtime generation is "
                f"{self.runtime_generation}, not {envelope.runtime_generation}"
            )
        if envelope.capability_digest != self.capabilities.digest:
            raise CapabilityMismatch("capability digest does not match the reference host")
        if self._now_ms() >= envelope.deadline_unix_ms:
            raise DeadlineExpired("request deadline has expired before acceptance")
        if envelope.budget.wall_time_ms == 0:
            raise BudgetDenied("workspace execution requires a positive wall-time budget")
        remaining_ms = envelope.deadline_unix_ms - self._now_ms()
        if remaining_ms > envelope.budget.wall_time_ms:
            raise BudgetDenied("wall-time budget does not cover the request deadline")
        if spec.wall_time_ms > envelope.budget.wall_time_ms:
            raise BudgetDenied("workspace wall-time exceeds the request budget")
        if envelope.budget.wall_time_ms > 60_000:
            raise BudgetDenied("wall-time budget exceeds the reference-host capability limit")
        matching = [
            grant
            for grant in envelope.grants
            if grant.capability == WORKSPACE_PROGRAM_OPERATION_KIND
            and grant.issued_to == envelope.principal
            and grant.expires_at_unix_ms >= envelope.deadline_unix_ms
        ]
        if not matching:
            raise GrantDenied("workspace.program.execute grant is absent or expired")
        if canonical_sha256(payload) != envelope.input_digest:
            raise InputDigestMismatch("workspace payload does not match envelope input digest")
        if (
            envelope.workspace != handle.workspace
            or envelope.workspace_generation != handle.generation
            or envelope.expected_workspace_revision != handle.revision
        ):
            raise InputDigestMismatch("workspace envelope does not match the exact handle")
        try:
            self.program_workspace.attach(handle, envelope.session)
        except (WorkspaceNotFound, WorkspaceSessionMismatch) as error:
            raise WorkspaceBindingDenied(str(error)) from error

    def _validate_rlm_request(
        self, envelope: RequestEnvelope, spec: RlmJobSpec
    ) -> None:
        if envelope.runtime_generation != self.runtime_generation:
            raise StaleRuntimeGeneration(
                "runtime generation is "
                f"{self.runtime_generation}, not {envelope.runtime_generation}"
            )
        if envelope.capability_digest != self.capabilities.digest:
            raise CapabilityMismatch("capability digest does not match the reference host")
        if self._now_ms() >= envelope.deadline_unix_ms:
            raise DeadlineExpired("request deadline has expired before acceptance")
        if envelope.budget.wall_time_ms == 0:
            raise BudgetDenied("RLM execution requires a positive wall-time budget")
        remaining_ms = envelope.deadline_unix_ms - self._now_ms()
        if remaining_ms > envelope.budget.wall_time_ms:
            raise BudgetDenied("wall-time budget does not cover the request deadline")
        if envelope.budget.wall_time_ms > 60_000:
            raise BudgetDenied("wall-time budget exceeds the reference-host capability limit")
        if envelope.budget.model_requests > 16:
            raise BudgetDenied("model-request budget exceeds the reference-host capability limit")
        required = {"model.request", "rlm.execute"}
        if spec.strategy == "evidence_synthesis":
            required.add("evidence.query")
        granted = {
            grant.capability
            for grant in envelope.grants
            if grant.issued_to == envelope.principal
            and grant.expires_at_unix_ms >= envelope.deadline_unix_ms
        }
        missing = sorted(required - granted)
        if missing:
            raise GrantDenied("required RLM grants are absent: " + ", ".join(missing))
        if canonical_sha256(spec) != envelope.input_digest:
            raise InputDigestMismatch("RLM payload does not match envelope input digest")
        if envelope.workspace is not None:
            raise WorkspaceBindingDenied(
                "portable RLM execution is not implicitly bound to a workspace"
            )

    def _validate_asset_import_request(
        self, envelope: RequestEnvelope, bundle: AdaptiveAssetBundle
    ) -> None:
        if envelope.runtime_generation != self.runtime_generation:
            raise StaleRuntimeGeneration(
                "runtime generation is "
                f"{self.runtime_generation}, not {envelope.runtime_generation}"
            )
        if envelope.capability_digest != self.capabilities.digest:
            raise CapabilityMismatch("capability digest does not match the reference host")
        if self._now_ms() >= envelope.deadline_unix_ms:
            raise DeadlineExpired("request deadline has expired before acceptance")
        if envelope.budget.wall_time_ms == 0:
            raise BudgetDenied("asset import requires a positive wall-time budget")
        remaining_ms = envelope.deadline_unix_ms - self._now_ms()
        if remaining_ms > envelope.budget.wall_time_ms:
            raise BudgetDenied("wall-time budget does not cover the request deadline")
        if envelope.budget.wall_time_ms > 60_000:
            raise BudgetDenied("wall-time budget exceeds the reference-host capability limit")
        matching = [
            grant
            for grant in envelope.grants
            if grant.capability == "asset.import"
            and grant.issued_to == envelope.principal
            and grant.expires_at_unix_ms >= envelope.deadline_unix_ms
        ]
        if not matching:
            raise GrantDenied("asset.import grant is absent or expired")
        if canonical_sha256(bundle) != envelope.input_digest:
            raise InputDigestMismatch("asset bundle does not match envelope input digest")
        if envelope.workspace is not None:
            raise WorkspaceBindingDenied("asset import is not implicitly bound to a workspace")

    def drain_runtime_resources(self) -> None:
        """Stop claims and workers while keeping registry truth writable for a terminal receipt."""

        with self._lifecycle_lock:
            if self._runtime_resources_closed:
                return
            self._runtime_resources_closed = True
        if self.dispatcher is not None:
            self.dispatcher.close()
        self.program_workspace.shutdown()
        self.adaptive_assets.close()
        self.rlm.close()
        self.brokers.close()
        self.subagents.close()
        self.artifacts.close()
        self.workspace.close()

    def close(self) -> None:
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
        self.drain_runtime_resources()
        self.registry.close()
