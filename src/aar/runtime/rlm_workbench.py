"""Durable RLM-native workbench coordination for the successor v8 surface."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.continuity_models import OperationAttemptRefV1
from aar.provider_ready_contract import (
    ProviderReadyContractError,
    load_provider_ready_json_bytes,
)
from aar.rlm_workbench_models import (
    ArtifactBinding,
    CallerWorkTicket,
    RecoveryPlannerInput,
    RlmDirective,
    RlmWorkbenchExecuteInput,
    RlmWorkbenchJobSpec,
    RlmWorkbenchResult,
    RlmWorkbenchSnapshot,
    WorkspaceBrokerFrame,
    _contract_documents,
    derive_cumulative_deadline_unix_ms,
)
from aar.runtime.artifact_publication import (
    ArtifactPublicationConflict,
    ArtifactPublicationRepository,
    read_caller_planner_trace,
)
from aar.runtime.caller_work import CallerWorkConflict, CallerWorkRepository
from aar.runtime.completion import workbench_result_document
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.ipython_backend import (
    SupervisedIPythonWorkspaceBackend,
    WorkspaceBrokerSession,
    WorkspaceBrokerSuspended,
)
from aar.runtime.registry import (
    InvalidTransition,
    OperationRegistry,
    PlannerOwner,
    StaleAttemptFence,
)
from aar.runtime.sqlite_repository import SQLiteConnectionFactory
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceProgramResult,
    WorkspaceProgramSpec,
    WorkspaceRestoreSpec,
)
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    FailureCategory,
    FailureEnvelope,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    RequestEnvelope,
    SessionRef,
    WorkspaceRef,
)

_WORKBENCH_ATTEMPT_LEASE_DURATION_MS = 30_000
_WORKBENCH_ATTEMPT_LEASE_HEARTBEAT_INTERVAL_MS = 10_000


class RlmWorkbenchError(RuntimeError):
    """Base error for successor workbench coordination."""


class RlmWorkbenchConflict(RlmWorkbenchError):
    """Persisted workbench authority disagrees with the requested binding."""


class _InvalidCompletionSelection(RlmWorkbenchError):
    """A committed helper selected no artifacts despite the job contract."""

    def __init__(self, cell_id: str, result_digest: str, manifest_digest: str) -> None:
        self.proof = (cell_id, result_digest, manifest_digest)
        super().__init__("completion requires named artifacts")


class RlmWorkbenchDeadlineExceeded(RlmWorkbenchConflict):
    """The cumulative deadline fenced further workbench side effects."""


class RlmWorkbenchModelCallBudgetExceeded(RlmWorkbenchConflict):
    """The root planner has no remaining model-call allowance."""


class RlmWorkbenchCellBudgetExceeded(RlmWorkbenchConflict):
    """A new cell was rejected before any preparation or execution."""


class RlmWorkbenchPromptCapacityExceeded(RlmWorkbenchConflict):
    """Mandatory context cannot fit before a planner ticket or send exists."""

    def __init__(self, observed_characters: int, maximum_characters: int = 65_536) -> None:
        self.observed_characters = observed_characters
        self.maximum_characters = maximum_characters
        # Never echo objective, output contract, source or model content.
        super().__init__("mandatory workbench planner context exceeds request capacity")


class RlmWorkbenchPlannerAbstained(RlmWorkbenchError):
    """A validated planner directive deliberately declined further work."""

    def __init__(self, reason: str) -> None:
        # The native model journal retains the response. Public failures must
        # not echo its arbitrary (potentially sensitive) reason text.
        self.reason_digest = canonical_sha256(reason)
        super().__init__("workbench planner abstained")


class RlmWorkbenchUnsupported(RlmWorkbenchError):
    """The requested workbench mode is outside the implemented backend boundary."""


class RlmWorkbenchPlanner(Protocol):
    """Inject one already-authorized planner receipt at a time."""

    def plan(
        self,
        operation: OperationRef,
        spec: RlmWorkbenchJobSpec,
        snapshot: RlmWorkbenchSnapshot,
    ) -> Mapping[str, Any] | RlmDirective: ...


class RlmWorkbenchRecoveryPlanner(Protocol):
    """Produce one continuation directive from exact durable recovery facts."""

    def recover(
        self,
        operation: OperationRef,
        spec: RlmWorkbenchJobSpec,
        recovery_input: RecoveryPlannerInput,
    ) -> Mapping[str, Any] | RlmDirective: ...


@dataclass(slots=True)
class _ActiveCell:
    operation: OperationRef
    attempt: OperationAttemptRefV1
    fence: AttemptFence
    cell_execution_id: str
    handle: ProgrammableWorkspaceHandle
    session: WorkspaceBrokerSession
    broker_call_ordinal: int = 0
    artifact_ordinal: int = 0
    max_artifact_count: int = 0
    max_artifact_bytes: int = 0
    allowed_artifact_media_types: tuple[str, ...] = ()


@dataclass(frozen=True)
class _RecoveryContinuation:
    planner_input: RecoveryPlannerInput
    consumed_model_calls: int
    failed_source_digest: str


_LEGACY_PHASE = {
    "accepted": "accepted",
    "preparing_workspace": "accepted",
    "running": "running",
    "waiting_external": "accepted",
    "checkpointing": "running",
    "finalizing": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "timed_out": "timed_out",
    "indeterminate": "indeterminate",
    "parked": "indeterminate",
}
_TERMINAL_PHASES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "indeterminate", "parked"}
)


def system_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _workspace_id(operation: OperationRef) -> str:
    suffix = hashlib.sha256(operation.value.encode("utf-8")).hexdigest()[:32]
    return f"rlm-wb-{suffix}"


def normalize_planner_mode(execution_mode: str) -> str:
    """Map the job spelling to the one executable planner owner."""

    if execution_mode == "caller_delegated":
        return "caller_delegated_ticketed"
    if execution_mode == "service_managed":
        return "service_managed"
    raise RlmWorkbenchUnsupported(f"unsupported workbench planner mode: {execution_mode!r}")


def _cell_result_committable(
    result: WorkspaceProgramResult,
    handle: ProgrammableWorkspaceHandle,
    execution_operation: OperationRef,
) -> bool:
    """Commit a completed receipt, never reinterpret a failed execution as success."""
    if result.workspace_lost:
        return False
    if result.status == "succeeded":
        return True
    return (
        result.status == "failed"
        and result.operation == execution_operation
        and result.workspace == handle.workspace
        and result.backend == handle.backend
        and result.generation == handle.generation
        and result.revision_before == handle.revision
        and result.revision_after == handle.revision + 1
        and any(event.kind == "exception" for event in result.events)
        and all(
            event.kind != "exception"
            or (isinstance(event.data, dict) and event.data.get("origin") == "user_code")
            for event in result.events
        )
    )


def _planner_prompt(document: dict[str, Any]) -> str:
    """Fit optional source feedback without weakening mandatory context."""
    prompt = canonical_json_bytes(document).decode("utf-8")
    cell = document.get("last_committed_cell")
    if len(prompt) > 65536 and cell is not None and "source" in cell:
        cell.pop("source")
        cell["source_omitted"] = (
            "Full source omitted due to prompt capacity; source_digest "
            "identifies the original directive."
        )
        prompt = canonical_json_bytes(document).decode("utf-8")
    if len(prompt) > 65_536:
        raise RlmWorkbenchPromptCapacityExceeded(len(prompt))
    return prompt


def _planner_request(
    spec: RlmWorkbenchJobSpec,
    *,
    owner: Mapping[str, Any],
    snapshot: RlmWorkbenchSnapshot,
    last_committed_cell: Mapping[str, Any] | None,
    committed_artifacts: list[dict[str, str]] | None = None,
    remaining_cells: int | None = None,
) -> dict[str, Any]:
    schema_document = _contract_documents()["aar-rlm-workbench-v1.schema.json"]
    response_schema = schema_document["$defs"]["RlmDirective"]
    route = dict(spec.root["model"]["route_binding"])
    prompt_document = {
        "objective": str(spec.root["objective"]),
        "completion_contract": spec.root["completion"],
        "committed_artifacts": committed_artifacts or [],
        "planner_owner": dict(owner),
        "workbench_snapshot": snapshot.root,
        "last_committed_cell": (None if last_committed_cell is None else dict(last_committed_cell)),
        "execution_budget": {
            **dict(spec.root["budgets"]),
            "remaining_cells": int(spec.root["budgets"]["max_cells"])
            if remaining_cells is None
            else remaining_cells,
            "remaining_model_calls": max(
                0, int(spec.root["budgets"]["max_model_calls"]) - int(owner["step_index"])
            ),
        },
        "artifact_policy": {
            **dict(spec.root["artifacts"]),
            "max_artifact_count": int(spec.root["budgets"]["max_artifact_count"]),
            "max_artifact_bytes": int(spec.root["budgets"]["max_artifact_bytes"]),
        },
        "instructions": (
            "Return one directive per response, not the entire task in one response. "
            "Match response_contract; use successive cells for multi-step tasks. "
            "remaining_model_calls includes this request. "
            "Abstain only if genuinely blocked. Choose one completion route: "
            "inline aar_complete(output, named_artifacts={name: (content, media_type)}) "
            "stages and selects final bytes in that cell; do not manually stage those files first. "
            "Or manually aar_artifact, then return a root finalize directive with actual "
            "committed stage IDs. aar_complete(output, None) selects no existing stages; "
            "it cannot satisfy require_named_artifacts=True. "
            "Completion takes effect only after the cell succeeds and commits. "
            "Use execute_cell in persistent IPython. For result.status='failed', read source "
            "and exception before correcting or abstaining; namespace and revision may change, "
            "so do not assume rollback or replay external effects. "
            "Failed-cell artifact stages are not eligible for finalization. "
            "use finalize only when the output contract can be satisfied. "
            "These are injected global callables, not importable modules: "
            "aar_artifact(name, content, media_type='application/octet-stream'), "
            "aar_display(value, media_type='application/json'), "
            "aar_progress(message, *, completed=None, total=None), and aar_broker(value). "
            "Call directly; normal imports work. "
            "aar_artifact accepts str or bytes content and returns an artifact_index; "
            "follow artifact_policy and explicitly choose an allowed media_type. "
            "A helper default is not an authorization; nor are filename extensions. "
            "Artifact count/byte budgets are cumulative across all cells, including failed-cell "
            "stages. Prepare and test drafts before staging final bytes; staging is not draft "
            "editing. Under duplicate_name_policy='reject', stage each logical name at most once. "
            "After staging, finalize with committed stage IDs instead of restaging files. "
            "Indices are not stage IDs. See committed_artifacts and "
            "last_committed_cell.artifact_stage_ids when finalizing. "
            "aar_broker requires a configured host broker and its authority."
        ),
    }
    return {
        "method": "model.request",
        "contract_id": "aar.broker-contract.model-request.v2",
        "prompt": _planner_prompt(prompt_document),
        "max_output_bytes": int(spec.root["budgets"]["max_result_bytes"]),
        "response_contract": {
            "dialect": "https://json-schema.org/draft/2020-12/schema",
            "max_instance_bytes": int(spec.root["budgets"]["max_result_bytes"]),
            "profile": "aar.json-schema-profile.v1",
            "schema": response_schema,
            "schema_digest": canonical_sha256(response_schema),
            "schema_version": "aar.json-contract.v1",
        },
        "route_binding": route,
    }


def _attempt_fence_digest(fence: AttemptFence) -> str:
    return canonical_sha256(
        {
            "dispatcher_generation": fence.dispatcher_generation,
            "lease_epoch": fence.lease_epoch,
            "owner_digest": fence.owner_digest,
        }
    )


class RlmWorkbenchCoordinator:
    """Own workbench admission and operation-scoped workspace preparation.

    Planner, cell execution, external waiting, and finalization are added as
    later vertical slices.  This owner already makes admission durable and
    restart-safe: a retry either attaches to the exact stored generation or
    finishes the same in-progress preparation.
    """

    def __init__(
        self,
        database_path: Path,
        workspace_backend: SupervisedIPythonWorkspaceBackend,
        caller_work: CallerWorkRepository,
        registry: OperationRegistry,
        *,
        now_ms: Callable[[], int] = system_now_ms,
        runtime_generation: int | None = None,
        planner: RlmWorkbenchPlanner | None = None,
        barrier: Callable[[str], None] | None = None,
    ) -> None:
        if workspace_backend.descriptor.kind != "ipython":
            raise RlmWorkbenchUnsupported("RLM workbench requires an IPython backend")
        self.database_path = database_path.resolve()
        self._backend = workspace_backend
        self._caller_work = caller_work
        self._registry = registry
        self._runtime_generation = (
            registry.current_runtime_generation()
            if runtime_generation is None
            else runtime_generation
        )
        self._factory = SQLiteConnectionFactory(self.database_path)
        self._artifact_publications = ArtifactPublicationRepository(
            self.database_path, now_ms=now_ms
        )
        self._now_ms = now_ms
        self._planner = planner
        self._barrier = barrier or (lambda _name: None)
        self._active_lock = threading.RLock()
        self._active_cells: dict[str, _ActiveCell] = {}
        self._broker_sessions: dict[str, WorkspaceBrokerSession] = {}
        self._dispatch_notifier: Callable[[OperationRef, str], None] | None = None
        self._deadline_terminalizer: Callable[[OperationRef], None] | None = None
        self._caller_work.bind_settlement_handler(self.notify_caller_settlement)
        bind_artifact_stage_handler = getattr(self._backend, "bind_artifact_stage_handler", None)
        self._artifact_stage_callback = self._stage_worker_artifact
        if callable(bind_artifact_stage_handler):
            bind_artifact_stage_handler(self._artifact_stage_callback)
        self.schedule_pending_successors()

    @contextmanager
    def _active_cell_binding(
        self,
        execution_operation: OperationRef,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        handle: ProgrammableWorkspaceHandle,
        spec: RlmWorkbenchJobSpec,
        session: WorkspaceBrokerSession,
        *,
        cell_execution_id: str,
        broker_call_ordinal: int = 0,
    ) -> Iterator[None]:
        active = _ActiveCell(
            operation=operation,
            attempt=attempt,
            fence=fence,
            cell_execution_id=cell_execution_id,
            handle=handle,
            session=session,
            broker_call_ordinal=broker_call_ordinal,
            max_artifact_count=int(spec.root["budgets"]["max_artifact_count"]),
            max_artifact_bytes=int(spec.root["budgets"]["max_artifact_bytes"]),
            allowed_artifact_media_types=tuple(spec.root["artifacts"]["allowed_media_types"]),
        )
        with self._active_lock:
            if execution_operation.value in self._active_cells:
                raise RlmWorkbenchConflict("cell execution already has an active broker context")
            self._active_cells[execution_operation.value] = active
        try:
            yield
        finally:
            with self._active_lock:
                if self._active_cells.get(execution_operation.value) is active:
                    del self._active_cells[execution_operation.value]

    def _stage_worker_artifact(
        self,
        execution_operation: OperationRef,
        logical_name: str,
        content: bytes,
        media_type: str,
    ) -> ArtifactReference | None:
        with self._active_lock:
            active = self._active_cells.get(execution_operation.value)
            if active is None:
                return None
            active.artifact_ordinal += 1
            ordinal = active.artifact_ordinal
            operation = active.operation
            handle = active.handle
            cell_execution_id = active.cell_execution_id
            max_artifact_count = active.max_artifact_count
            max_artifact_bytes = active.max_artifact_bytes
            allowed_media_types = active.allowed_artifact_media_types
        if media_type not in allowed_media_types:
            raise RlmWorkbenchConflict("workbench artifact media type is not allowed")
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        with self._factory.transaction(write=False) as connection:
            count, used, same_name = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(size_bytes), 0), "
                "COALESCE(SUM(CASE WHEN logical_name = ? THEN 1 ELSE 0 END), 0) "
                "FROM rlm_workbench_artifact_stages WHERE operation_id = ?",
                (logical_name, operation.value),
            ).fetchone()
        if int(count) >= max_artifact_count:
            raise RlmWorkbenchConflict("workbench artifact count budget exhausted")
        if int(same_name):
            raise RlmWorkbenchConflict("workbench artifact logical name is already staged")
        if int(used) + len(content) > max_artifact_bytes:
            raise RlmWorkbenchConflict("workbench artifact byte budget exhausted")
        binding = ArtifactBinding.model_validate(
            {
                "schema_version": "aar.artifact-binding.v1",
                "logical_name": logical_name,
                "media_type": media_type,
                "digest": digest,
                "size_bytes": len(content),
                "operation": operation.model_dump(mode="json"),
                "workspace": handle.workspace.model_dump(mode="json"),
                "workspace_generation": handle.generation,
                "cell_execution_id": cell_execution_id,
                "role": "result",
            },
            strict=True,
        )
        stage_id = (
            "stage-"
            + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "operation": operation.model_dump(mode="json"),
                        "cell_execution_id": cell_execution_id,
                        "ordinal": ordinal,
                        "logical_name": logical_name,
                        "digest": digest,
                    }
                )
            ).hexdigest()[:48]
        )
        self._artifact_publications.stage_content(
            stage_id=stage_id, binding=binding, content=content
        )
        return ArtifactReference(
            artifact=ArtifactIdRef(value="artifact-" + digest.removeprefix("sha256:")[:32]),
            digest=digest,
            media_type=media_type,
            size_bytes=len(content),
            created_by=operation,
            redacted=False,
        )

    def bind_runtime_callbacks(
        self,
        *,
        dispatch_notifier: Callable[[OperationRef, str], None],
        deadline_terminalizer: Callable[[OperationRef], None],
    ) -> None:
        """Bind live liveness callbacks after durable dispatch is started."""

        self._dispatch_notifier = dispatch_notifier
        self._deadline_terminalizer = deadline_terminalizer

    def notify_caller_settlement(self, ticket: CallerWorkTicket) -> None:
        document = dict(ticket.root)
        if document["state"] not in {
            "settled_success",
            "settled_failure",
            "cancelled_certain",
        }:
            return
        operation = OperationRef.model_validate(document["operation"], strict=True)
        with self._factory.transaction(write=False) as connection:
            outbox = connection.execute(
                """
                SELECT state FROM rlm_workbench_successor_outbox
                WHERE operation_id = ? AND suspension_revision = ?
                  AND settlement_digest = ?
                """,
                (
                    operation.value,
                    document["suspension_revision"],
                    document["settled_receipt_digest"],
                ),
            ).fetchone()
            job = connection.execute(
                "SELECT phase, cumulative_deadline_unix_ms "
                "FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        if outbox is None:
            if job is not None and str(job["phase"]) == "timed_out":
                terminalizer = self._deadline_terminalizer
                if terminalizer is not None:
                    terminalizer(operation)
                return
            if (
                job is not None
                and str(job["phase"]) == "waiting_external"
                and self._now_ms() >= int(job["cumulative_deadline_unix_ms"])
            ):
                # A startup deadline sweep already classified a may-have-sent
                # ticket as reconcile-only. Its late certain receipt is
                # durable evidence, not authority to create a continuation.
                return
            raise RlmWorkbenchConflict("settled caller ticket has no successor outbox")
        if str(outbox["state"]) == "pending":
            self._registry.request_dispatch(operation, "rlm.workbench.execute")
            notifier = self._dispatch_notifier
            if notifier is not None:
                notifier(operation, "rlm.workbench.execute")

    def schedule_pending_successors(self) -> None:
        with self._factory.transaction(write=False) as connection:
            rows = connection.execute(
                """
                SELECT DISTINCT outbox.operation_id
                FROM rlm_workbench_successor_outbox AS outbox
                JOIN rlm_workbench_jobs AS job
                  ON job.operation_id = outbox.operation_id
                JOIN operations AS operation
                  ON operation.operation_id = outbox.operation_id
                WHERE outbox.state = 'pending'
                  AND job.phase = 'accepted'
                  AND operation.state = 'accepted'
                ORDER BY outbox.operation_id
                """
            ).fetchall()
        for row in rows:
            self._registry.request_dispatch(
                OperationRef(value=str(row["operation_id"])), "rlm.workbench.execute"
            )

    def cancel_pending_caller_work(
        self,
        operation: OperationRef,
        *,
        reason: str,
    ) -> CallerWorkTicket | None:
        """Settle an unsent caller ticket before outer cancellation takes authority."""

        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                """
                SELECT outer_operation.payload_json, job.context_digest,
                       job.control_revision,
                       job.cancellation_revision, job.cumulative_deadline_unix_ms,
                       suspension.suspension_revision, suspension.ticket_id
                FROM rlm_workbench_jobs AS job
                JOIN operations AS outer_operation USING(operation_id)
                JOIN rlm_workbench_suspensions AS suspension USING(operation_id)
                JOIN caller_work_tickets AS ticket
                  ON ticket.ticket_id = suspension.ticket_id
                WHERE job.operation_id = ?
                  AND suspension.state = 'pending'
                  AND ticket.state IN ('pending', 'send_reserved')
                ORDER BY suspension.suspension_revision DESC
                LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
        if row is None:
            return None
        ticket = self._caller_work.get(str(row["ticket_id"]))
        payload = json.loads(str(row["payload_json"]))
        context = payload["context"]
        if canonical_sha256(context) != str(row["context_digest"]):
            raise RlmWorkbenchConflict(
                "durable workbench context does not match its admission digest"
            )
        claimant = ticket.claimant
        physical = ticket.physical_attempt
        command = {
            "context": context,
            "operation": operation.model_dump(mode="json"),
            "expected_control_revision": int(row["control_revision"]),
            "expected_cancellation_revision": int(row["cancellation_revision"]),
            "expected_suspension_revision": int(row["suspension_revision"]),
            "expected_cumulative_deadline_unix_ms": int(row["cumulative_deadline_unix_ms"]),
            "ticket_id": ticket.ticket_id,
            "expected_revision": ticket.revision,
            "ticket_digest": ticket.ticket_digest,
            "idempotency_key": "wb-cancel-"
            + canonical_sha256(
                {
                    "operation": operation.model_dump(mode="json"),
                    "ticket_id": ticket.ticket_id,
                    "revision": ticket.revision,
                }
            )[7:39],
            "expected_pre_send_state": ticket.state,
            "claim_id": None if claimant is None else claimant.claim_id,
            "claim_fence": None if claimant is None else claimant.claim_fence,
            "physical_attempt_id": (None if physical is None else physical.physical_attempt_id),
            "expected_claim_expires_at_unix_ms": (
                None if claimant is None else claimant.claim_expires_at_unix_ms
            ),
            "settled_receipt_digest": canonical_sha256(
                {
                    "kind": "workbench_cancelled_before_send",
                    "operation": operation.model_dump(mode="json"),
                    "ticket_id": ticket.ticket_id,
                    "revision": ticket.revision,
                    "reason": reason,
                }
            ),
            "settled_at_unix_ms": self._now_ms(),
            "reason": reason,
        }
        try:
            return self._caller_work.cancel_before_send(command)
        except CallerWorkConflict:
            return None

    def sweep_expired_caller_work(
        self,
    ) -> tuple[
        tuple[OperationRef, ...],
        tuple[OperationRef, ...],
        tuple[OperationRef, ...],
    ]:
        """Classify expired caller work before durable dispatch starts.

        Certain no-send tickets can terminalize the workbench deadline directly.
        Eligible known settlements remain successor-owned so deadline precedence
        is projected by the fenced prepared-to-consumed transaction. May-have-sent
        tickets remain explicitly uncertain.
        """

        now = self._now_ms()
        with self._factory.transaction(write=False) as connection:
            rows = connection.execute(
                """
                SELECT job.operation_id, job.phase, job.control_revision,
                       job.cancellation_requested, ticket.ticket_id,
                       ticket.revision AS ticket_revision, ticket.state AS ticket_state
                FROM rlm_workbench_jobs AS job
                JOIN operations AS outer USING(operation_id)
                JOIN rlm_workbench_suspensions AS suspension USING(operation_id)
                JOIN caller_work_tickets AS ticket
                  ON ticket.ticket_id = suspension.ticket_id
                WHERE outer.state = 'accepted'
                  AND job.cumulative_deadline_unix_ms <= ?
                  AND suspension.suspension_revision = (
                    SELECT MAX(latest.suspension_revision)
                    FROM rlm_workbench_suspensions AS latest
                    WHERE latest.operation_id = job.operation_id
                  )
                ORDER BY job.operation_id
                """,
                (now,),
            ).fetchall()
        certain: list[OperationRef] = []
        uncertain: list[OperationRef] = []
        successor_owned: list[OperationRef] = []
        successor_ticket_states = {
            "settled_success",
            "settled_failure",
            "cancelled_certain",
        }
        for row in rows:
            operation = OperationRef(value=str(row["operation_id"]))
            if bool(row["cancellation_requested"]):
                uncertain.append(operation)
                continue
            ticket_state = str(row["ticket_state"])
            if ticket_state in {"pending", "send_reserved"}:
                cancelled = self.cancel_pending_caller_work(
                    operation,
                    reason="deadline",
                )
                if cancelled is None:
                    current = self._caller_work.get(str(row["ticket_id"]))
                    ticket_state = current.state
                else:
                    ticket_state = cancelled.state
            elif ticket_state == "send_started":
                try:
                    current = self._caller_work.mark_outcome_unknown(
                        ticket_id=str(row["ticket_id"]),
                        expected_revision=int(row["ticket_revision"]),
                    )
                    ticket_state = current.state
                except CallerWorkConflict:
                    ticket_state = self._caller_work.get(str(row["ticket_id"])).state
            if ticket_state == "cancelled_before_send":
                self.mark_deadline_terminal(operation)
                certain.append(operation)
            elif ticket_state in successor_ticket_states:
                self._registry.request_dispatch(operation, "rlm.workbench.execute")
                successor_owned.append(operation)
            elif ticket_state in {"cancel_requested", "outcome_unknown", "quarantined"}:
                uncertain.append(operation)
            else:
                raise RlmWorkbenchConflict(
                    f"expired caller ticket has unsupported state {ticket_state!r}"
                )
        return tuple(certain), tuple(uncertain), tuple(successor_owned)

    def mark_deadline_terminal(self, operation: OperationRef) -> None:
        """CAS one nonterminal workbench to the cumulative-deadline outcome."""

        now = self._now_ms()
        with self._factory.transaction(write=True) as connection:
            row = connection.execute(
                """
                SELECT phase, control_revision, cancellation_requested
                FROM rlm_workbench_jobs WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            phase = str(row["phase"])
            if phase == "timed_out":
                return
            if phase not in {
                "accepted",
                "preparing_workspace",
                "running",
                "waiting_external",
                "checkpointing",
                "finalizing",
            }:
                raise RlmWorkbenchConflict("deadline terminalization lost nonterminal phase")
            if bool(row["cancellation_requested"]):
                raise RlmWorkbenchConflict("deadline terminalization lost cancellation authority")
            prior_revision = int(row["control_revision"])
            next_revision = prior_revision + 1
            failure = {
                "schema_version": "aar.envelope.v1",
                "category": "deadline",
                "code": "DEADLINE_EXCEEDED",
                "message": "workbench cumulative deadline expired",
                "retryable": False,
                "certainty": "certain",
                "operation": operation.model_dump(mode="json"),
                "details": [],
            }
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET phase = 'timed_out', control_revision = ?, certainty = 'certain',
                    failure_json = ?, updated_at_unix_ms = ?
                WHERE operation_id = ? AND phase = ? AND control_revision = ?
                  AND cancellation_requested = 0
                """,
                (
                    next_revision,
                    canonical_json_bytes(failure).decode("utf-8"),
                    now,
                    operation.value,
                    phase,
                    prior_revision,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("deadline terminalization lost its job CAS")
            connection.execute(
                """
                UPDATE operation_controls SET control_revision = ?
                WHERE operation_id = ? AND control_revision = ?
                  AND cancellation_requested = 0
                """,
                (next_revision, operation.value, prior_revision),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("deadline terminalization lost its control CAS")
            connection.execute(
                """
                UPDATE rlm_workbench_suspensions
                SET state = 'parked', settled_at_unix_ms = ?
                WHERE operation_id = ? AND state = 'pending'
                """,
                (now, operation.value),
            )
            connection.execute(
                """
                UPDATE rlm_workbench_cells
                SET state = 'lost_before_commit', updated_at_unix_ms = ?
                WHERE operation_id = ? AND state = 'running'
                """,
                (now, operation.value),
            )

    def mark_model_call_budget_terminal(
        self, operation: OperationRef, attempt: OperationAttemptRefV1, fence: AttemptFence
    ) -> None:
        """CAS a running workbench to the known root-planner budget failure."""

        self.mark_planner_failure_terminal(
            operation,
            attempt,
            fence,
            FailureEnvelope(
                category=FailureCategory.BUDGET,
                code="BUDGET_EXCEEDED",
                message="workbench root planner model-call budget exhausted",
                retryable=False,
                certainty=OutcomeCertainty.CERTAIN,
                operation=operation,
            ),
        )

    def mark_planner_failure_terminal(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        failure: FailureEnvelope,
        *,
        connection: Any | None = None,
        require_cell_budget: bool = False,
        require_prompt_capacity: bool = False,
        invalid_completion: _InvalidCompletionSelection | None = None,
    ) -> None:
        """Fence a known planner failure without erasing outstanding effects."""

        if connection is None:
            with self._factory.transaction(write=True) as owned_connection:
                self.mark_planner_failure_terminal(
                    operation,
                    attempt,
                    fence,
                    failure,
                    connection=owned_connection,
                    require_cell_budget=require_cell_budget,
                    require_prompt_capacity=require_prompt_capacity,
                    invalid_completion=invalid_completion,
                )
            return

        if failure.operation != operation or failure.certainty is not OutcomeCertainty.CERTAIN:
            raise RlmWorkbenchConflict("planner failure is not bound to this certain outcome")
        failure_json = canonical_json_bytes(failure.model_dump(mode="json")).decode("utf-8")
        now = self._now_ms()
        self._assert_attempt_fence(connection, operation, attempt, fence)
        row = connection.execute(
            """
            SELECT phase, control_revision, cancellation_requested, failure_json,
                   spec_json, cumulative_deadline_unix_ms
            FROM rlm_workbench_jobs WHERE operation_id = ?
            """,
            (operation.value,),
        ).fetchone()
        if row is None:
            raise KeyError(operation.value)
        phase = str(row["phase"])
        if phase == "failed":
            if row["failure_json"] != failure_json:
                raise RlmWorkbenchConflict("planner terminalization cannot replace a failure")
            return
        if phase != "running":
            raise RlmWorkbenchConflict("planner terminalization lost running phase")
        if bool(row["cancellation_requested"]):
            raise RlmWorkbenchConflict("planner terminalization lost cancellation authority")
        unresolved_cell = connection.execute(
            "SELECT 1 FROM rlm_workbench_cells WHERE operation_id = ? "
            "AND state != 'committed' LIMIT 1",
            (operation.value,),
        ).fetchone()
        unresolved_ticket = connection.execute(
            "SELECT 1 FROM caller_work_tickets WHERE operation_id = ? "
            "AND state NOT IN ('settled_success', 'settled_failure', "
            "'cancelled_before_send', 'cancelled_certain') "
            "LIMIT 1",
            (operation.value,),
        ).fetchone()
        if unresolved_cell is not None or unresolved_ticket is not None:
            raise RlmWorkbenchConflict("planner terminalization has unresolved work")
        if invalid_completion is not None:
            if failure.code != "WORKSPACE_FAILED" or failure.category is not FailureCategory.WORKER:
                raise RlmWorkbenchConflict("invalid completion failure classification disagrees")
            try:
                self._completion_directive(operation, connection=connection)
            except _InvalidCompletionSelection as verified:
                if verified.proof != invalid_completion.proof:
                    raise RlmWorkbenchConflict("invalid completion proof changed") from verified
            else:
                raise RlmWorkbenchConflict("invalid completion proof disappeared")
        if require_prompt_capacity:
            if (
                failure.code != "BUDGET_EXCEEDED"
                or failure.category is not FailureCategory.BUDGET
                or not any(
                    item.name == "budget_kind" and item.value == "planner_prompt_characters"
                    for item in failure.details
                )
            ):
                raise RlmWorkbenchConflict("planner capacity failure classification disagrees")
            if now >= int(row["cumulative_deadline_unix_ms"]):
                raise RlmWorkbenchConflict("planner capacity rejection lost its deadline")
        if require_cell_budget:
            # A typed pre-cell rejection is not proof that its predicates
            # still hold. The caller's BEGIN IMMEDIATE spans this recheck and
            # every inner/outer/ownership write in the terminal transaction.
            spec = RlmWorkbenchJobSpec.model_validate_json(str(row["spec_json"]), strict=True)
            cell_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()[0]
            )
            if cell_count < int(spec.root["budgets"]["max_cells"]):
                raise RlmWorkbenchConflict("planner cell budget is no longer exhausted")
            if now >= int(row["cumulative_deadline_unix_ms"]):
                raise RlmWorkbenchConflict("planner cell budget rejection lost its deadline")
        prior_revision = int(row["control_revision"])
        next_revision = prior_revision + 1
        connection.execute(
            """
            UPDATE rlm_workbench_jobs
            SET phase = 'failed', control_revision = ?, certainty = 'certain',
                failure_json = ?, updated_at_unix_ms = ?
            WHERE operation_id = ? AND phase = 'running' AND control_revision = ?
              AND cancellation_requested = 0
            """,
            (
                next_revision,
                canonical_json_bytes(failure).decode("utf-8"),
                now,
                operation.value,
                prior_revision,
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise RlmWorkbenchConflict("planner terminalization lost its job CAS")
        connection.execute(
            """
            UPDATE operation_controls SET control_revision = ?
            WHERE operation_id = ? AND control_revision = ?
              AND cancellation_requested = 0
            """,
            (next_revision, operation.value, prior_revision),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise RlmWorkbenchConflict("planner terminalization lost its control CAS")

    def recover_indeterminate(self, runtime_generation: int) -> int:
        """Recover committed rebinds whose exact worker vanished with the supervisor."""

        recovered = 0
        for record in self._registry.list_recovery_candidates():
            operation = record.operation
            with self._factory.transaction(write=False) as connection:
                prepared_cell = connection.execute(
                    """
                    SELECT cell.cell_execution_id, cell.attempt_id, cell.attempt_fence,
                           cell.workspace_id, cell.workspace_generation,
                           cell.pre_workspace_revision, catalog.manifest_json,
                           authority.authority_generation,
                           authority.worker_owner_generation,
                           authority.worker_process_identity_digest,
                           authority.cell_execution_id AS authority_cell_execution_id
                    FROM rlm_workbench_cells AS cell
                    JOIN workspace_checkpoint_catalog AS catalog
                      ON catalog.manifest_digest = cell.pre_checkpoint_digest
                    JOIN rlm_workbench_attempt_authority AS authority
                      ON authority.operation_id = cell.operation_id
                    WHERE cell.operation_id = ? AND cell.state = 'prepared'
                      AND authority.attempt_id = cell.attempt_id
                      AND authority.attempt_fence = cell.attempt_fence
                    """,
                    (operation.value,),
                ).fetchone()
            if prepared_cell is not None:
                envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
                manifest = WorkspaceCheckpointManifest.model_validate_json(
                    str(prepared_cell["manifest_json"]), strict=True
                )
                restored = self._backend.restore(
                    manifest,
                    WorkspaceRestoreSpec(
                        workspace=manifest.source_handle.workspace,
                        session=envelope.session,
                        expected_handle=manifest.source_handle,
                        recover_lost_generation=True,
                    ),
                )
                verification = self._backend.checkpoint(
                    OperationRef(value=f"verify-prepared-{operation.value}"),
                    restored,
                    WorkspaceCheckpointPolicy(
                        max_values=1_024,
                        max_bytes=16_777_216,
                        max_depth=32,
                        max_collection_items=65_536,
                    ),
                    trace_id=f"trace-verify-prepared-{operation.value}",
                )
                if (
                    verification.values != manifest.values
                    or verification.exclusions != manifest.exclusions
                    or verification.artifacts != manifest.artifacts
                ):
                    raise RlmWorkbenchConflict(
                        "restored prepared cell does not match its checkpoint"
                    )
                binding = self._backend.worker_binding(restored)
                now = self._now_ms()
                with self._factory.transaction(write=True) as connection:
                    connection.execute(
                        """
                        UPDATE rlm_workbench_jobs
                        SET workspace_generation = ?, workspace_revision = ?,
                            updated_at_unix_ms = ?
                        WHERE operation_id = ?
                        """,
                        (restored.generation, restored.revision, now, operation.value),
                    )
                    connection.execute(
                        """
                        UPDATE rlm_workbench_cells
                        SET workspace_generation = ?, pre_workspace_revision = ?,
                            updated_at_unix_ms = ?
                        WHERE operation_id = ? AND cell_execution_id = ?
                          AND state = 'prepared' AND attempt_id = ? AND attempt_fence = ?
                        """,
                        (
                            restored.generation,
                            restored.revision,
                            now,
                            operation.value,
                            prepared_cell["cell_execution_id"],
                            prepared_cell["attempt_id"],
                            prepared_cell["attempt_fence"],
                        ),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise RlmWorkbenchConflict("prepared planner cell changed during restore")
                    connection.execute(
                        """
                        UPDATE rlm_workbench_attempt_authority
                        SET authority_generation = authority_generation + 1,
                            worker_owner_generation = ?,
                            worker_process_identity_digest = ?,
                            workspace_generation = ?, workspace_revision = ?,
                            updated_at_unix_ms = ?
                        WHERE operation_id = ? AND attempt_id = ? AND attempt_fence = ?
                          AND authority_generation = ? AND cell_execution_id = ?
                        """,
                        (
                            binding.owner_generation,
                            binding.process_identity_digest,
                            restored.generation,
                            restored.revision,
                            now,
                            operation.value,
                            prepared_cell["attempt_id"],
                            prepared_cell["attempt_fence"],
                            prepared_cell["authority_generation"],
                            prepared_cell["cell_execution_id"],
                        ),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise RlmWorkbenchConflict(
                            "prepared planner authority changed during restore"
                        )
                self._registry.requeue_indeterminate(
                    operation,
                    runtime_generation,
                    decision="restore_checkpoint",
                    reason_code="prepared_planner_cell_worker_lost",
                    input_digest=record.input_digest,
                )
                recovered += 1
                continue
            with self._factory.transaction(write=False) as connection:
                planner = connection.execute(
                    """
                    SELECT outbox.outbox_digest, outbox.rebind_generation,
                           outbox.successor_attempt_id, outbox.successor_attempt_fence,
                           job.workspace_id, job.workspace_generation,
                           job.workspace_revision, job.checkpoint_digest,
                           catalog.manifest_json
                    FROM rlm_workbench_successor_outbox AS outbox
                    JOIN rlm_workbench_jobs AS job
                      ON job.operation_id = outbox.operation_id
                    JOIN rlm_workbench_suspensions AS suspension
                      ON suspension.operation_id = outbox.operation_id
                     AND suspension.suspension_revision = outbox.suspension_revision
                    LEFT JOIN workspace_checkpoint_catalog AS catalog
                      ON catalog.manifest_digest = job.checkpoint_digest
                    WHERE outbox.operation_id = ? AND outbox.state = 'prepared'
                      AND suspension.cell_execution_id IS NULL
                    """,
                    (operation.value,),
                ).fetchone()
            if planner is not None:
                envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
                if planner["workspace_id"] is None:
                    raise RlmWorkbenchConflict(
                        "prepared planner recovery has no durable workspace binding"
                    )
                if planner["checkpoint_digest"] is None:
                    restored = self._backend.create(
                        WorkspaceRef(value=str(planner["workspace_id"])), envelope.session
                    )
                    if restored.generation != int(
                        planner["workspace_generation"]
                    ) or restored.revision != int(planner["workspace_revision"]):
                        raise RlmWorkbenchConflict(
                            "empty planner workspace did not recover its exact handle"
                        )
                else:
                    if planner["manifest_json"] is None:
                        raise RlmWorkbenchConflict(
                            "prepared planner checkpoint manifest is unavailable"
                        )
                    manifest = WorkspaceCheckpointManifest.model_validate_json(
                        str(planner["manifest_json"]), strict=True
                    )
                    restored = self._backend.restore(
                        manifest,
                        WorkspaceRestoreSpec(
                            workspace=manifest.source_handle.workspace,
                            session=envelope.session,
                            expected_handle=manifest.source_handle,
                            recover_lost_generation=True,
                        ),
                    )
                    verification = self._backend.checkpoint(
                        OperationRef(value=f"verify-planner-{operation.value}"),
                        restored,
                        WorkspaceCheckpointPolicy(
                            max_values=1_024,
                            max_bytes=16_777_216,
                            max_depth=32,
                            max_collection_items=65_536,
                        ),
                        trace_id=f"trace-verify-planner-{operation.value}",
                    )
                    if (
                        verification.values != manifest.values
                        or verification.exclusions != manifest.exclusions
                        or verification.artifacts != manifest.artifacts
                    ):
                        raise RlmWorkbenchConflict(
                            "restored planner workspace does not match its checkpoint"
                        )
                with self._factory.transaction(write=True) as connection:
                    connection.execute(
                        """
                        UPDATE rlm_workbench_jobs
                        SET workspace_generation = ?, workspace_revision = ?,
                            updated_at_unix_ms = ?
                        WHERE operation_id = ? AND phase = 'accepted'
                          AND EXISTS (
                            SELECT 1 FROM rlm_workbench_successor_outbox
                            WHERE operation_id = ? AND state = 'prepared'
                              AND outbox_digest = ? AND rebind_generation = ?
                              AND successor_attempt_id = ?
                              AND successor_attempt_fence = ?
                          )
                        """,
                        (
                            restored.generation,
                            restored.revision,
                            self._now_ms(),
                            operation.value,
                            operation.value,
                            planner["outbox_digest"],
                            planner["rebind_generation"],
                            planner["successor_attempt_id"],
                            planner["successor_attempt_fence"],
                        ),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise RlmWorkbenchConflict(
                            "prepared planner changed during workspace recovery"
                        )
                self._registry.requeue_indeterminate(
                    operation,
                    runtime_generation,
                    decision="start_successor",
                    reason_code="planner_successor_worker_lost",
                    input_digest=record.input_digest,
                )
                recovered += 1
                continue
            with self._factory.transaction(write=False) as connection:
                row = connection.execute(
                    """
                    SELECT transfer.token_digest, transfer.state AS transfer_state,
                           transfer.consumption_kind, cell.state AS cell_state,
                           cell.pre_checkpoint_digest, catalog.manifest_json
                    FROM rlm_workbench_rebind_transfers AS transfer
                    JOIN rlm_workbench_cells AS cell
                      ON cell.operation_id = transfer.operation_id
                     AND cell.cell_execution_id = transfer.cell_execution_id
                    JOIN workspace_checkpoint_catalog AS catalog
                      ON catalog.manifest_digest = cell.pre_checkpoint_digest
                    WHERE transfer.operation_id = ?
                      AND (
                        (transfer.state IN ('prepared', 'committed')
                         AND cell.state IN ('running', 'suspended'))
                        OR
                        (transfer.state = 'consumed'
                         AND transfer.consumption_kind = 'recovery_fenced_loss'
                         AND cell.state = 'lost_before_commit')
                        OR
                        (transfer.state = 'aborted'
                         AND cell.state = 'lost_before_commit')
                      )
                    ORDER BY transfer.rebind_generation DESC
                    LIMIT 1
                    """,
                    (operation.value,),
                ).fetchone()
            if row is None:
                continue
            envelope = RequestEnvelope.model_validate_json(record.request_json, strict=True)
            manifest = WorkspaceCheckpointManifest.model_validate_json(
                str(row["manifest_json"]), strict=True
            )
            restored = self._backend.restore(
                manifest,
                WorkspaceRestoreSpec(
                    workspace=manifest.source_handle.workspace,
                    session=envelope.session,
                    expected_handle=manifest.source_handle,
                    recover_lost_generation=True,
                ),
            )
            verification = self._backend.checkpoint(
                OperationRef(value=f"verify-workbench-{operation.value}"),
                restored,
                WorkspaceCheckpointPolicy(
                    max_values=1_024,
                    max_bytes=16_777_216,
                    max_depth=32,
                    max_collection_items=65_536,
                ),
                trace_id=f"trace-verify-workbench-{operation.value}",
            )
            if (
                verification.values != manifest.values
                or verification.exclusions != manifest.exclusions
                or verification.artifacts != manifest.artifacts
            ):
                raise RlmWorkbenchConflict("restored workbench does not match pre-cell checkpoint")
            self._registry.mark_workbench_rebind_worker_lost(
                operation,
                runtime_generation,
                token_digest=str(row["token_digest"]),
                restored_handle=restored,
            )
            self._registry.requeue_indeterminate(
                operation,
                runtime_generation,
                decision="restore_checkpoint",
                reason_code="workbench_rebind_worker_lost",
                input_digest=record.input_digest,
            )
            recovered += 1
        return recovered

    @staticmethod
    def _supervisor_frame(
        *,
        kind: str,
        operation: OperationRef,
        token: Mapping[str, Any],
        payload: Mapping[str, Any],
        frame_sequence: int,
        broker_call_ordinal: int | None = None,
    ) -> WorkspaceBrokerFrame:
        body = dict(payload)
        return WorkspaceBrokerFrame.model_validate(
            {
                "schema_version": "aar.workspace-broker-frame.v1",
                "kind": kind,
                "direction": "supervisor_to_worker",
                "committed": True,
                "operation": operation.model_dump(mode="json"),
                "attempt_id": token["successor_attempt_id"],
                "attempt_fence": token["successor_attempt_fence"],
                "workspace": token["workspace"],
                "workspace_generation": token["workspace_generation"],
                "workspace_revision": token["workspace_revision"],
                "cell_execution_id": token["cell_execution_id"],
                "frame_sequence": frame_sequence,
                "broker_call_ordinal": broker_call_ordinal,
                "deadline_unix_ms": token["expected_cumulative_deadline_unix_ms"],
                "payload": body,
                "payload_digest": canonical_sha256(body),
                "payload_bytes": len(canonical_json_bytes(body)),
            },
            strict=True,
        )

    def _settled_broker_receipt_payload(
        self,
        operation: OperationRef,
        token: Mapping[str, Any],
    ) -> tuple[int, int, dict[str, Any]]:
        ticket_id = str(token["ticket_id"])
        ticket = self._caller_work.get(ticket_id).model_dump(mode="json")
        if (
            ticket["operation"] != operation.model_dump(mode="json")
            or ticket["state"] not in {"settled_success", "settled_failure", "cancelled_certain"}
            or ticket["settled_receipt_digest"] != token["settled_receipt_digest"]
        ):
            raise RlmWorkbenchConflict("successor caller receipt binding is stale")
        candidates = [
            item.model_dump(mode="json")
            for item in self._caller_work.candidate_receipts(ticket_id)
            if item.receipt_digest == ticket["settled_receipt_digest"]
        ]
        if len(candidates) != 1:
            raise RlmWorkbenchConflict("successor committed candidate receipt is ambiguous")
        candidate = candidates[0]
        with self._factory.transaction(write=False) as connection:
            suspension = connection.execute(
                """
                SELECT logical_owner_json, request_digest FROM rlm_workbench_suspensions
                WHERE operation_id = ? AND suspension_revision = ? AND ticket_id = ?
                """,
                (
                    operation.value,
                    token["suspension_revision"],
                    token["ticket_id"],
                ),
            ).fetchone()
            if suspension is None:
                raise RlmWorkbenchConflict("successor suspension does not exist")
            owner = json.loads(str(suspension["logical_owner_json"]))
            ordinal = int(owner["broker_call_ordinal"])
            broker_calls = connection.execute(
                """
                SELECT sequence, request_json, request_digest FROM broker_calls
                WHERE operation_id = ?
                  AND json_extract(request_json, '$.cell_execution_id') = ?
                  AND json_extract(request_json, '$.broker_call_ordinal') = ?
                """,
                (operation.value, token["cell_execution_id"], ordinal),
            ).fetchall()
            if len(broker_calls) != 1:
                raise RlmWorkbenchConflict("successor broker call is missing or ambiguous")
            broker_call = broker_calls[0]
        request_frame = WorkspaceBrokerFrame.model_validate_json(
            str(broker_call["request_json"]), strict=True
        )
        frame_document = request_frame.model_dump(mode="json")
        if (
            frame_document["operation"] != operation.model_dump(mode="json")
            or frame_document["frame_sequence"] != broker_call["sequence"]
            or canonical_sha256(frame_document) != broker_call["request_digest"]
        ):
            raise RlmWorkbenchConflict("successor broker frame binding is stale")
        request_payload = dict(request_frame.root["payload"])
        context = dict(request_payload["context"])
        if (
            request_payload["request_digest"] != suspension["request_digest"]
            or request_payload["request_digest"] != ticket["request_digest"]
            or context["cell_execution_id"] != token["cell_execution_id"]
            or context["broker_call_ordinal"] != ordinal
        ):
            raise RlmWorkbenchConflict("successor broker request binding is stale")
        context["attempt_id"] = token["successor_attempt_id"]
        context["attempt_fence"] = token["successor_attempt_fence"]
        context["deadline_unix_ms"] = token["expected_cumulative_deadline_unix_ms"]
        return (
            ordinal,
            int(broker_call["sequence"]),
            {
                "context": context,
                "contract_id": request_payload["contract_id"],
                "observation": candidate["observation"],
                "receipt_digest": ticket["settled_receipt_digest"],
                "request_digest": str(suspension["request_digest"]),
            },
        )

    def admit(
        self,
        operation: OperationRef,
        value: Mapping[str, Any] | RlmWorkbenchExecuteInput,
    ) -> RlmWorkbenchSnapshot:
        request = RlmWorkbenchExecuteInput.model_validate(
            value.root if isinstance(value, RlmWorkbenchExecuteInput) else dict(value),
            strict=True,
        )
        document = request.root
        spec = RlmWorkbenchJobSpec.model_validate(document["spec"], strict=True)
        context = document["context"]
        if spec.root["workspace"]["seed_checkpoint"] is not None:
            raise RlmWorkbenchUnsupported(
                "seed checkpoint restore is not available in the admission slice"
            )

        spec_json = canonical_json_bytes(spec.root).decode("utf-8")
        spec_digest = canonical_sha256(spec.root)
        context_digest = canonical_sha256(context)
        route_digest = canonical_sha256(spec.root["model"]["route_binding"])
        now = self._now_ms()
        operation_record = self._registry.get(operation)
        cumulative_deadline = derive_cumulative_deadline_unix_ms(
            intent_persisted_at_unix_ms=operation_record.created_at_unix_ms,
            context_deadline_unix_ms=int(context["deadline_unix_ms"]),
            total_wall_time_ms=int(spec.root["budgets"]["total_wall_time_ms"]),
        )
        with self._factory.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO operation_controls(
                        operation_id, control_revision, cancellation_requested,
                        requested_at_unix_ms, requested_by_digest, reason_code
                    ) VALUES (?, 1, 0, NULL, NULL, NULL)
                    ON CONFLICT(operation_id) DO NOTHING
                    """,
                    (operation.value,),
                )
                control = connection.execute(
                    "SELECT control_revision, cancellation_requested "
                    "FROM operation_controls WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()
                if control is None or int(control["cancellation_requested"]) != 0:
                    raise RlmWorkbenchConflict("operation control is absent or already cancelled")
                connection.execute(
                    """
                    INSERT INTO rlm_workbench_jobs(
                        operation_id, phase, control_revision,
                        cancellation_revision, cancellation_requested,
                        spec_json, spec_digest, context_digest,
                        route_binding_digest, cumulative_deadline_unix_ms,
                        workspace_id, workspace_generation, workspace_revision,
                        checkpoint_digest, result_json, result_digest,
                        failure_json, certainty, created_at_unix_ms,
                        updated_at_unix_ms
                    ) VALUES (
                        ?, 'accepted', ?, 0, 0, ?, ?, ?, ?, ?,
                        NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                        'certain', ?, ?
                    )
                    """,
                    (
                        operation.value,
                        int(control["control_revision"]),
                        spec_json,
                        spec_digest,
                        context_digest,
                        route_digest,
                        cumulative_deadline,
                        now,
                        now,
                    ),
                )
            else:
                self._assert_admission_binding(
                    row,
                    spec_json=spec_json,
                    spec_digest=spec_digest,
                    context_digest=context_digest,
                    route_digest=route_digest,
                )

        self._prepare_workspace(
            operation,
            SessionRef(value=str(context["session_id"])),
        )
        return self.snapshot(operation)

    def snapshot(self, operation: OperationRef) -> RlmWorkbenchSnapshot:
        with self._factory.transaction(write=False) as connection:
            return self._snapshot_with_connection(operation, connection)

    def _snapshot_with_connection(
        self, operation: OperationRef, connection: Any
    ) -> RlmWorkbenchSnapshot:
        row = connection.execute(
            "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        if row is None:
            raise KeyError(operation.value)
        pending_rows = connection.execute(
            """
            SELECT ticket.ticket_id, ticket.revision,
                   ticket.ticket_digest, ticket.state
            FROM rlm_workbench_suspensions AS suspension
            JOIN caller_work_tickets AS ticket
              ON ticket.ticket_id = suspension.ticket_id
            WHERE suspension.operation_id = ?
              AND suspension.state = 'pending'
            ORDER BY suspension.suspension_revision
            """,
            (operation.value,),
        ).fetchall()
        phase = str(row["phase"])
        workspace = None
        if row["workspace_id"] is not None:
            workspace = {
                "workspace": {"type": "workspace", "value": str(row["workspace_id"])},
                "generation": int(row["workspace_generation"]),
                "revision": int(row["workspace_revision"]),
                "checkpoint_digest": row["checkpoint_digest"],
            }
        result = None if row["result_json"] is None else json.loads(row["result_json"])
        failure = None if row["failure_json"] is None else json.loads(row["failure_json"])
        return RlmWorkbenchSnapshot.model_validate(
            {
                "schema_version": "aar.rlm-workbench-snapshot.v1",
                "operation": operation.model_dump(mode="json"),
                "revision": int(row["control_revision"]),
                "phase": phase,
                "legacy_projection": {
                    "phase": phase,
                    "legacy_operation_state": _LEGACY_PHASE[phase],
                },
                "workspace": workspace,
                "pending_tickets": [
                    {
                        "ticket_id": str(item["ticket_id"]),
                        "revision": int(item["revision"]),
                        "ticket_digest": str(item["ticket_digest"]),
                        "state": str(item["state"]),
                    }
                    for item in pending_rows
                ],
                "result": result,
                "failure": failure,
                "certainty": str(row["certainty"]),
                "updated_at_unix_ms": int(row["updated_at_unix_ms"]),
            },
            strict=True,
        )

    def resolve_public_artifact(self, reference: ArtifactReference) -> bytes | None:
        return self._artifact_publications.resolve_public_reference(reference)

    def workspace_handle(self, operation: OperationRef) -> ProgrammableWorkspaceHandle:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                """
                SELECT workspace_id, workspace_generation, workspace_revision
                FROM rlm_workbench_jobs WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
        if row is None:
            raise KeyError(operation.value)
        if row["workspace_id"] is None:
            raise RlmWorkbenchConflict("workbench workspace is not prepared")
        return ProgrammableWorkspaceHandle(
            workspace=WorkspaceRef(value=str(row["workspace_id"])),
            backend=self._backend.descriptor,
            generation=int(row["workspace_generation"]),
            revision=int(row["workspace_revision"]),
        )

    def _job_row(self, operation: OperationRef) -> Any:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        if row is None:
            raise KeyError(operation.value)
        return row

    def _suspend_root_planner(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        spec: RlmWorkbenchJobSpec,
    ) -> RlmWorkbenchSnapshot:
        self._require_deadline(operation)
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                """
                SELECT cumulative_deadline_unix_ms
                FROM rlm_workbench_jobs WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            prior = connection.execute(
                """
                SELECT COALESCE(MAX(suspension_revision), 0) AS value,
                       COALESCE(SUM(CASE WHEN cell_execution_id IS NULL THEN 1 ELSE 0 END), 0)
                         AS planner_steps
                FROM rlm_workbench_suspensions WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            last_cell = connection.execute(
                """
                SELECT cell_index, cell_execution_id, source_json, source_digest,
                       result_json, result_digest,
                       post_checkpoint_digest
                FROM rlm_workbench_cells
                WHERE operation_id = ? AND state = 'committed'
                ORDER BY cell_index DESC LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
            cell_count = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()[0]
            )
            artifacts = []
            if last_cell is not None:
                artifacts = connection.execute(
                    "SELECT s.stage_id, s.logical_name FROM rlm_workbench_artifact_stages AS s "
                    "JOIN rlm_workbench_cells AS c ON c.operation_id = s.operation_id "
                    "AND c.cell_execution_id = s.cell_execution_id "
                    "WHERE s.operation_id = ? AND s.state = 'committed' AND c.state = 'committed' "
                    "AND json_extract(c.result_json, '$.status') = 'succeeded' "
                    "ORDER BY s.stage_id",
                    (operation.value,),
                ).fetchall()
        if row is None or prior is None:
            raise KeyError(operation.value)
        if int(prior["planner_steps"]) >= int(spec.root["budgets"]["max_model_calls"]):
            raise RlmWorkbenchModelCallBudgetExceeded(
                "workbench root planner model-call budget exhausted"
            )
        suspension_revision = int(prior["value"]) + 1
        owner = PlannerOwner(
            phase="initial" if int(prior["planner_steps"]) == 0 else "finalizer",
            step_index=int(prior["planner_steps"]),
        ).as_wire()
        last_committed_cell = None
        if last_cell is not None:
            with self._factory.transaction(write=False) as connection:
                stage_rows = connection.execute(
                    """
                    SELECT stage_id FROM rlm_workbench_artifact_stages
                    WHERE operation_id = ? AND cell_execution_id = ? AND state = 'committed'
                    ORDER BY stage_id
                    """,
                    (operation.value, last_cell["cell_execution_id"]),
                ).fetchall()
            last_committed_cell = {
                "cell_index": int(last_cell["cell_index"]),
                "result": json.loads(str(last_cell["result_json"])),
                "result_digest": str(last_cell["result_digest"]),
                "post_checkpoint_digest": str(last_cell["post_checkpoint_digest"]),
                "artifact_stage_ids": [str(item["stage_id"]) for item in stage_rows],
            }
            last_committed_cell["source"] = json.loads(str(last_cell["source_json"]))
            last_committed_cell["source_digest"] = str(last_cell["source_digest"])
        request = _planner_request(
            spec,
            owner=owner,
            snapshot=self.snapshot(operation),
            last_committed_cell=last_committed_cell,
            committed_artifacts=[
                {"stage_id": str(item["stage_id"]), "logical_name": str(item["logical_name"])}
                for item in artifacts
            ],
            remaining_cells=max(0, int(spec.root["budgets"]["max_cells"]) - cell_count),
        )
        request_digest = canonical_sha256(request)
        ticket_material = {
            "operation": operation.model_dump(mode="json"),
            "owner": owner,
            "request_digest": request_digest,
        }
        ticket_id = (
            "planner-" + hashlib.sha256(canonical_json_bytes(ticket_material)).hexdigest()[:48]
        )
        ticket_payload = {
            "schema_version": "aar.caller-work-ticket.v1",
            "ticket_id": ticket_id,
            "operation": operation.model_dump(mode="json"),
            "suspension_revision": suspension_revision,
            "revision": 0,
            "owner": owner,
            "request": request,
            "request_digest": request_digest,
            "state": "pending",
            "deadline_unix_ms": int(row["cumulative_deadline_unix_ms"]),
            "claimant": None,
            "physical_attempt": None,
            "settled_receipt_digest": None,
            "settled_at_unix_ms": None,
        }
        ticket = CallerWorkTicket.model_validate(
            {**ticket_payload, "ticket_digest": canonical_sha256(ticket_payload)},
            strict=True,
        )
        self._registry.suspend_planner_attempt(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            ticket=ticket,
            ticket_writer=self._caller_work,
        )
        return self.snapshot(operation)

    def _consume_root_planner(
        self,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> RlmDirective | RlmWorkbenchSnapshot:
        token = self._registry.prepared_planner_successor(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
        )
        if token is None:
            raise RlmWorkbenchConflict("prepared planner successor is absent")
        if token.get("recovery_source") == "prepared_cell":
            return RlmDirective.model_validate(token["directive"], strict=True)
        if token["ticket_state"] in {"settled_failure", "cancelled_certain"}:
            terminal_state = (
                OperationState.CANCELLED
                if token["ticket_state"] == "cancelled_certain"
                else OperationState.FAILED
            )
            failure = {
                "schema_version": "aar.envelope.v1",
                "category": (
                    "cancelled" if terminal_state is OperationState.CANCELLED else "internal"
                ),
                "code": (
                    "CONFLICT" if terminal_state is OperationState.CANCELLED else "INTERNAL_ERROR"
                ),
                "message": f"root planner settled as {token['ticket_state']}",
                "retryable": False,
                "certainty": "certain",
                "operation": attempt.operation.model_dump(mode="json"),
                "details": [],
            }
            self._registry.consume_planner_successor(
                attempt,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                token=token,
                terminal_state=terminal_state,
                failure=failure,
            )
            return self.snapshot(attempt.operation)
        if token["ticket_state"] != "settled_success":
            raise RlmWorkbenchConflict(
                f"planner settlement is not outbox-eligible: {token['ticket_state']!r}"
            )
        receipts = tuple(
            receipt
            for receipt in self._caller_work.candidate_receipts(token["ticket_id"])
            if receipt.receipt_digest == token["settlement_digest"]
        )
        if len(receipts) != 1:
            raise RlmWorkbenchConflict("planner settlement receipt is not unique")
        observation = dict(receipts[0].root["observation"])
        if (
            observation.get("kind") != "model"
            or observation.get("outcome") != "succeeded"
            or observation.get("route_receipt_digest") is None
            or observation.get("usage_receipt_digest") is None
            or observation.get("host_receipt_digest") is None
        ):
            raise RlmWorkbenchConflict("planner settlement lacks exact successful model evidence")
        output_text = str(observation["output_text"])
        output_digest = "sha256:" + hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        if output_digest != str(observation["output_digest"]):
            raise RlmWorkbenchConflict("planner response digest is stale")
        try:
            directive_document = load_provider_ready_json_bytes(output_text.encode("utf-8"))
            directive = RlmDirective.model_validate(directive_document, strict=True)
        except (ProviderReadyContractError, ValidationError) as error:
            return self._correct_or_fail_root_planner(
                attempt,
                fence,
                token=token,
                observation=observation,
                error=error,
            )
        cell_projection: dict[str, Any] | None = None
        if directive.root["kind"] == "execute_cell":
            operation = attempt.operation
            spec = RlmWorkbenchJobSpec.model_validate_json(
                str(self._job_row(operation)["spec_json"]), strict=True
            )
            with self._factory.transaction(write=False) as connection:
                cell_index = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                        (operation.value,),
                    ).fetchone()[0]
                )
            if cell_index >= int(spec.root["budgets"]["max_cells"]):
                # Accepted caller successors have not entered the running
                # phase. Consume the exact settled directive as a rejected
                # cell in the existing registry transaction, not by loosening
                # the running-only planner-failure fence.
                self._registry.consume_planner_successor(
                    attempt,
                    fence.dispatcher_generation,
                    fence.lease_epoch,
                    fence.owner_digest,
                    token=token,
                    directive=directive.root,
                    reject_cell_budget=True,
                )
                return self.snapshot(operation)
            handle = self.workspace_handle(operation)
            source_json = canonical_json_bytes(directive.root).decode("utf-8")
            source_digest = canonical_sha256(directive.root)
            cell_execution_id = (
                "cell-"
                + hashlib.sha256(
                    f"{operation.value}\0{cell_index}\0{source_digest}".encode()
                ).hexdigest()[:32]
            )
            pre_checkpoint = self._backend.checkpoint(
                OperationRef(value=f"ck-pre-{cell_execution_id}"),
                handle,
                WorkspaceCheckpointPolicy(),
                trace_id=f"trace-pre-{cell_execution_id}",
            )
            worker_binding = self._backend.worker_binding(handle)
            checkpoint_json = canonical_json_bytes(pre_checkpoint.model_dump(mode="json")).decode(
                "utf-8"
            )
            cell_projection = {
                "cell_index": cell_index,
                "cell_execution_id": cell_execution_id,
                "source_json": source_json,
                "source_digest": source_digest,
                "checkpoint_json": checkpoint_json,
                "pre_checkpoint_digest": pre_checkpoint.content_digest,
                "workspace_id": handle.workspace.value,
                "workspace_generation": handle.generation,
                "workspace_revision": handle.revision,
                "backend_capability_digest": handle.backend.capability_digest,
                "environment_digest": pre_checkpoint.environment.digest,
                "worker_owner_generation": worker_binding.owner_generation,
                "worker_process_identity_digest": worker_binding.process_identity_digest,
            }
        consumed_record = self._registry.consume_planner_successor(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            token=token,
            directive=directive.root,
            cell_projection=cell_projection,
        )
        if consumed_record.state in {
            OperationState.FAILED,
            OperationState.CANCELLED,
            OperationState.TIMED_OUT,
        }:
            return self.snapshot(attempt.operation)
        return directive

    def _correct_or_fail_root_planner(
        self,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        *,
        token: Mapping[str, Any],
        observation: Mapping[str, Any],
        error: ProviderReadyContractError | ValidationError,
    ) -> RlmWorkbenchSnapshot:
        operation = attempt.operation
        spec = RlmWorkbenchJobSpec.model_validate_json(
            str(self._job_row(operation)["spec_json"]), strict=True
        )
        with self._factory.transaction(write=False) as connection:
            planner_owners = connection.execute(
                """
                SELECT logical_owner_json FROM rlm_workbench_suspensions
                WHERE operation_id = ? AND cell_execution_id IS NULL
                ORDER BY suspension_revision
                """,
                (operation.value,),
            ).fetchall()
        planner_call_count = len(planner_owners)
        correction_count = sum(
            json.loads(str(row["logical_owner_json"])).get("phase") == "correction"
            for row in planner_owners
        )
        max_model_calls = int(spec.root["budgets"]["max_model_calls"])
        max_corrections = int(spec.root["model"]["max_corrections"])
        error_details: list[dict[str, Any]]
        if isinstance(error, ValidationError):
            error_details = [
                {
                    "location": [str(part) for part in item["loc"]],
                    "type": str(item["type"]),
                    "message": str(item["msg"])[:256],
                }
                for item in error.errors(include_input=False, include_url=False)[:8]
            ]
        else:
            error_details = [
                {
                    "location": [],
                    "type": type(error).__name__,
                    "message": str(error)[:256],
                }
            ]
        if planner_call_count >= max_model_calls or correction_count >= max_corrections:
            failure = {
                "schema_version": "aar.envelope.v1",
                "category": "validation",
                "code": "INVALID_ARGUMENT",
                "message": "root planner exhausted its strict directive correction budget",
                "retryable": False,
                "certainty": "certain",
                "operation": operation.model_dump(mode="json"),
                "details": [
                    {
                        "name": "planner_validation",
                        "value": canonical_json_bytes(error_details).decode("utf-8"),
                    }
                ],
            }
            self._registry.consume_planner_successor(
                attempt,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                token=token,
                terminal_state=OperationState.FAILED,
                failure=failure,
            )
            return self.snapshot(operation)

        prior_ticket = self._caller_work.get(str(token["ticket_id"]))
        prior_document = dict(prior_ticket.root)
        prior_request = dict(prior_document["request"])
        prompt_document = json.loads(str(prior_request["prompt"]))
        owner = PlannerOwner(
            phase="correction",
            step_index=int(token["logical_owner"]["step_index"]) + 1,
        ).as_wire()
        prompt_document["planner_owner"] = owner
        prompt_document["execution_budget"] = {
            **prompt_document["execution_budget"],
            "remaining_model_calls": max_model_calls - planner_call_count,
        }
        prompt_document["correction"] = {
            "invalid_output_digest": observation["output_digest"],
            "errors": error_details,
        }
        request = {
            **prior_request,
            "prompt": _planner_prompt(prompt_document),
        }
        request_digest = canonical_sha256(request)
        ticket_material = {
            "operation": operation.model_dump(mode="json"),
            "owner": owner,
            "request_digest": request_digest,
        }
        ticket_id = (
            "planner-" + hashlib.sha256(canonical_json_bytes(ticket_material)).hexdigest()[:48]
        )
        ticket_payload = {
            "schema_version": "aar.caller-work-ticket.v1",
            "ticket_id": ticket_id,
            "operation": operation.model_dump(mode="json"),
            "suspension_revision": int(token["suspension_revision"]) + 1,
            "revision": 0,
            "owner": owner,
            "request": request,
            "request_digest": request_digest,
            "state": "pending",
            "deadline_unix_ms": int(prior_document["deadline_unix_ms"]),
            "claimant": None,
            "physical_attempt": None,
            "settled_receipt_digest": None,
            "settled_at_unix_ms": None,
        }
        ticket = CallerWorkTicket.model_validate(
            {**ticket_payload, "ticket_digest": canonical_sha256(ticket_payload)},
            strict=True,
        )
        self._registry.consume_planner_successor(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            token=token,
            correction_ticket=ticket,
            ticket_writer=self._caller_work,
        )
        return self.snapshot(operation)

    def _planner_trace(
        self,
        operation: OperationRef,
    ) -> tuple[int, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...], int, int]:
        """Use the same exact journal projection enforced by native publication."""
        with self._factory.transaction(write=False) as connection:
            try:
                return read_caller_planner_trace(connection, operation.value)
            except ArtifactPublicationConflict as exc:
                raise RlmWorkbenchConflict(str(exc)) from exc

    def run_claimed(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> RlmWorkbenchResult | RlmWorkbenchSnapshot:
        if attempt.operation != operation:
            raise RlmWorkbenchConflict("claimed attempt does not belong to operation")
        stop_heartbeat = threading.Event()
        lost_fence = threading.Event()

        def renew_attempt_lease() -> None:
            while not stop_heartbeat.wait(_WORKBENCH_ATTEMPT_LEASE_HEARTBEAT_INTERVAL_MS / 1_000):
                try:
                    self._registry.heartbeat(
                        attempt,
                        self._runtime_generation,
                        fence.dispatcher_generation,
                        fence.lease_epoch,
                        fence.owner_digest,
                        _WORKBENCH_ATTEMPT_LEASE_DURATION_MS,
                    )
                except StaleAttemptFence:
                    lost_fence.set()
                    return
                except Exception:
                    # A transient registry fault is retried on the next beat;
                    # subsequent work still has to pass the normal fence checks.
                    continue

        heartbeat_thread = threading.Thread(
            target=renew_attempt_lease,
            name=f"aar-workbench-heartbeat-{operation.value}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            result = self._run_claimed_inner(operation, attempt, fence)
            if lost_fence.is_set() and not (
                isinstance(result, RlmWorkbenchSnapshot)
                and result.phase in {"waiting_external", "failed", "cancelled", "timed_out"}
            ):
                raise RlmWorkbenchConflict("workbench attempt lease fence was lost")
            return result
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()

    def _run_claimed_inner(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> RlmWorkbenchResult | RlmWorkbenchSnapshot:
        if attempt.operation != operation:
            raise RlmWorkbenchConflict("claimed attempt does not belong to operation")
        persisted_spec = RlmWorkbenchJobSpec.model_validate_json(
            str(self._job_row(operation)["spec_json"]), strict=True
        )
        caller_delegated = (
            normalize_planner_mode(str(persisted_spec.root["model"]["execution_mode"]))
            == "caller_delegated_ticketed"
        )
        if caller_delegated and attempt.attempt_no == 1:
            spec = self._begin_running(operation, attempt, fence)
            return self._suspend_root_planner(operation, attempt, fence, spec)
        if caller_delegated:
            worker_token = self._registry.prepared_workbench_rebind(
                attempt, fence.dispatcher_generation, fence.lease_epoch, fence.owner_digest
            )
            if worker_token is not None:
                with self._active_lock:
                    broker_session = WorkspaceBrokerSession(
                        self.database_path,
                        operation_id=operation.value,
                        attempt_id=attempt.attempt_id,
                        dispatch=self._dispatch_broker_payload,
                    )
                    self._broker_sessions[attempt.attempt_id] = broker_session
                try:
                    self._resume_successor_cell(
                        operation, persisted_spec, attempt, fence, worker_token, broker_session
                    )
                except WorkspaceBrokerSuspended:
                    return self.snapshot(operation)
                self._require_deadline(operation)
                completion = self._completion_directive(operation)
                if completion is not None:
                    calls, digests, usage, trace, inputs, outputs = self._planner_trace(operation)
                    return self._finalize(
                        operation,
                        attempt,
                        fence,
                        persisted_spec,
                        completion,
                        model_calls=calls,
                        directive_digests=digests,
                        usage_receipt_digests=usage,
                        broker_trace=trace,
                        input_tokens=inputs,
                        output_tokens=outputs,
                    )
                return self._suspend_root_planner(operation, attempt, fence, persisted_spec)
        if caller_delegated:
            directive = self._consume_root_planner(attempt, fence)
            if isinstance(directive, RlmWorkbenchSnapshot):
                return directive
            kind = str(directive.root["kind"])
            if kind == "finalize":
                (
                    planner_model_calls,
                    planner_directive_digests,
                    planner_usage_receipt_digests,
                    planner_broker_trace,
                    planner_input_tokens,
                    planner_output_tokens,
                ) = self._planner_trace(operation)
                return self._finalize(
                    operation,
                    attempt,
                    fence,
                    persisted_spec,
                    directive,
                    model_calls=planner_model_calls,
                    directive_digests=planner_directive_digests,
                    usage_receipt_digests=planner_usage_receipt_digests,
                    broker_trace=planner_broker_trace,
                    input_tokens=planner_input_tokens,
                    output_tokens=planner_output_tokens,
                )
            if kind != "execute_cell":
                raise RlmWorkbenchPlannerAbstained(str(directive.root["reason"]))
            with self._active_lock:
                broker_session = WorkspaceBrokerSession(
                    self.database_path,
                    operation_id=operation.value,
                    attempt_id=attempt.attempt_id,
                    dispatch=self._dispatch_broker_payload,
                )
                self._broker_sessions[attempt.attempt_id] = broker_session
            suspended = self._execute_cell(
                operation,
                attempt,
                fence,
                persisted_spec,
                directive,
                broker_session,
            )
            if suspended:
                return self.snapshot(operation)
            completion = self._completion_directive(operation)
            if completion is not None:
                calls, digests, usage, trace, inputs, outputs = self._planner_trace(operation)
                return self._finalize(
                    operation,
                    attempt,
                    fence,
                    persisted_spec,
                    completion,
                    model_calls=calls,
                    directive_digests=digests,
                    usage_receipt_digests=usage,
                    broker_trace=trace,
                    input_tokens=inputs,
                    output_tokens=outputs,
                )
            return self._suspend_root_planner(operation, attempt, fence, persisted_spec)
        if self._planner is None:
            raise RlmWorkbenchUnsupported("workbench planner is not configured")
        with self._active_lock:
            broker_session = self._broker_sessions.get(attempt.attempt_id)
            if broker_session is None:
                broker_session = WorkspaceBrokerSession(
                    self.database_path,
                    operation_id=operation.value,
                    attempt_id=attempt.attempt_id,
                    dispatch=self._dispatch_broker_payload,
                )
                self._broker_sessions[attempt.attempt_id] = broker_session
        successor_token = self._registry.prepared_workbench_rebind(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
        )
        if successor_token is None:
            spec = self._begin_running(operation, attempt, fence)
        else:
            spec = RlmWorkbenchJobSpec.model_validate_json(
                str(self._job_row(operation)["spec_json"]), strict=True
            )
            try:
                self._resume_successor_cell(
                    operation,
                    spec,
                    attempt,
                    fence,
                    successor_token,
                    broker_session,
                )
            except WorkspaceBrokerSuspended:
                return self.snapshot(operation)
        self._require_deadline(operation)
        directive_digests: list[str] = []
        recovery = self._prepare_recovery_continuation(operation, attempt, fence, spec)
        with self._factory.transaction(write=False) as connection:
            model_calls = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()[0]
            )
        if recovery is not None:
            model_calls = max(model_calls, recovery.consumed_model_calls)
        max_model_calls = int(spec.root["budgets"]["max_model_calls"])
        if recovery is not None:
            if model_calls >= max_model_calls:
                raise RlmWorkbenchModelCallBudgetExceeded(
                    "workbench root planner model-call budget exhausted"
                )
            recover = getattr(self._planner, "recover", None)
            if not callable(recover):
                raise RlmWorkbenchUnsupported(
                    "workbench recovery planner is required after committed worker loss"
                )
            raw_recovery = recover(operation, spec, recovery.planner_input)
            self._require_deadline(operation)
            directive = RlmDirective.model_validate(
                raw_recovery.root if isinstance(raw_recovery, RlmDirective) else dict(raw_recovery),
                strict=True,
            )
            model_calls += 1
            if str(directive.root["kind"]) != "execute_cell":
                raise RlmWorkbenchUnsupported(
                    "workbench recovery planner must produce a continuation cell"
                )
            recovery_digest = canonical_sha256(directive.root)
            if recovery_digest == recovery.failed_source_digest:
                raise RlmWorkbenchConflict("recovery planner attempted to replay the failed cell")
            directive_digests.append(recovery_digest)
            suspended = self._execute_cell(
                operation,
                attempt,
                fence,
                spec,
                directive,
                broker_session,
            )
            if suspended:
                return self.snapshot(operation)
            completion = self._completion_directive(operation)
            if completion is not None:
                return self._finalize(
                    operation,
                    attempt,
                    fence,
                    spec,
                    completion,
                    model_calls=model_calls,
                    directive_digests=tuple(directive_digests),
                )
        if successor_token is not None:
            completion = self._completion_directive(operation)
            if completion is not None:
                return self._finalize(
                    operation,
                    attempt,
                    fence,
                    spec,
                    completion,
                    model_calls=model_calls,
                    directive_digests=tuple(directive_digests),
                )
        while True:
            self._require_deadline(operation)
            if model_calls >= max_model_calls:
                raise RlmWorkbenchModelCallBudgetExceeded(
                    "workbench root planner model-call budget exhausted"
                )
            raw_directive = self._planner.plan(operation, spec, self.snapshot(operation))
            self._require_deadline(operation)
            directive = RlmDirective.model_validate(
                raw_directive.root
                if isinstance(raw_directive, RlmDirective)
                else dict(raw_directive),
                strict=True,
            )
            model_calls += 1
            directive_digests.append(canonical_sha256(directive.root))
            kind = str(directive.root["kind"])
            if kind == "execute_cell":
                suspended = self._execute_cell(
                    operation,
                    attempt,
                    fence,
                    spec,
                    directive,
                    broker_session,
                )
                if suspended:
                    return self.snapshot(operation)
                completion = self._completion_directive(operation)
                if completion is not None:
                    return self._finalize(
                        operation,
                        attempt,
                        fence,
                        spec,
                        completion,
                        model_calls=model_calls,
                        directive_digests=tuple(directive_digests),
                    )
                continue
            if kind == "finalize":
                self._require_deadline(operation)
                return self._finalize(
                    operation,
                    attempt,
                    fence,
                    spec,
                    directive,
                    model_calls=model_calls,
                    directive_digests=tuple(directive_digests),
                )
            raise RlmWorkbenchPlannerAbstained(str(directive.root["reason"]))

    def _prepare_recovery_continuation(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        spec: RlmWorkbenchJobSpec,
    ) -> _RecoveryContinuation | None:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                """
                SELECT cell.cell_execution_id, cell.source_digest,
                       cell.pre_checkpoint_digest, cell.cell_index,
                       transfer.suspension_revision,
                       transfer.settlement_digest AS settled_receipt_digest,
                       job.cumulative_deadline_unix_ms,
                       (
                         SELECT COUNT(*) FROM rlm_workbench_cells AS counted
                         WHERE counted.operation_id = cell.operation_id
                       ) AS consumed_model_calls
                FROM rlm_workbench_cells AS cell
                JOIN rlm_workbench_rebind_transfers AS transfer
                  ON transfer.operation_id = cell.operation_id
                 AND transfer.cell_execution_id = cell.cell_execution_id
                JOIN rlm_workbench_jobs AS job USING(operation_id)
                WHERE cell.operation_id = ?
                  AND cell.state = 'lost_before_commit'
                  AND (
                    (transfer.state = 'consumed'
                     AND transfer.consumption_kind = 'recovery_fenced_loss')
                    OR transfer.state = 'aborted'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM rlm_workbench_cells AS later
                    WHERE later.operation_id = cell.operation_id
                      AND later.cell_index > cell.cell_index
                  )
                ORDER BY cell.cell_index DESC
                LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
        if row is None:
            return None
        handle = self.workspace_handle(operation)
        worker_binding = self._backend.worker_binding(handle)
        self._registry.bind_workbench_recovery_authority(
            attempt,
            fence.dispatcher_generation,
            fence.lease_epoch,
            fence.owner_digest,
            workspace=handle,
            worker_owner_generation=worker_binding.owner_generation,
            worker_process_identity_digest=worker_binding.process_identity_digest,
        )
        consumed_model_calls = int(row["consumed_model_calls"])
        remaining_model_calls = int(spec.root["budgets"]["max_model_calls"]) - consumed_model_calls
        remaining_wall_time_ms = self._remaining_wall_time_ms(
            int(row["cumulative_deadline_unix_ms"])
        )
        if remaining_model_calls < 1:
            raise RlmWorkbenchModelCallBudgetExceeded(
                "workbench root planner model-call budget exhausted"
            )
        document = RecoveryPlannerInput.model_validate(
            {
                "schema_version": "aar.rlm-recovery-planner-input.v1",
                "operation": operation.model_dump(mode="json"),
                "failed_cell_execution_id": str(row["cell_execution_id"]),
                "failed_cell_source_digest": str(row["source_digest"]),
                "pre_cell_checkpoint_digest": str(row["pre_checkpoint_digest"]),
                "suspension_revision": int(row["suspension_revision"]),
                "settled_ticket_receipt_digests": [str(row["settled_receipt_digest"])],
                "committed_artifact_binding_digests": [],
                "committed_event_digests": [],
                "failure_code": "supervisor_restarted",
                "remaining_model_calls": remaining_model_calls,
                "remaining_wall_time_ms": remaining_wall_time_ms,
            },
            strict=True,
        )
        return _RecoveryContinuation(
            planner_input=document,
            consumed_model_calls=consumed_model_calls,
            failed_source_digest=str(row["source_digest"]),
        )

    def _begin_running(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> RlmWorkbenchJobSpec:
        with self._factory.transaction(write=True) as connection:
            self._assert_attempt_fence(connection, operation, attempt, fence)
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if int(row["cancellation_requested"]) != 0:
                raise RlmWorkbenchConflict("workbench cancellation is already requested")
            if self._now_ms() >= int(row["cumulative_deadline_unix_ms"]):
                raise RlmWorkbenchConflict("workbench cumulative deadline expired")
            if row["phase"] == "preparing_workspace":
                revision = int(row["control_revision"]) + 1
                now = self._now_ms()
                connection.execute(
                    """
                    UPDATE rlm_workbench_jobs
                    SET phase = 'running', control_revision = ?, updated_at_unix_ms = ?
                    WHERE operation_id = ? AND phase = 'preparing_workspace'
                      AND control_revision = ?
                    """,
                    (revision, now, operation.value, int(row["control_revision"])),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RlmWorkbenchConflict("workbench running transition lost its CAS")
                connection.execute(
                    "UPDATE operation_controls SET control_revision = ? WHERE operation_id = ?",
                    (revision, operation.value),
                )
            elif row["phase"] != "running":
                raise RlmWorkbenchConflict(f"cannot run workbench from phase {row['phase']}")
            return RlmWorkbenchJobSpec.model_validate_json(str(row["spec_json"]), strict=True)

    @staticmethod
    def _failure_message_from_result(result: Any) -> str:
        diagnostic = next(
            (event for event in reversed(result.events) if event.kind in {"exception", "stderr"}),
            result.events[-1] if result.events else None,
        )
        detail = (
            canonical_json_bytes(diagnostic.model_dump(mode="json")).decode("utf-8")
            if diagnostic is not None
            else "no worker event detail"
        )
        return f"workbench cell did not complete certainly: {result.status}: {detail[:512]}"

    def _resume_successor_cell(
        self,
        operation: OperationRef,
        spec: RlmWorkbenchJobSpec,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        token: Mapping[str, Any],
        broker_session: WorkspaceBrokerSession,
    ) -> ProgrammableWorkspaceHandle:
        job = self._job_row(operation)
        handle = self._handle_from_row(job)
        with self._factory.transaction(write=False) as connection:
            cell = connection.execute(
                """
                SELECT cell_index, source_json, source_digest, pre_checkpoint_digest
                FROM rlm_workbench_cells
                WHERE operation_id = ? AND cell_execution_id = ?
                  AND state IN ('suspended', 'running')
                """,
                (operation.value, token["cell_execution_id"]),
            ).fetchone()
        if cell is None:
            raise RlmWorkbenchConflict("successor suspended cell does not exist")
        directive = RlmDirective.model_validate_json(str(cell["source_json"]), strict=True)
        if canonical_sha256(directive.root) != str(cell["source_digest"]):
            raise RlmWorkbenchConflict("successor cell source digest changed")
        execution_operation = OperationRef(value=f"exec-{token['cell_execution_id']}")
        budgets = dict(spec.root["budgets"])
        program_spec = WorkspaceProgramSpec(
            code=str(directive.root["code"]),
            wall_time_ms=self._cell_execution_wall_time_ms(
                operation, int(budgets["cell_wall_time_ms"])
            ),
            max_output_chars=int(budgets["max_output_chars_per_cell"]),
            max_events=int(budgets["max_events_per_cell"]),
        )
        ordinal, broker_sequence, receipt_payload = self._settled_broker_receipt_payload(
            operation, token
        )
        receipt_frame = self._supervisor_frame(
            kind="broker_receipt",
            operation=operation,
            token=token,
            payload=receipt_payload,
            frame_sequence=3,
            broker_call_ordinal=ordinal,
        )
        result: WorkspaceProgramResult | None = None
        acknowledgement: WorkspaceBrokerFrame | None = None
        acknowledged = False
        receipt_document = receipt_frame.model_dump(mode="json")
        receipt_json = canonical_json_bytes(receipt_document).decode("utf-8")
        receipt_digest = canonical_sha256(receipt_document)

        def acknowledge(frame: WorkspaceBrokerFrame) -> None:
            nonlocal acknowledged
            if applied is None:
                self._barrier("rebind_delivered_before_ack")
            document = frame.model_dump(mode="json")
            if (
                document["attempt_id"] != token["successor_attempt_id"]
                or document["attempt_fence"] != _attempt_fence_digest(fence)
                or document["cell_execution_id"] != token["cell_execution_id"]
            ):
                raise RlmWorkbenchConflict("successor worker acknowledgement changed authority")
            self._registry.acknowledge_workbench_rebind(
                attempt,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                token_digest=str(token["token_digest"]),
                acknowledgement=dict(document["payload"]),
            )
            # Consuming this settled broker receipt is separate from completing
            # its cell: the same cell may now suspend at another broker call.
            with self._factory.transaction(write=True) as connection:
                self._assert_attempt_fence(connection, operation, attempt, fence)
                broker_row = connection.execute(
                    "SELECT state, response_digest, response_json FROM broker_calls "
                    "WHERE operation_id = ? AND sequence = ?",
                    (operation.value, broker_sequence),
                ).fetchone()
                if broker_row is None:
                    raise RlmWorkbenchConflict("successor broker receipt lost commit authority")
                if broker_row["state"] == "succeeded":
                    if (
                        broker_row["response_digest"] != receipt_digest
                        or broker_row["response_json"] != receipt_json
                    ):
                        raise RlmWorkbenchConflict("successor broker receipt replay changed")
                elif broker_row["state"] == "started":
                    connection.execute(
                        "UPDATE broker_calls SET state = 'succeeded', "
                        "response_digest = ?, response_json = ? "
                        "WHERE operation_id = ? AND sequence = ? AND state = 'started'",
                        (receipt_digest, receipt_json, operation.value, broker_sequence),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise RlmWorkbenchConflict("successor broker receipt lost commit authority")
                else:
                    raise RlmWorkbenchConflict("successor broker receipt is not committable")
            acknowledged = True
            self._barrier("rebind_acked_before_cell_commit")

        with self._active_cell_binding(
            execution_operation,
            operation,
            attempt,
            fence,
            handle,
            spec,
            broker_session,
            cell_execution_id=str(token["cell_execution_id"]),
            broker_call_ordinal=ordinal,
        ):
            applied = self._backend.reconcile_rebind_commit(execution_operation, handle)
            if applied is None:
                binding = self._backend.worker_binding(handle)
            else:
                binding, result, acknowledgement = applied
            if binding.owner_generation != int(
                token["worker_owner_generation"]
            ) or binding.process_identity_digest != str(token["worker_process_identity_digest"]):
                raise RlmWorkbenchConflict("successor worker identity changed before prepare")
            if applied is None:
                prepare_frame = self._supervisor_frame(
                    kind="rebind_prepare",
                    operation=operation,
                    token=token,
                    payload={"phase": "prepare", "token": dict(token)},
                    frame_sequence=2,
                )
                self._backend.send_rebind_prepare(execution_operation, handle, prepare_frame)
                self._barrier("rebind_prepared_before_commit")
            self._registry.commit_workbench_rebind(
                attempt,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                token_digest=str(token["token_digest"]),
                worker_owner_generation=binding.owner_generation,
                worker_process_identity_digest=binding.process_identity_digest,
            )
            if applied is None:
                self._barrier("rebind_committed_before_delivery")
                commit_frame = self._supervisor_frame(
                    kind="rebind_commit",
                    operation=operation,
                    token=token,
                    payload={"phase": "commit", "token": dict(token)},
                    frame_sequence=4,
                )
                result, acknowledgement = self._backend.resume_rebind_commit(
                    execution_operation,
                    handle,
                    program_spec,
                    receipt_frame,
                    commit_frame,
                    on_acknowledgement=acknowledge,
                )
        if result is None or acknowledgement is None:
            raise RlmWorkbenchConflict("successor rebind produced no committed worker result")
        if not acknowledged:
            acknowledge(acknowledgement)
        result_digest = canonical_sha256(workbench_result_document(result))
        if not _cell_result_committable(result, handle, execution_operation):
            raise RlmWorkbenchConflict(self._failure_message_from_result(result))
        current_handle = handle.model_copy(update={"revision": result.revision_after})
        post_checkpoint = self._backend.checkpoint(
            OperationRef(value=f"ck-post-{token['cell_execution_id']}"),
            current_handle,
            WorkspaceCheckpointPolicy(),
            trace_id=f"tr-post-{token['cell_execution_id']}",
        )
        now = self._now_ms()
        post_checkpoint_json = canonical_json_bytes(post_checkpoint.model_dump(mode="json")).decode(
            "utf-8"
        )
        result_json = canonical_json_bytes(workbench_result_document(result)).decode("utf-8")
        with self._factory.transaction(write=False) as connection:
            stage_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT stage_id FROM rlm_workbench_artifact_stages "
                    "WHERE operation_id = ? AND cell_execution_id = ? AND state = 'staged' "
                    "AND ? = 'succeeded' "
                    "ORDER BY stage_id",
                    (operation.value, token["cell_execution_id"], result.status),
                ).fetchall()
            )
        cell_manifest = {
            "operation": operation,
            "cell_execution_id": token["cell_execution_id"],
            "cell_index": int(cell["cell_index"]),
            "attempt_id": token["successor_attempt_id"],
            "attempt_fence": _attempt_fence_digest(fence),
            "source_digest": str(cell["source_digest"]),
            "result_digest": result_digest,
            "pre_checkpoint_digest": str(cell["pre_checkpoint_digest"]),
            "post_checkpoint_digest": post_checkpoint.content_digest,
            "post_checkpoint": post_checkpoint.model_dump(mode="json"),
            "workspace": current_handle,
            "artifact_stage_ids": list(stage_ids),
        }
        cell_manifest_json = canonical_json_bytes(cell_manifest).decode("utf-8")
        cell_manifest_digest = canonical_sha256(cell_manifest)
        with self._factory.transaction(write=True) as connection:
            job_row = self._current_cell_commit_job(connection, operation, attempt, fence)
            control_revision = int(job_row["control_revision"]) + 1
            connection.execute(
                """
                INSERT INTO workspace_checkpoint_catalog(
                    manifest_digest, workspace_id, source_generation,
                    source_revision, backend_capability_digest,
                    environment_digest, creation_operation_id,
                    manifest_json, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(manifest_digest) DO NOTHING
                """,
                (
                    post_checkpoint.content_digest,
                    post_checkpoint.source_handle.workspace.value,
                    post_checkpoint.source_handle.generation,
                    post_checkpoint.source_handle.revision,
                    post_checkpoint.source_handle.backend.capability_digest,
                    post_checkpoint.environment.digest,
                    operation.value,
                    post_checkpoint_json,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE rlm_workbench_cells
                SET state = 'committed', post_checkpoint_digest = ?,
                    post_workspace_revision = ?, result_json = ?, result_digest = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND cell_execution_id = ? AND state = 'running'
                  AND attempt_id = ? AND attempt_fence = ?
                """,
                (
                    post_checkpoint.content_digest,
                    result.revision_after,
                    result_json,
                    result_digest,
                    now,
                    operation.value,
                    token["cell_execution_id"],
                    token["successor_attempt_id"],
                    _attempt_fence_digest(fence),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("successor cell lost commit authority")
            connection.execute(
                """
                INSERT INTO rlm_workbench_cell_manifests(
                    operation_id, cell_execution_id, manifest_json,
                    manifest_digest, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operation.value,
                    token["cell_execution_id"],
                    cell_manifest_json,
                    cell_manifest_digest,
                    now,
                ),
            )
            connection.execute(
                "UPDATE rlm_workbench_artifact_stages "
                "SET state = 'committed', updated_at_unix_ms = ? "
                "WHERE operation_id = ? AND cell_execution_id = ? AND state = 'staged' "
                "AND ? = 'succeeded'",
                (now, operation.value, token["cell_execution_id"], result.status),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != len(stage_ids):
                raise RlmWorkbenchConflict("successor artifact stages changed before commit")
            connection.execute(
                """
                UPDATE rlm_workbench_attempt_authority
                SET workspace_revision = ?, updated_at_unix_ms = ?
                WHERE operation_id = ? AND attempt_id = ? AND attempt_fence = ?
                  AND rebind_token_digest = ?
                """,
                (
                    result.revision_after,
                    now,
                    operation.value,
                    token["successor_attempt_id"],
                    _attempt_fence_digest(fence),
                    token["token_digest"],
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("successor authority lost cell commit")
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET workspace_revision = ?, checkpoint_digest = ?,
                    control_revision = ?, updated_at_unix_ms = ?
                WHERE operation_id = ? AND phase = 'running'
                """,
                (
                    result.revision_after,
                    post_checkpoint.content_digest,
                    control_revision,
                    now,
                    operation.value,
                ),
            )
            connection.execute(
                "UPDATE operation_controls SET control_revision = ? WHERE operation_id = ?",
                (control_revision, operation.value),
            )
        return current_handle

    def _execute_cell(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        spec: RlmWorkbenchJobSpec,
        directive: RlmDirective,
        broker_session: WorkspaceBrokerSession,
    ) -> bool:
        self._require_deadline(operation)
        handle = self.workspace_handle(operation)
        source_json = canonical_json_bytes(directive.root).decode("utf-8")
        source_digest = canonical_sha256(directive.root)
        fence_digest = _attempt_fence_digest(fence)
        with self._factory.transaction(write=False) as connection:
            prepared = connection.execute(
                """
                SELECT cell.*, catalog.manifest_json,
                       authority.worker_owner_generation,
                       authority.worker_process_identity_digest,
                       authority.workspace_id AS authority_workspace_id,
                       authority.workspace_generation AS authority_workspace_generation,
                       authority.workspace_revision AS authority_workspace_revision,
                       authority.cell_execution_id AS authority_cell_execution_id
                FROM rlm_workbench_cells AS cell
                JOIN workspace_checkpoint_catalog AS catalog
                  ON catalog.manifest_digest = cell.pre_checkpoint_digest
                JOIN rlm_workbench_attempt_authority AS authority
                  ON authority.operation_id = cell.operation_id
                WHERE cell.operation_id = ? AND cell.state = 'prepared'
                  AND cell.source_digest = ? AND cell.source_json = ?
                  AND cell.attempt_id = ? AND cell.attempt_fence = ?
                """,
                (
                    operation.value,
                    source_digest,
                    source_json,
                    attempt.attempt_id,
                    fence_digest,
                ),
            ).fetchone()
        if prepared is not None:
            cell_index = int(prepared["cell_index"])
            cell_execution_id = str(prepared["cell_execution_id"])
            if (
                prepared["authority_cell_execution_id"] != cell_execution_id
                or str(prepared["workspace_id"]) != handle.workspace.value
                or int(prepared["workspace_generation"]) != handle.generation
                or int(prepared["pre_workspace_revision"]) != handle.revision
                or str(prepared["authority_workspace_id"]) != handle.workspace.value
                or int(prepared["authority_workspace_generation"]) != handle.generation
                or int(prepared["authority_workspace_revision"]) != handle.revision
            ):
                raise RlmWorkbenchConflict("prepared planner cell authority is stale")
            worker_binding = self._backend.worker_binding(handle)
            if (
                int(prepared["worker_owner_generation"]) != worker_binding.owner_generation
                or str(prepared["worker_process_identity_digest"])
                != worker_binding.process_identity_digest
            ):
                raise RlmWorkbenchConflict("prepared planner cell worker binding is stale")
            pre_checkpoint = WorkspaceCheckpointManifest.model_validate_json(
                str(prepared["manifest_json"]), strict=True
            )
            now = self._now_ms()
            with self._factory.transaction(write=True) as connection:
                self._assert_attempt_fence(connection, operation, attempt, fence)
                connection.execute(
                    """
                    UPDATE rlm_workbench_cells SET state = 'running', updated_at_unix_ms = ?
                    WHERE operation_id = ? AND cell_execution_id = ? AND state = 'prepared'
                      AND attempt_id = ? AND attempt_fence = ?
                    """,
                    (
                        now,
                        operation.value,
                        cell_execution_id,
                        attempt.attempt_id,
                        fence_digest,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RlmWorkbenchConflict("prepared planner cell lost start authority")
        else:
            if (
                normalize_planner_mode(str(spec.root["model"]["execution_mode"]))
                == "caller_delegated_ticketed"
            ):
                raise RlmWorkbenchConflict(
                    "caller-delegated execute_cell lacks durable preparation"
                )
            with self._factory.transaction(write=False) as connection:
                cell_index = int(
                    connection.execute(
                        "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                        (operation.value,),
                    ).fetchone()[0]
                )
            if cell_index >= int(spec.root["budgets"]["max_cells"]):
                raise RlmWorkbenchCellBudgetExceeded("workbench cell budget exhausted")
            cell_execution_id = (
                "cell-"
                + hashlib.sha256(
                    f"{operation.value}\0{cell_index}\0{source_digest}".encode()
                ).hexdigest()[:32]
            )
            pre_checkpoint = self._backend.checkpoint(
                OperationRef(value=f"ck-pre-{cell_execution_id}"),
                handle,
                WorkspaceCheckpointPolicy(),
                trace_id=f"trace-pre-{cell_execution_id}",
            )
            worker_binding = self._backend.worker_binding(handle)
            checkpoint_json = canonical_json_bytes(pre_checkpoint.model_dump(mode="json")).decode(
                "utf-8"
            )
            now = self._now_ms()
            with self._factory.transaction(write=True) as connection:
                self._assert_attempt_fence(connection, operation, attempt, fence)
                connection.execute(
                    """
                    INSERT INTO workspace_checkpoint_catalog(
                        manifest_digest, workspace_id, source_generation,
                        source_revision, backend_capability_digest,
                        environment_digest, creation_operation_id,
                        manifest_json, created_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(manifest_digest) DO NOTHING
                    """,
                    (
                        pre_checkpoint.content_digest,
                        handle.workspace.value,
                        handle.generation,
                        handle.revision,
                        handle.backend.capability_digest,
                        pre_checkpoint.environment.digest,
                        operation.value,
                        checkpoint_json,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO rlm_workbench_cells(
                        operation_id, cell_execution_id, cell_index,
                        source_json, source_digest, pre_checkpoint_digest,
                        post_checkpoint_digest, state, attempt_id, attempt_fence,
                        workspace_id, workspace_generation, pre_workspace_revision,
                        post_workspace_revision, result_json, result_digest,
                        created_at_unix_ms, updated_at_unix_ms
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, 'running', ?, ?, ?, ?, ?,
                              NULL, NULL, NULL, ?, ?)
                    """,
                    (
                        operation.value,
                        cell_execution_id,
                        cell_index,
                        source_json,
                        source_digest,
                        pre_checkpoint.content_digest,
                        attempt.attempt_id,
                        fence_digest,
                        handle.workspace.value,
                        handle.generation,
                        handle.revision,
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO rlm_workbench_attempt_authority(
                        operation_id, attempt_id, attempt_fence,
                        authority_generation, worker_owner_generation,
                        worker_process_identity_digest, workspace_id,
                        workspace_generation, workspace_revision,
                        cell_execution_id, rebind_token_digest,
                        updated_at_unix_ms
                    ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, NULL, ?)
                    ON CONFLICT(operation_id) DO UPDATE SET
                        worker_owner_generation = excluded.worker_owner_generation,
                        worker_process_identity_digest = excluded.worker_process_identity_digest,
                        workspace_id = excluded.workspace_id,
                        workspace_generation = excluded.workspace_generation,
                        workspace_revision = excluded.workspace_revision,
                        cell_execution_id = excluded.cell_execution_id,
                        updated_at_unix_ms = excluded.updated_at_unix_ms
                    WHERE rlm_workbench_attempt_authority.attempt_id = excluded.attempt_id
                      AND rlm_workbench_attempt_authority.attempt_fence = excluded.attempt_fence
                    """,
                    (
                        operation.value,
                        attempt.attempt_id,
                        fence_digest,
                        worker_binding.owner_generation,
                        worker_binding.process_identity_digest,
                        handle.workspace.value,
                        handle.generation,
                        handle.revision,
                        cell_execution_id,
                        now,
                    ),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RlmWorkbenchConflict(
                        "workbench attempt authority changed before cell start"
                    )
        execution_operation = OperationRef(value=f"exec-{cell_execution_id}")
        with self._active_cell_binding(
            execution_operation,
            operation,
            attempt,
            fence,
            handle,
            spec,
            broker_session,
            cell_execution_id=cell_execution_id,
        ):
            try:
                result = self._backend.execute(
                    execution_operation,
                    handle,
                    WorkspaceProgramSpec(
                        code=str(directive.root["code"]),
                        wall_time_ms=self._cell_execution_wall_time_ms(
                            operation,
                            int(spec.root["budgets"]["cell_wall_time_ms"]),
                        ),
                        max_output_chars=int(spec.root["budgets"]["max_output_chars_per_cell"]),
                        max_events=int(spec.root["budgets"]["max_events_per_cell"]),
                    ),
                )
            except WorkspaceBrokerSuspended:
                return True
        self._require_deadline(operation)
        if not _cell_result_committable(result, handle, execution_operation):
            diagnostic = next(
                (
                    event
                    for event in reversed(result.events)
                    if event.kind in {"exception", "stderr"}
                ),
                result.events[-1] if result.events else None,
            )
            detail = (
                canonical_json_bytes(diagnostic.model_dump(mode="json")).decode("utf-8")
                if diagnostic is not None
                else "no worker event detail"
            )
            raise RlmWorkbenchConflict(
                f"workbench cell did not complete certainly: {result.status}: {detail[:512]}"
            )
        post_handle = ProgrammableWorkspaceHandle(
            workspace=result.workspace,
            backend=result.backend,
            generation=result.generation,
            revision=result.revision_after,
        )
        post_checkpoint = self._backend.checkpoint(
            OperationRef(value=f"ck-post-{cell_execution_id}"),
            post_handle,
            WorkspaceCheckpointPolicy(),
            trace_id=f"trace-post-{cell_execution_id}",
        )
        self._require_deadline(operation)
        result_document = workbench_result_document(result)
        result_json = canonical_json_bytes(result_document).decode("utf-8")
        result_digest = canonical_sha256(result_document)
        with self._factory.transaction(write=False) as connection:
            stage_ids = tuple(
                str(row[0])
                for row in connection.execute(
                    "SELECT stage_id FROM rlm_workbench_artifact_stages "
                    "WHERE operation_id = ? AND cell_execution_id = ? AND state = 'staged' "
                    "AND ? = 'succeeded' "
                    "ORDER BY stage_id",
                    (operation.value, cell_execution_id, result.status),
                ).fetchall()
            )
        manifest_payload = {
            "schema_version": "aar.cell-commit-manifest.v1",
            "operation": operation.model_dump(mode="json"),
            "attempt_id": attempt.attempt_id,
            "attempt_fence": fence_digest,
            "workspace": post_handle.workspace.model_dump(mode="json"),
            "workspace_generation": post_handle.generation,
            "pre_revision": handle.revision,
            "post_revision": post_handle.revision,
            "cell_execution_id": cell_execution_id,
            "source_digest": source_digest,
            "result_digest": result_digest,
            "checkpoint_digest": post_checkpoint.content_digest,
            "artifact_stage_ids": list(stage_ids),
        }
        manifest = {
            **manifest_payload,
            "manifest_digest": canonical_sha256(manifest_payload),
        }
        manifest_json = canonical_json_bytes(manifest).decode("utf-8")
        manifest_digest = manifest["manifest_digest"]
        now = self._now_ms()
        with self._factory.transaction(write=True) as connection:
            row = self._current_cell_commit_job(connection, operation, attempt, fence)
            revision = int(row["control_revision"]) + 1
            connection.execute(
                """
                UPDATE rlm_workbench_cells
                SET post_checkpoint_digest = ?, state = 'committed',
                    post_workspace_revision = ?, result_json = ?, result_digest = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND cell_execution_id = ? AND state = 'running'
                """,
                (
                    post_checkpoint.content_digest,
                    post_handle.revision,
                    result_json,
                    result_digest,
                    now,
                    operation.value,
                    cell_execution_id,
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("workbench cell commit lost its CAS")
            connection.execute(
                """
                INSERT INTO rlm_workbench_cell_manifests(
                    operation_id, cell_execution_id, manifest_json,
                    manifest_digest, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    operation.value,
                    cell_execution_id,
                    manifest_json,
                    manifest_digest,
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE rlm_workbench_artifact_stages
                SET state = 'committed', updated_at_unix_ms = ?
                WHERE operation_id = ? AND cell_execution_id = ? AND state = 'staged'
                  AND ? = 'succeeded'
                """,
                (now, operation.value, cell_execution_id, result.status),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != len(stage_ids):
                raise RlmWorkbenchConflict("cell artifact stages changed before commit")
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET workspace_revision = ?, checkpoint_digest = ?,
                    control_revision = ?, updated_at_unix_ms = ?
                WHERE operation_id = ? AND phase = 'running'
                """,
                (
                    post_handle.revision,
                    post_checkpoint.content_digest,
                    revision,
                    now,
                    operation.value,
                ),
            )
            connection.execute(
                "UPDATE operation_controls SET control_revision = ? WHERE operation_id = ?",
                (revision, operation.value),
            )
        return False

    def _completion_directive(
        self, operation: OperationRef, *, connection: Any | None = None
    ) -> RlmDirective | None:
        """Read only the latest committed, digest-bound worker helper projection."""
        if connection is None:
            with self._factory.transaction(write=False) as owned_connection:
                return self._completion_directive(operation, connection=owned_connection)
        with nullcontext(connection):
            row = connection.execute(
                "SELECT * FROM rlm_workbench_cells "
                "WHERE operation_id=? ORDER BY cell_index DESC LIMIT 1",
                (operation.value,),
            ).fetchone()
            if row is None or row["state"] != "committed":
                return None
            result = json.loads(str(row["result_json"]))
            value = result.get("result")
            if not isinstance(value, dict) or set(value) != {"__aar_completion_v1__"}:
                return None
            manifest_row = connection.execute(
                "SELECT manifest_json, manifest_digest FROM rlm_workbench_cell_manifests "
                "WHERE operation_id=? AND cell_execution_id=?",
                (operation.value, row["cell_execution_id"]),
            ).fetchone()
            if manifest_row is None:
                raise RlmWorkbenchConflict("completion cell manifest is unavailable")
            row = {**dict(row), **dict(manifest_row)}
            manifest = json.loads(str(row["manifest_json"]))
            material = dict(manifest)
            embedded = material.pop("manifest_digest", None)
            if manifest.get("schema_version") == "aar.cell-commit-manifest.v1":
                scope_matches = (
                    manifest.get("workspace", {}).get("value") == row["workspace_id"]
                    and manifest.get("workspace_generation") == row["workspace_generation"]
                    and manifest.get("pre_revision") == row["pre_workspace_revision"]
                    and manifest.get("post_revision") == row["post_workspace_revision"]
                )
            elif set(material) == {
                "artifact_stage_ids",
                "attempt_fence",
                "attempt_id",
                "cell_execution_id",
                "cell_index",
                "operation",
                "post_checkpoint",
                "post_checkpoint_digest",
                "pre_checkpoint_digest",
                "result_digest",
                "source_digest",
                "workspace",
            }:
                # Existing successor manifests retain a full handle/checkpoint,
                # not the flat public CellCommitManifest shape. No new wire form.
                handle = ProgrammableWorkspaceHandle.model_validate(
                    manifest["workspace"], strict=True
                )
                checkpoint = WorkspaceCheckpointManifest.model_validate(
                    manifest["post_checkpoint"], strict=True
                )
                scope_matches = (
                    handle.workspace.value == row["workspace_id"]
                    and handle.generation == row["workspace_generation"]
                    and handle.revision == row["post_workspace_revision"]
                    and checkpoint.source_handle == handle
                    and manifest["pre_checkpoint_digest"] == row["pre_checkpoint_digest"]
                    and manifest["post_checkpoint_digest"] == row["post_checkpoint_digest"]
                    and checkpoint.content_digest == row["post_checkpoint_digest"]
                    and manifest["cell_index"] == row["cell_index"]
                )
            else:
                scope_matches = False
            if (
                canonical_sha256(result) != row["result_digest"]
                or manifest.get("result_digest") != row["result_digest"]
                or canonical_sha256(material) != row["manifest_digest"]
                or (embedded is not None and embedded != row["manifest_digest"])
                or manifest.get("cell_execution_id") != row["cell_execution_id"]
                or manifest.get("operation", {}).get("value") != operation.value
                or not scope_matches
                or manifest.get("source_digest") != row["source_digest"]
                or result.get("status") != "succeeded"
                or result.get("operation", {}).get("value") != "exec-" + row["cell_execution_id"]
                or result.get("workspace", {}).get("value") != row["workspace_id"]
                or result.get("generation") != row["workspace_generation"]
                or result.get("revision_before") != row["pre_workspace_revision"]
                or result.get("revision_after") != row["post_workspace_revision"]
            ):
                raise RlmWorkbenchConflict("completion cell result or manifest identity changed")
            intent = value["__aar_completion_v1__"]
            if (
                not isinstance(intent, dict)
                or set(intent) != {"output", "names"}
                or not isinstance(intent["names"], list)
            ):
                raise RlmWorkbenchConflict("invalid completion intent")
            names = intent["names"]
            if any(not isinstance(name, str) for name in names) or len(set(names)) != len(names):
                raise RlmWorkbenchConflict("invalid completion artifact names")
            stages = connection.execute(
                "SELECT stage_id, logical_name FROM rlm_workbench_artifact_stages "
                "WHERE operation_id=? AND cell_execution_id=? AND state='committed'",
                (operation.value, row["cell_execution_id"]),
            ).fetchall()
            selected = [stage for stage in stages if stage["logical_name"] in names]
            if len(selected) != len(names) or any(
                stage["stage_id"] not in manifest["artifact_stage_ids"] for stage in selected
            ):
                raise RlmWorkbenchConflict(
                    "completion artifacts are not in committed cell manifest"
                )
            spec_row = connection.execute(
                "SELECT spec_json FROM rlm_workbench_jobs WHERE operation_id=?",
                (operation.value,),
            ).fetchone()
            if spec_row is None:
                raise RlmWorkbenchConflict("completion job disappeared")
            spec = RlmWorkbenchJobSpec.model_validate_json(str(spec_row["spec_json"]), strict=True)
            if not names and spec.root["completion"]["require_named_artifacts"]:
                raise _InvalidCompletionSelection(
                    str(row["cell_execution_id"]),
                    str(row["result_digest"]),
                    str(row["manifest_digest"]),
                )
            return RlmDirective.model_validate(
                {
                    "kind": "finalize",
                    "output": intent["output"],
                    "artifact_stage_ids": [str(stage["stage_id"]) for stage in selected],
                },
                strict=True,
            )

    def _finalize(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        spec: RlmWorkbenchJobSpec,
        directive: RlmDirective,
        *,
        model_calls: int,
        directive_digests: tuple[str, ...],
        usage_receipt_digests: tuple[str, ...] = (),
        broker_trace: tuple[dict[str, Any], ...] = (),
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> RlmWorkbenchResult:
        output = directive.root["output"]
        contract = spec.root["completion"]["output_contract"]
        encoded_output = canonical_json_bytes(output)
        if len(encoded_output) > int(contract["max_instance_bytes"]):
            raise RlmWorkbenchConflict("final output exceeds its contract byte limit")
        errors = sorted(
            Draft202012Validator(contract["schema"]).iter_errors(output),
            key=lambda item: tuple(str(part) for part in item.absolute_path),
        )
        if errors:
            raise RlmWorkbenchConflict(f"final output contract mismatch: {errors[0].message}")
        stage_ids = tuple(str(item) for item in directive.root["artifact_stage_ids"])
        if spec.root["completion"]["require_named_artifacts"] and not stage_ids:
            raise RlmWorkbenchConflict("finalization requires at least one named artifact")

        with self._factory.transaction(write=True) as connection:
            self._assert_attempt_fence(connection, operation, attempt, fence)
            placeholders = ",".join("?" for _ in stage_ids)
            stage_rows = ()
            if stage_ids:
                stage_rows = connection.execute(
                    "SELECT s.*, j.workspace_id, j.workspace_generation "
                    "FROM rlm_workbench_artifact_stages AS s "
                    "JOIN rlm_workbench_jobs AS j ON j.operation_id = s.operation_id "
                    f"WHERE s.operation_id = ? AND s.state = 'committed' "
                    f"AND s.stage_id IN ({placeholders}) ORDER BY s.logical_name, s.stage_id",
                    (operation.value, *stage_ids),
                ).fetchall()
            if len(stage_rows) != len(stage_ids):
                raise RlmWorkbenchConflict(
                    "finalization references absent or uncommitted artifact stages"
                )
            bindings = tuple(
                ArtifactBinding.model_validate(
                    {
                        "schema_version": "aar.artifact-binding.v1",
                        "logical_name": str(item["logical_name"]),
                        "media_type": str(item["media_type"]),
                        "digest": str(item["content_digest"]),
                        "size_bytes": int(item["size_bytes"]),
                        "operation": operation.model_dump(mode="json"),
                        "workspace": {"type": "workspace", "value": str(item["workspace_id"])},
                        "workspace_generation": int(item["workspace_generation"]),
                        "cell_execution_id": str(item["cell_execution_id"]),
                        "role": str(item["role"]),
                    },
                    strict=True,
                )
                for item in stage_rows
            )
            unresolved = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_suspensions "
                    "WHERE operation_id = ? AND state = 'pending'",
                    (operation.value,),
                ).fetchone()[0]
            )
            if unresolved:
                raise RlmWorkbenchConflict("finalization has unresolved required tickets")
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None or row["phase"] != "running":
                raise RlmWorkbenchConflict("workbench is not running at finalization")
            prior_cancellation_revision = int(row["cancellation_revision"])
            deadline = int(row["cumulative_deadline_unix_ms"])
            revision = int(row["control_revision"]) + 1
            now = self._now_ms()
            connection.execute(
                "UPDATE rlm_workbench_jobs SET phase = 'finalizing', "
                "control_revision = ?, updated_at_unix_ms = ? "
                "WHERE operation_id = ? AND phase = 'running'",
                (revision, now, operation.value),
            )
            connection.execute(
                "UPDATE operation_controls SET control_revision = ? WHERE operation_id = ?",
                (revision, operation.value),
            )

        handle = self.workspace_handle(operation)
        worker_owner_generation = self._backend.worker_binding(handle).owner_generation
        final_checkpoint = self._backend.checkpoint(
            OperationRef(value="ck-final-" + operation.value[:96]),
            handle,
            WorkspaceCheckpointPolicy(),
            trace_id="trace-final-" + operation.value[:96],
        )
        self._backend.close(handle, reason="terminal checkpoint committed")
        with self._factory.transaction(write=False) as connection:
            manifest_digests = tuple(
                str(item[0])
                for item in connection.execute(
                    "SELECT manifest_digest FROM rlm_workbench_cell_manifests "
                    "WHERE operation_id = ? ORDER BY cell_execution_id",
                    (operation.value,),
                ).fetchall()
            )
            python_cells = int(
                connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_cells "
                    "WHERE operation_id = ? AND state = 'committed'",
                    (operation.value,),
                ).fetchone()[0]
            )
        result_payload = {
            "schema_version": "aar.rlm-workbench-result.v1",
            "operation": operation.model_dump(mode="json"),
            "terminal_status": "succeeded",
            "certainty": "certain",
            "output": output,
            "output_digest": canonical_sha256(output),
            "artifact_bindings": [item.model_dump(mode="json") for item in bindings],
            "child_lineage": [],
            "unresolved_required_children": 0,
            "model_calls": model_calls,
            "subagent_calls": 0,
            "python_cells": python_cells,
            "recovery_planner_calls": 0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "step_trace_digest": canonical_sha256(
                {"directives": directive_digests, "cell_manifests": manifest_digests}
            ),
            "broker_trace_digest": canonical_sha256(broker_trace),
            "usage_receipt_digests": list(usage_receipt_digests),
            "workspace_disposition": "closed",
            "final_checkpoint_digest": final_checkpoint.content_digest,
        }
        result = RlmWorkbenchResult.model_validate(
            {**result_payload, "result_digest": canonical_sha256(result_payload)},
            strict=True,
        )
        proposal_digest = canonical_sha256(directive.root)
        fence_digest = _attempt_fence_digest(fence)
        manifest_payload = {
            "schema_version": "aar.finalization-manifest.v1",
            "operation": operation.model_dump(mode="json"),
            "finalizer_attempt_id": attempt.attempt_id,
            "finalizer_attempt_fence": fence_digest,
            "expected_prior_phase": "finalizing",
            "control_revision": revision,
            "expected_cancellation_revision": prior_cancellation_revision,
            "expected_deadline_unix_ms": deadline,
            "worker_owner_generation": worker_owner_generation,
            "unresolved_required_ticket_count": 0,
            "result_digest": result.root["result_digest"],
            "artifact_binding_digests": [
                canonical_sha256(item.model_dump(mode="json")) for item in bindings
            ],
            "usage_digest": canonical_sha256(list(usage_receipt_digests)),
            "child_policy_digest": canonical_sha256([]),
            "workspace_disposition_digest": canonical_sha256(
                {"workspace_disposition": "closed", "checkpoint": final_checkpoint.content_digest}
            ),
        }
        manifest = {
            **manifest_payload,
            "manifest_digest": canonical_sha256(manifest_payload),
        }
        with self._factory.transaction(write=True) as connection:
            self._assert_attempt_fence(connection, operation, attempt, fence)
            if self._now_ms() >= deadline:
                raise RlmWorkbenchDeadlineExceeded(
                    "workbench cumulative deadline expired before finalization commit"
                )
            self._artifact_publications.finalize_in_transaction(
                connection,
                manifest,
                result=result.root,
                proposal_digest=proposal_digest,
            )
        return result

    def _deadline(self, operation: OperationRef) -> int:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT cumulative_deadline_unix_ms FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        if row is None:
            raise KeyError(operation.value)
        return int(row[0])

    def _require_deadline(self, operation: OperationRef) -> None:
        if self._now_ms() >= self._deadline(operation):
            raise RlmWorkbenchDeadlineExceeded("workbench cumulative deadline expired")

    def _remaining_wall_time_ms(self, deadline_unix_ms: int) -> int:
        remaining_ms = deadline_unix_ms - self._now_ms()
        if remaining_ms < 1:
            raise RlmWorkbenchDeadlineExceeded("workbench cumulative deadline expired")
        return remaining_ms

    def _cell_execution_wall_time_ms(self, operation: OperationRef, requested_ms: int) -> int:
        return min(requested_ms, self._remaining_wall_time_ms(self._deadline(operation)))

    def handle_broker_request(
        self,
        execution_operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        with self._active_lock:
            active = self._active_cells.get(execution_operation.value)
            if active is None:
                raise RlmWorkbenchConflict("broker request has no active cell authority")
            if active.handle != handle:
                raise RlmWorkbenchConflict("broker request workspace boundary changed")
            active.broker_call_ordinal += 1
            ordinal = active.broker_call_ordinal
            context = self._broker_context(active, request, ordinal)
            payload = {
                "context": context,
                "method": request.get("method"),
                "contract_id": request.get("contract_id"),
                "request_digest": canonical_sha256(request),
                "request": request,
            }
            document = {
                "schema_version": "aar.workspace-broker-frame.v1",
                "kind": "broker_intent",
                "direction": "worker_to_supervisor",
                "committed": False,
                "operation": active.operation.model_dump(mode="json"),
                "attempt_id": active.attempt.attempt_id,
                "attempt_fence": _attempt_fence_digest(active.fence),
                "workspace": handle.workspace.model_dump(mode="json"),
                "workspace_generation": handle.generation,
                "workspace_revision": handle.revision,
                "cell_execution_id": active.cell_execution_id,
                "frame_sequence": ordinal * 2 - 1,
                "broker_call_ordinal": ordinal,
                "deadline_unix_ms": context["deadline_unix_ms"],
                "payload": payload,
                "payload_digest": canonical_sha256(payload),
                "payload_bytes": len(canonical_json_bytes(payload)),
            }
            frame = WorkspaceBrokerFrame.model_validate(document, strict=True)
            session = active.session
        receipt = session.handle_frame(frame)
        return dict(receipt.root["payload"])

    def _broker_context(
        self,
        active: _ActiveCell,
        request: Mapping[str, Any],
        ordinal: int,
    ) -> dict[str, Any]:
        method = str(request.get("method", ""))
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT request_json FROM operations WHERE operation_id = ?",
                (active.operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(active.operation.value)
            envelope = json.loads(str(row["request_json"]))
        grants = [
            item
            for item in envelope.get("grants", [])
            if isinstance(item, dict) and item.get("capability") == method
        ]
        if len(grants) != 1:
            raise RlmWorkbenchConflict("broker method does not bind one admitted grant")
        return {
            "schema_version": "aar.broker-context.v2",
            "operation": active.operation.model_dump(mode="json"),
            "attempt_id": active.attempt.attempt_id,
            "attempt_fence": _attempt_fence_digest(active.fence),
            "workspace": active.handle.workspace.model_dump(mode="json"),
            "workspace_generation": active.handle.generation,
            "workspace_revision_before": active.handle.revision,
            "cell_execution_id": active.cell_execution_id,
            "broker_call_ordinal": ordinal,
            "contract_id": request.get("contract_id"),
            "grant_id": grants[0]["grant_id"],
            "idempotency_key": (
                f"broker-{active.cell_execution_id.removeprefix('cell-')[:40]}-{ordinal}"
            ),
            "deadline_unix_ms": min(
                int(grants[0]["expires_at_unix_ms"]),
                self._deadline(active.operation),
            ),
        }

    def _dispatch_broker_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        context = payload["context"]
        operation = OperationRef.model_validate(context["operation"], strict=True)
        self._require_deadline(operation)
        request = payload["request"]
        request_digest = str(payload["request_digest"])
        owner = {
            "kind": "cell",
            "cell_execution_id": context["cell_execution_id"],
            "broker_call_ordinal": context["broker_call_ordinal"],
        }
        ticket_id = (
            "ticket-"
            + hashlib.sha256(
                canonical_json_bytes(
                    {
                        "operation": operation,
                        "owner": owner,
                        "request_digest": request_digest,
                    }
                )
            ).hexdigest()[:48]
        )
        with self._active_lock:
            active = next(
                (
                    item
                    for item in self._active_cells.values()
                    if item.operation == operation
                    and item.cell_execution_id == context["cell_execution_id"]
                ),
                None,
            )
        if active is None:
            raise RlmWorkbenchConflict("broker intent has no active cell authority")
        with self._factory.transaction(write=False) as connection:
            job = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if job is None:
                raise KeyError(operation.value)
            if job["phase"] != "running":
                raise RlmWorkbenchConflict("broker suspension requires a running workbench")
            cell = connection.execute(
                "SELECT pre_checkpoint_digest FROM rlm_workbench_cells "
                "WHERE operation_id = ? AND cell_execution_id = ?",
                (operation.value, context["cell_execution_id"]),
            ).fetchone()
            if cell is None or cell["pre_checkpoint_digest"] is None:
                raise RlmWorkbenchConflict("broker suspension has no certain pre-cell checkpoint")
            suspension_revision = int(
                connection.execute(
                    "SELECT COALESCE(MAX(suspension_revision), 0) + 1 "
                    "FROM rlm_workbench_suspensions WHERE operation_id = ?",
                    (operation.value,),
                ).fetchone()[0]
            )
            ticket_payload = {
                "schema_version": "aar.caller-work-ticket.v1",
                "ticket_id": ticket_id,
                "operation": operation.model_dump(mode="json"),
                "suspension_revision": suspension_revision,
                "revision": 0,
                "owner": owner,
                "request": request,
                "request_digest": request_digest,
                "state": "pending",
                "deadline_unix_ms": min(
                    int(context["deadline_unix_ms"]),
                    int(job["cumulative_deadline_unix_ms"]),
                ),
                "claimant": None,
                "physical_attempt": None,
                "settled_receipt_digest": None,
                "settled_at_unix_ms": None,
            }
            ticket = CallerWorkTicket.model_validate(
                {
                    **ticket_payload,
                    "ticket_digest": canonical_sha256(ticket_payload),
                },
                strict=True,
            )
        try:
            self._registry.suspend_workbench_attempt(
                active.attempt,
                active.fence.dispatcher_generation,
                active.fence.lease_epoch,
                active.fence.owner_digest,
                attempt_fence=_attempt_fence_digest(active.fence),
                cell_execution_id=active.cell_execution_id,
                pre_checkpoint_digest=str(cell["pre_checkpoint_digest"]),
                ticket=ticket,
                ticket_writer=self._caller_work,
            )
        except InvalidTransition as error:
            if self._now_ms() >= self._deadline(operation):
                raise RlmWorkbenchDeadlineExceeded(
                    "workbench cumulative deadline expired before caller suspension"
                ) from error
            raise
        raise WorkspaceBrokerSuspended(
            ticket_id=ticket.ticket_id,
            suspension_revision=ticket.suspension_revision,
        )

    def park_claimed(
        self,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
        error: BaseException,
    ) -> RlmWorkbenchSnapshot:
        failure = {
            "schema_version": "aar.envelope.v1",
            "category": "worker",
            "code": "RECONCILIATION_REQUIRED",
            "message": (str(error) or type(error).__name__)[:512],
            "retryable": True,
            "certainty": "indeterminate",
            "operation": operation.model_dump(mode="json"),
            "details": [
                {"name": "operator.action", "value": "reconcile-or-restore-checkpoint"},
                {"name": "error.type", "value": type(error).__name__[:512]},
            ],
        }
        if isinstance(error, RlmWorkbenchPlannerAbstained):
            failure["details"].extend(
                [
                    {"name": "planner_decision", "value": "abstain"},
                    {"name": "reason_digest", "value": error.reason_digest},
                ]
            )
        now = self._now_ms()
        with self._factory.transaction(write=True) as connection:
            self._assert_attempt_fence(connection, operation, attempt, fence)
            row = connection.execute(
                "SELECT phase, control_revision FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if row["phase"] in _TERMINAL_PHASES:
                raise RlmWorkbenchConflict("cannot park a terminal workbench job")
            revision = int(row["control_revision"]) + 1
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET phase = 'parked', certainty = 'indeterminate',
                    failure_json = ?, control_revision = ?, updated_at_unix_ms = ?
                WHERE operation_id = ? AND control_revision = ?
                """,
                (
                    canonical_json_bytes(failure).decode("utf-8"),
                    revision,
                    now,
                    operation.value,
                    int(row["control_revision"]),
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("workbench park transition lost its CAS")
            connection.execute(
                "UPDATE operation_controls SET control_revision = ? WHERE operation_id = ?",
                (revision, operation.value),
            )
        return self.snapshot(operation)

    def _current_cell_commit_job(
        self,
        connection: Any,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> Any:
        self._assert_attempt_fence(connection, operation, attempt, fence)
        try:
            job, _ = ArtifactPublicationRepository.current_native_cell_authority(
                connection,
                operation.value,
                attempt.attempt_id,
                _attempt_fence_digest(fence),
                now_unix_ms=self._now_ms(),
            )
        except ArtifactPublicationConflict as error:
            raise RlmWorkbenchConflict(str(error)) from error
        return job

    def _assert_attempt_fence(
        self,
        connection: Any,
        operation: OperationRef,
        attempt: OperationAttemptRefV1,
        fence: AttemptFence,
    ) -> None:
        row = connection.execute(
            """
            SELECT 1
            FROM operation_attempts AS attempt
            JOIN operation_dispatch AS dispatch
              ON dispatch.operation_id = attempt.operation_id
            JOIN operation_leases AS lease
              ON lease.operation_id = attempt.operation_id
             AND lease.attempt_no = attempt.attempt_no
            WHERE attempt.operation_id = ? AND attempt.attempt_no = ?
              AND attempt.attempt_id = ? AND attempt.state = 'running'
              AND attempt.dispatcher_generation = ?
              AND dispatch.state = 'running'
              AND dispatch.current_attempt_no = attempt.attempt_no
              AND lease.lease_epoch = ? AND lease.owner_digest = ?
              AND lease.released_at_unix_ms IS NULL
              AND lease.expires_at_unix_ms > ?
            """,
            (
                operation.value,
                attempt.attempt_no,
                attempt.attempt_id,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
                self._now_ms(),
            ),
        ).fetchone()
        if row is None:
            raise RlmWorkbenchConflict("workbench attempt fence is stale")

    def close(self) -> None:
        self._caller_work.bind_settlement_handler(None)
        self._dispatch_notifier = None
        self._deadline_terminalizer = None
        with self._active_lock:
            sessions = tuple(self._broker_sessions.values())
            self._broker_sessions.clear()
        for session in sessions:
            session.close()
        unbind_artifact_stage_handler = getattr(
            self._backend, "unbind_artifact_stage_handler", None
        )
        if callable(unbind_artifact_stage_handler):
            unbind_artifact_stage_handler(self._artifact_stage_callback)
        self._artifact_publications.close()
        self._factory.close()

    def _prepare_workspace(
        self,
        operation: OperationRef,
        session: SessionRef,
    ) -> ProgrammableWorkspaceHandle:
        stored_handle: ProgrammableWorkspaceHandle | None = None
        with self._factory.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            self._require_preparation_authority(connection, row)
            if row["workspace_id"] is not None:
                stored_handle = self._handle_from_row(row)
            elif row["phase"] == "accepted":
                revision = int(row["control_revision"]) + 1
                now = self._now_ms()
                connection.execute(
                    """
                    UPDATE rlm_workbench_jobs
                    SET phase = 'preparing_workspace', control_revision = ?,
                        updated_at_unix_ms = ?
                    WHERE operation_id = ? AND phase = 'accepted'
                      AND control_revision = ? AND cancellation_requested = 0
                    """,
                    (revision, now, operation.value, row["control_revision"]),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RlmWorkbenchConflict("workspace preparation lost its phase CAS")
                connection.execute(
                    """
                    UPDATE operation_controls SET control_revision = ?
                    WHERE operation_id = ? AND control_revision = ?
                      AND cancellation_requested = 0
                    """,
                    (revision, operation.value, row["control_revision"]),
                )
                if connection.execute("SELECT changes()").fetchone()[0] != 1:
                    raise RlmWorkbenchConflict("workspace preparation lost its control CAS")
            elif row["phase"] != "preparing_workspace":
                raise RlmWorkbenchConflict(f"cannot prepare workspace from phase {row['phase']}")

        if stored_handle is not None:
            return self._backend.attach(stored_handle, session)

        workspace = WorkspaceRef(value=_workspace_id(operation))
        handle = self._backend.create(workspace, session)
        with self._factory.transaction(write=True) as connection:
            row = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            # Worker creation runs outside the transaction. A cancellation or
            # terminal phase acquired in that interval wins over publication.
            self._require_preparation_authority(connection, row)
            if row["workspace_id"] is not None:
                stored = self._handle_from_row(row)
                if stored != handle:
                    raise RlmWorkbenchConflict(
                        "workbench already binds another workspace generation"
                    )
                return stored
            revision = int(row["control_revision"]) + 1
            now = self._now_ms()
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET workspace_id = ?, workspace_generation = ?,
                    workspace_revision = ?, control_revision = ?,
                    updated_at_unix_ms = ?
                WHERE operation_id = ? AND phase = 'preparing_workspace'
                  AND workspace_id IS NULL AND control_revision = ?
                  AND cancellation_requested = 0
                """,
                (
                    handle.workspace.value,
                    handle.generation,
                    handle.revision,
                    revision,
                    now,
                    operation.value,
                    row["control_revision"],
                ),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("workspace publication lost its phase CAS")
            connection.execute(
                """
                UPDATE operation_controls SET control_revision = ?
                WHERE operation_id = ? AND control_revision = ?
                  AND cancellation_requested = 0
                """,
                (revision, operation.value, row["control_revision"]),
            )
            if connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise RlmWorkbenchConflict("workspace publication lost its control CAS")
        return handle

    @staticmethod
    def _require_preparation_authority(connection: Any, row: Any) -> None:
        if str(row["phase"]) in _TERMINAL_PHASES:
            raise RlmWorkbenchConflict(
                f"cannot prepare workspace from terminal phase {row['phase']}"
            )
        control = connection.execute(
            "SELECT 1 FROM operation_controls "
            "WHERE operation_id = ? AND control_revision = ? AND cancellation_requested = 0",
            (row["operation_id"], row["control_revision"]),
        ).fetchone()
        if bool(row["cancellation_requested"]) or control is None:
            raise RlmWorkbenchConflict("workspace preparation lost its control authority")

    @staticmethod
    def _assert_admission_binding(
        row: Any,
        *,
        spec_json: str,
        spec_digest: str,
        context_digest: str,
        route_digest: str,
    ) -> None:
        if (
            str(row["spec_json"]) != spec_json
            or str(row["spec_digest"]) != spec_digest
            or str(row["context_digest"]) != context_digest
            or str(row["route_binding_digest"]) != route_digest
        ):
            raise RlmWorkbenchConflict(
                "workbench operation already binds different admission bytes"
            )

    def _handle_from_row(self, row: Any) -> ProgrammableWorkspaceHandle:
        return ProgrammableWorkspaceHandle(
            workspace=WorkspaceRef(value=str(row["workspace_id"])),
            backend=self._backend.descriptor,
            generation=int(row["workspace_generation"]),
            revision=int(row["workspace_revision"]),
        )


__all__ = [
    "RlmWorkbenchConflict",
    "RlmWorkbenchCoordinator",
    "RlmWorkbenchError",
    "RlmWorkbenchUnsupported",
]
