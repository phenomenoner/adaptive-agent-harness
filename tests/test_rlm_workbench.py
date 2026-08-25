from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event
from typing import Any, cast

import pytest

from aar.broker_models import (
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import RecoveryPlannerInput, RlmWorkbenchExecuteInput
from aar.runtime.caller_work import CallerWorkRepository, build_reconcile_fence
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.model_broker import ModelExecutionJournal
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.rlm_workbench import RlmWorkbenchCoordinator
from aar.schemas import (
    Budget,
    Grant,
    HostRef,
    LaneRef,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
)

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
FIXTURE = SDD / "fixtures" / "valid-workbench-execute.json"
BASELINE_SQL = SDD / "fixtures" / "registry-v5.sql"
MIGRATION_SQL = SDD / "migration-v6.sql"
FIXED_NOW_MS = 1_999_999_990_000


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def prepare_v6_registry(database: Path, snapshot_path: Path) -> None:
    from aar.runtime.migrations import apply_registry_v6, create_sqlite_backup

    with sqlite3.connect(database) as connection:
        connection.executescript(BASELINE_SQL.read_text(encoding="utf-8"))
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = MIGRATION_SQL.read_bytes()
    payload: dict[str, object] = {
        "schema_version": "aar.migration-v6-attestation-payload.v1",
        "migration_version": 6,
        "cutover_epoch": "cutover-workbench-test",
        "snapshot_id": "snapshot-workbench-test",
        "snapshot_sha256": snapshot.sha256,
        "snapshot_size_bytes": snapshot.size_bytes,
        "canonical_v5_row_set_digest": "sha256:" + "2" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "profile_digest": "sha256:" + "4" * 64,
        "skill_digest": "sha256:" + "5" * 64,
        "contract_manifest_digest": "sha256:" + "6" * 64,
        "migration_sql_digest": digest_bytes(migration_bytes),
        "external_authority_store_id": "cutover-authority-workbench-test",
        "external_authority_prepared_digest": "sha256:" + "8" * 64,
        "started_at_unix_ms": FIXED_NOW_MS,
        "completed_at_unix_ms": FIXED_NOW_MS + 1,
        "foreign_key_violation_count": 0,
        "integrity_result": "ok",
    }
    attestation = {
        "attestation": payload,
        "attestation_digest": canonical_sha256(payload),
    }
    apply_registry_v6(
        database,
        migration_sql_bytes=migration_bytes,
        attestation=attestation,
    )


def workbench_request(
    host: ReferenceHost,
    *,
    require_named_artifacts: bool | None = None,
    execution_mode: str = "service_managed",
    max_model_calls: int | None = None,
) -> RlmWorkbenchExecuteInput:
    value = json.loads(FIXTURE.read_text(encoding="utf-8"))
    value["context"]["runtime_generation"] = host.runtime_generation
    value["context"]["capability_digest"] = host.capabilities.digest
    value["spec"]["budgets"]["total_wall_time_ms"] = (
        value["context"]["deadline_unix_ms"] - FIXED_NOW_MS
    )
    value["spec"]["model"]["execution_mode"] = execution_mode
    if max_model_calls is not None:
        value["spec"]["budgets"]["max_model_calls"] = max_model_calls
        value["context"]["budget_model_requests"] = max_model_calls
    if require_named_artifacts is not None:
        value["spec"]["completion"]["require_named_artifacts"] = require_named_artifacts
    return RlmWorkbenchExecuteInput.model_validate(value, strict=True)


class BlockingFinalizePlanner:
    def __init__(self) -> None:
        self.cell_committed = Event()
        self.release_finalization = Event()
        self.calls = 0

    def plan(self, _operation: Any, _spec: Any, _snapshot: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            return {
                "kind": "execute_cell",
                "code": "answer = 6 * 7\nanswer",
                "expected_result_hint": "42",
            }
        if self.calls == 2:
            self.cell_committed.set()
            if not self.release_finalization.wait(timeout=10):
                raise TimeoutError("test did not release finalization")
            return {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": [],
            }
        raise AssertionError("planner requested an unexpected extra directive")


class DeadlineCrossingPlanner:
    def __init__(self, clock: list[int]) -> None:
        self._clock = clock

    def plan(self, _operation: Any, _spec: Any, _snapshot: Any) -> dict[str, Any]:
        self._clock[0] += 20_000
        return {
            "kind": "execute_cell",
            "code": "answer = 42\nanswer",
            "expected_result_hint": "42",
        }


class BrokerRoundTripPlanner:
    def __init__(self) -> None:
        fixture = json.loads(
            (SDD / "fixtures" / "valid-worker-broker-intent-typed.json").read_text(encoding="utf-8")
        )
        self.request = fixture["payload"]["request"]
        self.calls = 0

    def plan(self, _operation: Any, _spec: Any, _snapshot: Any) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            request_json = json.dumps(self.request, sort_keys=True)
            return {
                "kind": "execute_cell",
                "code": (
                    "import json\n"
                    f"broker_receipt = aar_broker(json.loads({request_json!r}))\n"
                    "answer = broker_receipt['observation']['output_text']\n"
                    "answer"
                ),
                "expected_result_hint": "ok",
            }
        if self.calls == 2:
            return {
                "kind": "finalize",
                "output": {"answer": "ok", "artifacts": []},
                "artifact_stage_ids": [],
            }
        raise AssertionError("planner requested an unexpected extra directive")


def wait_for_pending_ticket(database: Path, *, timeout_s: float = 10.0) -> str:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        with sqlite3.connect(database) as connection:
            row = connection.execute(
                "SELECT ticket_id FROM caller_work_tickets WHERE state = 'pending'"
            ).fetchone()
        if row is not None:
            return str(row[0])
        time.sleep(0.01)
    raise TimeoutError("workbench did not persist a pending caller-work ticket")


def caller_common_input(
    database: Path,
    request: RlmWorkbenchExecuteInput,
    ticket: Any,
    *,
    idempotency_key: str,
) -> dict[str, Any]:
    with sqlite3.connect(database) as connection:
        authority = connection.execute(
            """
            SELECT c.control_revision, j.cancellation_revision,
                   j.cumulative_deadline_unix_ms
            FROM operation_controls AS c
            JOIN rlm_workbench_jobs AS j USING(operation_id)
            WHERE c.operation_id = ?
            """,
            (ticket.operation.value,),
        ).fetchone()
    assert authority is not None
    return {
        "context": copy.deepcopy(request.root["context"]),
        "operation": ticket.operation.model_dump(mode="json"),
        "expected_control_revision": int(authority[0]),
        "expected_cancellation_revision": int(authority[1]),
        "expected_suspension_revision": ticket.suspension_revision,
        "expected_cumulative_deadline_unix_ms": int(authority[2]),
        "ticket_id": ticket.ticket_id,
        "expected_revision": ticket.revision,
        "ticket_digest": ticket.ticket_digest,
        "idempotency_key": idempotency_key,
    }


def commit_pending_ticket_success(
    repository: Any,
    database: Path,
    request: RlmWorkbenchExecuteInput,
    pending: Any,
    *,
    prefix: str,
) -> Any:
    claimed = repository.claim(
        {
            **caller_common_input(
                database,
                request,
                pending,
                idempotency_key=f"{prefix}-claim",
            ),
            "adapter_id": "fixture-adapter",
            "adapter_generation": 1,
            "claim_lease_ms": 5_000,
        }
    )
    assert claimed.claimant is not None
    assert claimed.physical_attempt is not None
    started = repository.mark_send_started(
        {
            **caller_common_input(
                database,
                request,
                claimed,
                idempotency_key=f"{prefix}-send-start",
            ),
            "claim_id": claimed.claimant.claim_id,
            "claim_fence": claimed.claimant.claim_fence,
            "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
            "expected_claim_expires_at_unix_ms": claimed.claimant.claim_expires_at_unix_ms,
            "provider_or_child_idempotency_key": (
                claimed.physical_attempt.provider_or_child_idempotency_key
            ),
            "sent_request_digest": claimed.request_digest,
            "lookup_supported": True,
            "cancel_supported": True,
        }
    )
    assert started.claimant is not None
    assert started.physical_attempt is not None
    evidence, model_response = model_commit_evidence(started, "ok")
    return repository.commit(
        {
            **caller_common_input(
                database,
                request,
                started,
                idempotency_key=f"{prefix}-commit",
            ),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": FIXED_NOW_MS + 2,
            "provider_or_child_request_id": f"provider-request-{prefix}",
            "observation": {
                "kind": "model",
                "outcome": "succeeded",
                "output_text": "ok",
                "output_digest": digest_bytes(b"ok"),
                **evidence,
            },
            "model_response": model_response,
        }
    )


def caller_success_candidate_receipt(
    started: Any,
    *,
    prefix: str,
    model_evidence: dict[str, str] | None = None,
) -> dict[str, Any]:
    physical = started.physical_attempt
    assert physical is not None
    payload: dict[str, Any] = {
        "schema_version": "aar.caller-work-candidate-receipt.v1",
        "ticket_id": started.ticket_id,
        "ticket_digest": started.ticket_digest,
        "physical_attempt_id": physical.physical_attempt_id,
        "sent_request_digest": started.request_digest,
        "sent_at_unix_ms": FIXED_NOW_MS + 2,
        "provider_or_child_request_id": f"provider-request-{prefix}",
        "observation": {
            "kind": "model",
            "outcome": "succeeded",
            "output_text": "ok",
            "output_digest": digest_bytes(b"ok"),
            **(
                model_evidence
                or {
                    "route_receipt_digest": "sha256:" + "b" * 64,
                    "usage_receipt_digest": "sha256:" + "c" * 64,
                    "host_receipt_digest": "sha256:" + "d" * 64,
                }
            ),
        },
        "callback_principal_id": "fixture-principal",
        "callback_session_id": "fixture-session",
        "callback_adapter_id": "fixture-adapter",
        "callback_adapter_generation": 1,
        "observed_at_unix_ms": FIXED_NOW_MS + 3,
        "signature_digest": "sha256:" + "e" * 64,
    }
    return {**payload, "receipt_digest": canonical_sha256(payload)}


def model_commit_evidence(
    started: Any,
    output_text: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    request_document = dict(started.root["request"])
    binding = ModelRouteBinding.model_validate(request_document["route_binding"], strict=True)
    route_receipt = ModelRouteReceipt.issue(
        requested=binding,
        effective=EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=binding.provider,
            model=binding.model,
            reasoning_effort=binding.reasoning_effort,
        ),
        finish_reason="stop",
        provider_response_id="provider-deadline-1",
        lookup_supported=True,
    )
    usage = ModelUsageRecord(
        accounting_source="provider_reported",
        input_tokens=7,
        output_tokens=3,
        total_tokens=10,
    )
    response = ModelResponse(
        output_text=output_text,
        route_receipt=route_receipt,
        usage=usage,
    )
    return (
        {
            "route_receipt_digest": route_receipt.receipt_digest,
            "usage_receipt_digest": canonical_sha256(usage),
            "host_receipt_digest": canonical_sha256(response),
        },
        response.model_dump(mode="json"),
    )


def workbench_envelope(
    host: ReferenceHost,
    request: RlmWorkbenchExecuteInput,
) -> RequestEnvelope:
    context = request.root["context"]
    principal = PrincipalRef(value=context["principal_id"])
    capabilities = {
        "fixture-artifact-grant": "artifact.write",
        "fixture-model-grant": "model.request",
        "fixture-rlm-grant": "rlm.workbench.execute",
        "fixture-subagent-grant": "subagent.submit",
    }
    grants = tuple(
        Grant(
            grant_id=grant_id,
            capability=capabilities[grant_id],
            issued_to=principal,
            expires_at_unix_ms=context["deadline_unix_ms"],
        )
        for grant_id in context["grant_ids"]
    )
    return RequestEnvelope(
        request_id=context["request_id"],
        idempotency_key=context["idempotency_key"],
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="rlm-workbench"),
        session=SessionRef(value=context["session_id"]),
        workspace=None,
        runtime_generation=context["runtime_generation"],
        workspace_generation=None,
        expected_workspace_revision=None,
        capability_digest=context["capability_digest"],
        deadline_unix_ms=context["deadline_unix_ms"],
        grants=grants,
        budget=Budget(
            wall_time_ms=context["budget_wall_time_ms"],
            model_requests=context["budget_model_requests"],
            input_tokens=context["budget_input_tokens"],
            output_tokens=context["budget_output_tokens"],
            child_operations=context["budget_child_operations"],
            artifact_bytes=context["budget_artifact_bytes"],
        ),
        trace_id=f"trace-{context['request_id']}",
        input_digest=canonical_sha256(request.root["spec"]),
    )


def database_counts(path: Path) -> dict[str, Any]:
    with sqlite3.connect(path) as connection:
        operation_count = connection.execute(
            "SELECT COUNT(*) FROM operations WHERE idempotency_key = ?",
            ("fixture-idempotency-key",),
        ).fetchone()
        job_count = connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()
        dispatch = connection.execute(
            "SELECT kind, state, COUNT(*) FROM operation_dispatch GROUP BY kind, state"
        ).fetchone()
    return {
        "workbench_operations": operation_count,
        "jobs": job_count,
        "dispatch": dispatch,
    }


class RecoveryBrokerRoundTripPlanner(BrokerRoundTripPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.recovery_inputs: list[RecoveryPlannerInput] = []

    def recover(
        self,
        operation: Any,
        spec: Any,
        recovery_input: RecoveryPlannerInput,
    ) -> dict[str, Any]:
        del operation, spec
        self.recovery_inputs.append(recovery_input)
        return {
            "kind": "execute_cell",
            "code": 'recovered_answer = "ok"\nrecovered_answer',
            "expected_result_hint": "continue from the durable receipt without replay",
        }


def test_start_only_admission_creates_one_operation_scoped_ipython_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        request = workbench_request(host)
        envelope = workbench_envelope(host, request)

        first = host.submit_rlm_workbench(envelope, request)
        first_snapshot = host.rlm_workbench.snapshot(first.operation)
        first_handle = host.rlm_workbench.workspace_handle(first.operation)

        second = host.submit_rlm_workbench(
            envelope,
            RlmWorkbenchExecuteInput.model_validate(copy.deepcopy(request.root), strict=True),
        )
        second_snapshot = host.rlm_workbench.snapshot(second.operation)
        second_handle = host.rlm_workbench.workspace_handle(second.operation)

        assert first.operation == second.operation
        assert first.state.value == "accepted"
        assert first_handle == second_handle
        assert first_handle.backend.kind == "ipython"
        assert first_handle.generation == 1
        assert first_handle.revision == 0
        assert first_snapshot.phase == second_snapshot.phase == "preparing_workspace"
        assert first_snapshot.workspace.generation == 1
        assert first_snapshot.workspace.revision == 0
        assert host.program_workspace.inspect(first_handle).revision == 0
        assert database_counts(database) == {
            "workbench_operations": (1,),
            "jobs": (1,),
            "dispatch": ("rlm.workbench.execute", "queued", 1),
        }
    finally:
        host.close()


def test_concurrent_exact_admission_reuses_operation_and_workspace_generation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        request = workbench_request(host)
        envelope = workbench_envelope(host, request)
        barrier = Barrier(2)

        def submit() -> Any:
            barrier.wait()
            return host.submit_rlm_workbench(envelope, request)

        with ThreadPoolExecutor(max_workers=2) as executor:
            records = tuple(executor.map(lambda _: submit(), range(2)))

        assert records[0].operation == records[1].operation
        handle = host.rlm_workbench.workspace_handle(records[0].operation)
        assert handle.generation == 1
        assert handle.revision == 0
        assert database_counts(database) == {
            "workbench_operations": (1,),
            "jobs": (1,),
            "dispatch": ("rlm.workbench.execute", "queued", 1),
        }
    finally:
        host.close()


def test_planner_cell_commits_revision_and_digest_bound_step_before_finalization(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = BlockingFinalizePlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        assert planner.cell_committed.wait(timeout=10)

        running = host.rlm_workbench.snapshot(record.operation)
        handle = host.rlm_workbench.workspace_handle(record.operation)
        inspected = host.program_workspace.inspect(handle)
        assert running.phase == "running"
        assert handle.revision == inspected.revision == 1
        assert any(item.name == "answer" for item in inspected.variables)

        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            cell = connection.execute(
                "SELECT * FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            manifest = connection.execute(
                "SELECT * FROM rlm_workbench_cell_manifests WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
        assert cell is not None and manifest is not None
        assert cell["state"] == "committed"
        assert cell["pre_workspace_revision"] == 0
        assert cell["post_workspace_revision"] == 1
        for field in (
            "source_digest",
            "result_digest",
            "pre_checkpoint_digest",
            "post_checkpoint_digest",
        ):
            assert str(cell[field]).startswith("sha256:")
            assert len(str(cell[field])) == 71
        assert str(manifest["manifest_digest"]).startswith("sha256:")

        planner.release_finalization.set()
        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(record.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(record.operation)
        assert terminal.state.value == "succeeded"
        assert snapshot.phase == "succeeded"
        assert snapshot.result.python_cells == 1
        assert snapshot.result.output.answer == "42"
        assert tuple(snapshot.result.output.artifacts) == ()
    finally:
        planner.release_finalization.set()
        host.close()


def test_worker_loss_after_committed_checkpoint_parks_without_cell_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = BlockingFinalizePlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    operation = None
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        operation = record.operation
        assert planner.cell_committed.wait(timeout=10)
        handle = host.rlm_workbench.workspace_handle(record.operation)
        backend = host.program_workspace
        state = backend._states[handle.workspace.value]  # type: ignore[attr-defined]
        state.exact_child.terminalize(5)
        planner.release_finalization.set()
        assert host.dispatcher is not None
        parked_outer = host.dispatcher.wait(record.operation, timeout_s=10)
        parked_inner = host.rlm_workbench.snapshot(record.operation)
        assert parked_outer.state.value == "indeterminate"
        assert parked_inner.phase == "parked"
        assert parked_inner.certainty == "indeterminate"
        assert parked_inner.failure.code == "RECONCILIATION_REQUIRED"
        assert parked_inner.failure.details[0].name == "operator.action"
    finally:
        planner.release_finalization.set()
        host.close()

    assert operation is not None
    reopened = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        enable_durable_dispatch=False,
    )
    try:
        snapshot = reopened.rlm_workbench.snapshot(operation)
        assert snapshot.phase == "parked"
        assert snapshot.failure.details[0].value == "reconcile-or-restore-checkpoint"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells "
                "WHERE operation_id = ? AND state = 'committed'",
                (operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT state FROM operation_dispatch WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() == ("parked",)
    finally:
        reopened.close()


def test_worker_broker_ticket_commits_once_then_resumes_cell(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = BrokerRoundTripPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)

        claimed = repository.claim(
            {
                **caller_common_input(database, request, pending, idempotency_key="broker-claim-1"),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert claimed.claimant is not None
        assert claimed.physical_attempt is not None
        started = repository.mark_send_started(
            {
                **caller_common_input(
                    database, request, claimed, idempotency_key="broker-send-start-1"
                ),
                "claim_id": claimed.claimant.claim_id,
                "claim_fence": claimed.claimant.claim_fence,
                "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": (claimed.claimant.claim_expires_at_unix_ms),
                "provider_or_child_idempotency_key": (
                    claimed.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": claimed.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        assert started.claimant is not None
        assert started.physical_attempt is not None
        evidence, model_response = model_commit_evidence(started, "ok")
        commit_command = {
            **caller_common_input(database, request, started, idempotency_key="broker-commit-1"),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": FIXED_NOW_MS + 2,
            "provider_or_child_request_id": "provider-request-broker-1",
            "observation": {
                "kind": "model",
                "outcome": "succeeded",
                "output_text": "ok",
                "output_digest": digest_bytes(b"ok"),
                **evidence,
            },
            "model_response": model_response,
        }
        committed = repository.commit(commit_command)
        replayed = repository.commit(copy.deepcopy(commit_command))
        assert replayed == committed

        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(record.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(record.operation)
        assert terminal.state.value == "succeeded"
        assert snapshot.phase == "succeeded"
        assert snapshot.result.output.answer == "ok"
        assert planner.calls == 2

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts",
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_command_receipts",
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT COUNT(*) FROM broker_calls WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(DISTINCT physical_attempt_id) "
                "FROM caller_work_tickets WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT state FROM broker_calls WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("succeeded",)
            assert connection.execute(
                "SELECT state, consumption_kind FROM rlm_workbench_rebind_transfers "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("consumed", "worker_ack")
            assert connection.execute(
                "SELECT state, rebind_generation FROM rlm_workbench_successor_outbox "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("consumed", 1)
            assert connection.execute(
                "SELECT authority_generation, rebind_token_digest IS NOT NULL "
                "FROM rlm_workbench_attempt_authority WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (2, 1)
            assert connection.execute(
                "SELECT state FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("committed",)
            assert connection.execute(
                "SELECT state FROM operation_attempts WHERE operation_id = ? ORDER BY attempt_no",
                (record.operation.value,),
            ).fetchall() == [("suspended_external",), ("succeeded",)]
    finally:
        host.close()


def test_ticket_insert_failure_rolls_back_suspension_and_parks(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TRIGGER fail_caller_ticket_insert
            BEFORE INSERT ON caller_work_tickets
            BEGIN
                SELECT RAISE(ABORT, 'forced caller ticket insert failure');
            END;
            """
        )
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=BrokerRoundTripPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(record.operation, timeout_s=60)
        snapshot = host.rlm_workbench.snapshot(record.operation)
        assert terminal.state.value == "indeterminate"
        assert snapshot.phase == "parked"

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_suspensions WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT state FROM broker_calls WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("started",)
    finally:
        host.close()


def test_caller_wait_releases_attempt_lease_and_projects_accepted(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=BrokerRoundTripPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        ticket_id = wait_for_pending_ticket(database)
        assert host.caller_work is not None
        pending = host.caller_work.get(ticket_id)
        assert pending.state == "pending"

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state, certainty FROM operations WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("accepted", "certain")
            assert connection.execute(
                "SELECT phase FROM rlm_workbench_jobs WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("waiting_external",)
            assert connection.execute(
                "SELECT state, certainty FROM operation_attempts "
                "WHERE operation_id = ? AND attempt_no = 1",
                (record.operation.value,),
            ).fetchone() == ("suspended_external", "certain")
            assert connection.execute(
                "SELECT released_at_unix_ms IS NOT NULL FROM operation_leases "
                "WHERE operation_id = ? AND attempt_no = 1",
                (record.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT state, current_attempt_no FROM operation_dispatch WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("parked", 1)
            assert connection.execute(
                """
                SELECT c.state,
                       a.attempt_id = c.attempt_id,
                       a.attempt_fence = c.attempt_fence,
                       catalog.manifest_json IS NOT NULL
                FROM rlm_workbench_cells AS c
                JOIN rlm_workbench_attempt_authority AS a
                  ON a.operation_id = c.operation_id
                JOIN workspace_checkpoint_catalog AS catalog
                  ON catalog.manifest_digest = c.pre_checkpoint_digest
                WHERE c.operation_id = ?
                """,
                (record.operation.value,),
            ).fetchone() == ("suspended", 1, 1, 1)
    finally:
        host.close()


@pytest.mark.parametrize(
    ("barrier_name", "expected_barrier_hits"),
    (
        (
            "rebind_prepared_before_commit",
            ("rebind_prepared_before_commit",),
        ),
        (
            "rebind_committed_before_delivery",
            (
                "rebind_prepared_before_commit",
                "rebind_committed_before_delivery",
            ),
        ),
        (
            "rebind_delivered_before_ack",
            (
                "rebind_prepared_before_commit",
                "rebind_committed_before_delivery",
                "rebind_delivered_before_ack",
            ),
        ),
        (
            "rebind_acked_before_cell_commit",
            (
                "rebind_prepared_before_commit",
                "rebind_committed_before_delivery",
                "rebind_delivered_before_ack",
                "rebind_acked_before_cell_commit",
            ),
        ),
    ),
)
def test_rebind_replays_after_coordinator_crash(
    tmp_path: Path,
    barrier_name: str,
    expected_barrier_hits: tuple[str, ...],
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = BrokerRoundTripPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, planner),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(
            workbench_envelope(host, request),
            request,
        )
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)

        dispatcher = host.dispatcher
        assert dispatcher is not None
        dispatcher.close()
        host.dispatcher = None

        claimed = repository.claim(
            {
                **caller_common_input(
                    database,
                    request,
                    pending,
                    idempotency_key="restart-broker-claim-1",
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert claimed.claimant is not None
        assert claimed.physical_attempt is not None
        started = repository.mark_send_started(
            {
                **caller_common_input(
                    database,
                    request,
                    claimed,
                    idempotency_key="restart-broker-send-start-1",
                ),
                "claim_id": claimed.claimant.claim_id,
                "claim_fence": claimed.claimant.claim_fence,
                "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": claimed.claimant.claim_expires_at_unix_ms,
                "provider_or_child_idempotency_key": (
                    claimed.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": claimed.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        assert started.claimant is not None
        assert started.physical_attempt is not None
        evidence, model_response = model_commit_evidence(started, "ok")
        committed = repository.commit(
            {
                **caller_common_input(
                    database,
                    request,
                    started,
                    idempotency_key="restart-broker-commit-1",
                ),
                "claim_id": started.claimant.claim_id,
                "claim_fence": started.claimant.claim_fence,
                "physical_attempt_id": started.physical_attempt.physical_attempt_id,
                "sent_request_digest": started.request_digest,
                "sent_at_unix_ms": FIXED_NOW_MS + 2,
                "provider_or_child_request_id": "provider-request-restart-1",
                "observation": {
                    "kind": "model",
                    "outcome": "succeeded",
                    "output_text": "ok",
                    "output_digest": digest_bytes(b"ok"),
                    **evidence,
                },
                "model_response": model_response,
            }
        )
        assert committed.state == "settled_success"

        original = host.rlm_workbench
        assert original is not None
        original.close()
        barrier_hits: list[str] = []

        def crash_after_commit(name: str) -> None:
            barrier_hits.append(name)
            if name == barrier_name:
                raise RuntimeError(f"simulated coordinator crash at {name}")

        crashing = RlmWorkbenchCoordinator(
            database,
            cast(Any, host.program_workspace),
            repository,
            host.registry,
            now_ms=lambda: FIXED_NOW_MS,
            planner=cast(Any, planner),
            barrier=crash_after_commit,
        )
        host.rlm_workbench = crashing
        owner_digest = canonical_sha256("restart-successor-owner")
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=owner_digest,
            lease_duration_ms=30_000,
        )
        assert claim is not None
        assert claim.kind == "rlm.workbench.execute"
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=claim.owner_digest,
        )
        with pytest.raises(
            RuntimeError,
            match="simulated coordinator crash at rebind_",
        ):
            crashing.run_claimed(record.operation, claim.attempt, fence)
        assert tuple(barrier_hits) == expected_barrier_hits

        with sqlite3.connect(database) as connection:
            if barrier_name == "rebind_prepared_before_commit":
                expected_transfer = ("prepared", None)
                expected_outbox = ("prepared",)
                expected_cell = ("suspended",)
            elif barrier_name == "rebind_acked_before_cell_commit":
                expected_transfer = ("consumed", "worker_ack")
                expected_outbox = ("consumed",)
                expected_cell = ("running",)
            else:
                expected_transfer = ("committed", None)
                expected_outbox = ("consumed",)
                expected_cell = ("running",)
            assert (
                connection.execute(
                    "SELECT state, consumption_kind FROM rlm_workbench_rebind_transfers "
                    "WHERE operation_id = ?",
                    (record.operation.value,),
                ).fetchone()
                == expected_transfer
            )
            assert (
                connection.execute(
                    "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                    (record.operation.value,),
                ).fetchone()
                == expected_outbox
            )
            assert (
                connection.execute(
                    "SELECT state FROM rlm_workbench_cells WHERE operation_id = ?",
                    (record.operation.value,),
                ).fetchone()
                == expected_cell
            )
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cell_manifests WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts",
            ).fetchone() == (1,)

        crashing.close()
        recovered = RlmWorkbenchCoordinator(
            database,
            cast(Any, host.program_workspace),
            repository,
            host.registry,
            now_ms=lambda: FIXED_NOW_MS,
            planner=cast(Any, planner),
        )
        host.rlm_workbench = recovered
        result = recovered.run_claimed(record.operation, claim.attempt, fence)
        assert result.output.answer == "ok"
        terminal = recovered.snapshot(record.operation)
        assert terminal.phase == "succeeded"
        assert planner.calls == 2

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state, consumption_kind FROM rlm_workbench_rebind_transfers "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("consumed", "worker_ack")
            assert connection.execute(
                "SELECT state FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("committed",)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (2,)
            assert connection.execute(
                "SELECT COUNT(*) FROM broker_calls WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
    finally:
        host.close()


@pytest.mark.parametrize(
    ("barrier_name", "expected_transfer"),
    (
        ("rebind_prepared_before_commit", ("aborted", None)),
        ("rebind_committed_before_delivery", ("consumed", "recovery_fenced_loss")),
    ),
)
def test_rebind_worker_loss_restores_checkpoint_and_runs_recovery_cell(
    tmp_path: Path,
    barrier_name: str,
    expected_transfer: tuple[str, str | None],
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = RecoveryBrokerRoundTripPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, planner),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    host_closed = False
    restarted: ReferenceHost | None = None
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)

        dispatcher = host.dispatcher
        assert dispatcher is not None
        dispatcher.close()
        host.dispatcher = None
        committed = commit_pending_ticket_success(
            repository,
            database,
            request,
            pending,
            prefix=f"worker-loss-{barrier_name}",
        )
        assert committed.state == "settled_success"

        original = host.rlm_workbench
        assert original is not None
        original.close()

        def lose_worker_at_rebind_barrier(name: str) -> None:
            if name == barrier_name:
                raise RuntimeError("simulated supervisor loss during rebind")

        crashing = RlmWorkbenchCoordinator(
            database,
            cast(Any, host.program_workspace),
            repository,
            host.registry,
            now_ms=lambda: FIXED_NOW_MS,
            planner=cast(Any, planner),
            barrier=lose_worker_at_rebind_barrier,
        )
        host.rlm_workbench = crashing
        owner_digest = canonical_sha256("worker-loss-successor-owner")
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=owner_digest,
            lease_duration_ms=30_000,
        )
        assert claim is not None
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=claim.owner_digest,
        )
        with pytest.raises(RuntimeError, match="simulated supervisor loss"):
            crashing.run_claimed(record.operation, claim.attempt, fence)

        host.close()
        host_closed = True
        restarted = ReferenceHost(
            database,
            now_ms=lambda: FIXED_NOW_MS,
            workbench_planner=cast(Any, planner),
            enable_durable_dispatch=False,
            dispatcher_concurrency=1,
        )
        assert restarted.status(record.operation).state.value == "indeterminate"
        restarted.recover_durable()
        assert restarted.status(record.operation).state.value == "accepted"

        recovery_dispatcher = restarted.start_durable_dispatch()
        terminal = recovery_dispatcher.wait(record.operation, timeout_s=90)
        assert terminal.state.value == "succeeded"
        coordinator = restarted.rlm_workbench
        assert coordinator is not None
        result = coordinator.snapshot(record.operation)
        assert result.phase == "succeeded"
        assert result.result.output.answer == "ok"
        assert planner.calls == 2
        assert len(planner.recovery_inputs) == 1
        recovery_input = planner.recovery_inputs[0].root
        assert recovery_input["failure_code"] == "supervisor_restarted"
        assert len(recovery_input["settled_ticket_receipt_digests"]) == 1

        with sqlite3.connect(database) as connection:
            assert (
                connection.execute(
                    "SELECT state, consumption_kind FROM rlm_workbench_rebind_transfers "
                    "WHERE operation_id = ?",
                    (record.operation.value,),
                ).fetchone()
                == expected_transfer
            )
            assert connection.execute(
                "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("consumed",)
            assert connection.execute(
                "SELECT state FROM rlm_workbench_cells WHERE operation_id = ? ORDER BY cell_index",
                (record.operation.value,),
            ).fetchall() == [("lost_before_commit",), ("committed",)]
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (3,)
            assert connection.execute(
                "SELECT workspace_generation FROM rlm_workbench_jobs WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (2,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts",
            ).fetchone() == (1,)
    finally:
        if restarted is not None:
            restarted.close()
        if not host_closed:
            host.close()


def test_cancel_after_ticket_settlement_before_successor_claim_is_terminal(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = BrokerRoundTripPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, planner),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        dispatcher = host.dispatcher
        coordinator = host.rlm_workbench
        assert repository is not None and dispatcher is not None and coordinator is not None
        pending = repository.get(ticket_id)

        dispatcher.close()
        host.dispatcher = None
        committed = commit_pending_ticket_success(
            repository,
            database,
            request,
            pending,
            prefix="cancel-after-settlement",
        )
        assert committed.state == "settled_success"

        cancelled = host.cancel(record.operation, reason_code="user_requested")
        assert cancelled.state.value == "cancelled"
        assert coordinator.snapshot(record.operation).phase == "cancelled"

        coordinator.schedule_pending_successors()
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT phase, cancellation_requested FROM rlm_workbench_jobs "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("cancelled", 1)
            assert connection.execute(
                "SELECT state FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("cancelled",)
            assert connection.execute(
                "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("pending",)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_rebind_transfers WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
    finally:
        host.close()


def test_cancel_waiting_workbench_before_send_settles_ticket_without_successor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        coordinator = host.rlm_workbench
        assert repository is not None and coordinator is not None

        cancelled = host.cancel(record.operation, reason_code="user_requested")
        assert cancelled.state.value == "cancelled"
        assert repository.get(ticket_id).state == "cancelled_before_send"
        assert coordinator.snapshot(record.operation).phase == "cancelled"

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM rlm_workbench_suspensions WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("cancelled",)
            assert connection.execute(
                "SELECT state FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == ("cancelled",)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT physical_attempt_id FROM caller_work_tickets WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone() == (None,)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (1,)
    finally:
        host.close()


def test_quiet_restart_past_deadline_cancels_pending_ticket_without_send(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    now = [FIXED_NOW_MS]
    original = ReferenceHost(
        database,
        now_ms=lambda: now[0],
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    operation = None
    ticket_id = None
    deadline = None
    try:
        request = workbench_request(original, require_named_artifacts=False)
        record = original.submit_rlm_workbench(workbench_envelope(original, request), request)
        operation = record.operation
        ticket_id = wait_for_pending_ticket(database)
        assert original.caller_work is not None
        deadline = original.caller_work.get(ticket_id).deadline_unix_ms
    finally:
        original.close()

    assert operation is not None and ticket_id is not None and deadline is not None
    now[0] = deadline
    reopened = ReferenceHost(
        database,
        now_ms=lambda: now[0],
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        assert reopened.caller_work is not None
        assert reopened.rlm_workbench is not None
        ticket = reopened.caller_work.get(ticket_id)
        assert ticket.state == "cancelled_before_send"
        assert ticket.physical_attempt is None
        assert reopened.caller_work.send_started_count(ticket_id) == 0
        assert reopened.status(operation).state.value == "timed_out"
        assert reopened.rlm_workbench.snapshot(operation).phase == "timed_out"
    finally:
        reopened.close()


def test_quiet_restart_past_deadline_keeps_may_have_sent_outcome_unknown(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    now = [FIXED_NOW_MS]
    original = ReferenceHost(
        database,
        now_ms=lambda: now[0],
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    operation = None
    ticket_id = None
    deadline = None
    attempts_before_restart = None
    try:
        request = workbench_request(original, require_named_artifacts=False)
        record = original.submit_rlm_workbench(workbench_envelope(original, request), request)
        operation = record.operation
        ticket_id = wait_for_pending_ticket(database)
        assert original.caller_work is not None
        pending = original.caller_work.get(ticket_id)
        reserved = original.caller_work.claim(
            {
                **caller_common_input(
                    database,
                    request,
                    pending,
                    idempotency_key="deadline-send-claim",
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert reserved.claimant is not None and reserved.physical_attempt is not None
        started = original.caller_work.mark_send_started(
            {
                **caller_common_input(
                    database,
                    request,
                    reserved,
                    idempotency_key="deadline-send-start",
                ),
                "claim_id": reserved.claimant.claim_id,
                "claim_fence": reserved.claimant.claim_fence,
                "physical_attempt_id": reserved.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": (reserved.claimant.claim_expires_at_unix_ms),
                "provider_or_child_idempotency_key": (
                    reserved.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": reserved.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        deadline = started.deadline_unix_ms
        attempts_before_restart = original.registry._connection.execute(
            "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()[0]
    finally:
        original.close()

    assert (
        operation is not None
        and ticket_id is not None
        and deadline is not None
        and attempts_before_restart is not None
    )
    now[0] = deadline
    reopened = ReferenceHost(
        database,
        now_ms=lambda: now[0],
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        assert reopened.caller_work is not None
        assert reopened.dispatcher is not None
        terminal = reopened.dispatcher.wait(operation, timeout_s=10)
        ticket = reopened.caller_work.get(ticket_id)
        assert ticket.state == "outcome_unknown"
        assert reopened.caller_work.send_started_count(ticket_id) == 1
        assert terminal.state.value == "accepted"
        assert reopened.rlm_workbench is not None
        snapshot = reopened.rlm_workbench.snapshot(operation)
        assert snapshot.phase == "waiting_external"
        assert snapshot.failure is None
        assert (
            reopened.registry._connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox "
                "WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()[0]
            == 0
        )
        assert (
            reopened.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()[0]
            == attempts_before_restart
        )

        from aar.runtime.caller_work import build_reconcile_fence

        late_evidence, late_model_response = model_commit_evidence(started, "ok")
        late_receipt = caller_success_candidate_receipt(
            started,
            prefix="deadline-late-receipt",
            model_evidence=late_evidence,
        )
        reopened.caller_work.append_model_candidate_receipt(
            late_receipt,
            late_model_response,
        )
        settled = reopened.caller_work.settle_candidate(
            ticket_id=ticket_id,
            expected_revision=ticket.revision,
            candidate_receipt_digest=late_receipt["receipt_digest"],
            reconciler_id="fixture-adapter",
            reconciler_generation=1,
            reconcile_fence=build_reconcile_fence(
                ticket_id=ticket_id,
                expected_revision=ticket.revision,
                physical_attempt_id=started.physical_attempt.physical_attempt_id,
                candidate_receipt_digest=late_receipt["receipt_digest"],
                reconciler_id="fixture-adapter",
                reconciler_generation=1,
                reconciliation_action="settle",
            ),
            idempotency_key="deadline-late-settlement",
        )
        assert settled.state == "settled_success"
        assert reopened.caller_work.send_started_count(ticket_id) == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts WHERE ticket_id = ?",
                (ticket_id,),
            ).fetchone() == (1,)
    finally:
        reopened.close()


def test_worker_loss_after_send_started_raw_model_lookup_remains_unknown_without_redispatch(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkDispatcher

    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    original = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    operation = None
    request = None
    started = None
    try:
        request = workbench_request(original, require_named_artifacts=False)
        record = original.submit_rlm_workbench(
            workbench_envelope(original, request),
            request,
        )
        operation = record.operation
        ticket_id = wait_for_pending_ticket(database)
        repository = original.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)
        claimed = repository.claim(
            {
                **caller_common_input(
                    database,
                    request,
                    pending,
                    idempotency_key="worker-loss-send-claim",
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert claimed.claimant is not None and claimed.physical_attempt is not None
        started = repository.mark_send_started(
            {
                **caller_common_input(
                    database,
                    request,
                    claimed,
                    idempotency_key="worker-loss-send-start",
                ),
                "claim_id": claimed.claimant.claim_id,
                "claim_fence": claimed.claimant.claim_fence,
                "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": (claimed.claimant.claim_expires_at_unix_ms),
                "provider_or_child_idempotency_key": (
                    claimed.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": claimed.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        assert started.state == "send_started"
    finally:
        original.close()

    assert operation is not None and request is not None and started is not None
    late_receipt = caller_success_candidate_receipt(started, prefix="worker-loss-send")
    reopened = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )

    class LookupOnlyAdapter:
        def __init__(self) -> None:
            self.lookup_calls = 0
            self.send_calls = 0

        def lookup(self, *, idempotency_key: str) -> dict[str, Any]:
            del idempotency_key
            self.lookup_calls += 1
            return copy.deepcopy(late_receipt)

        def send(self, *, idempotency_key: str, request: object) -> dict[str, Any]:
            del idempotency_key, request
            self.send_calls += 1
            raise AssertionError("restart must not physically redispatch caller work")

    adapter = LookupOnlyAdapter()
    assert reopened.caller_work is not None
    caller_dispatcher = CallerWorkDispatcher(reopened.caller_work, adapter)
    try:
        assert reopened.caller_work.get(started.ticket_id).state == "send_started"
        assert reopened.rlm_workbench is not None
        assert reopened.rlm_workbench.snapshot(operation).phase == "waiting_external"
        with sqlite3.connect(database) as connection:
            attempts_before_lookup = connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()[0]

        unresolved = caller_dispatcher.resume(started.ticket_id)
        assert unresolved.state == "outcome_unknown"
        assert adapter.lookup_calls == 1
        assert adapter.send_calls == 0
        assert reopened.rlm_workbench.snapshot(operation).phase == "waiting_external"

        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM model_executions WHERE operation_id = ? "
                "AND state = 'result_committed'",
                (operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts WHERE ticket_id = ?",
                (started.ticket_id,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (operation.value,),
            ).fetchone() == (attempts_before_lookup,)
    finally:
        caller_dispatcher.close()
        reopened.close()


def test_planner_deadline_overrun_terminalizes_once_without_new_work(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    clock = [FIXED_NOW_MS]
    host = ReferenceHost(
        database,
        now_ms=lambda: clock[0],
        workbench_planner=cast(Any, DeadlineCrossingPlanner(clock)),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            terminal = host.registry.get(admitted.operation)
            if terminal.state.value == "timed_out":
                break
            time.sleep(0.01)
        else:
            raise TimeoutError("deadline overrun did not terminalize the outer operation")

        assert host.rlm_workbench is not None
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "timed_out"
        assert snapshot.failure.code == "DEADLINE_EXCEEDED"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (0,)
    finally:
        host.close()


def test_late_settlement_after_restart_consumes_successor_into_deadline(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    clock = [FIXED_NOW_MS]
    host: ReferenceHost | None = ReferenceHost(
        database,
        now_ms=lambda: clock[0],
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    reopened: ReferenceHost | None = None
    detached: CallerWorkRepository | None = None
    detached_journal: ModelExecutionJournal | None = None
    try:
        assert host is not None
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)
        reserved = repository.claim(
            {
                **caller_common_input(
                    database,
                    request,
                    pending,
                    idempotency_key="late-deadline-claim",
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert reserved.claimant is not None and reserved.physical_attempt is not None
        started = repository.mark_send_started(
            {
                **caller_common_input(
                    database,
                    request,
                    reserved,
                    idempotency_key="late-deadline-send-start",
                ),
                "claim_id": reserved.claimant.claim_id,
                "claim_fence": reserved.claimant.claim_fence,
                "physical_attempt_id": reserved.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": reserved.claimant.claim_expires_at_unix_ms,
                "provider_or_child_idempotency_key": (
                    reserved.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": reserved.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        physical = started.physical_attempt
        assert physical is not None
        evidence, model_response = model_commit_evidence(started, "ok")
        receipt = caller_success_candidate_receipt(
            started,
            prefix="late-deadline",
            model_evidence=evidence,
        )
        repository.append_model_candidate_receipt(receipt, model_response)
        host.close()
        host = None
        clock[0] = int(request.root["context"]["deadline_unix_ms"])
        detached_journal = ModelExecutionJournal(database)
        detached = CallerWorkRepository(
            database,
            now_ms=lambda: clock[0],
            model_executions=detached_journal,
        )
        settled = detached.settle_candidate(
            ticket_id=ticket_id,
            expected_revision=started.revision,
            candidate_receipt_digest=receipt["receipt_digest"],
            reconciler_id="fixture-adapter",
            reconciler_generation=1,
            reconcile_fence=build_reconcile_fence(
                ticket_id=ticket_id,
                expected_revision=started.revision,
                physical_attempt_id=physical.physical_attempt_id,
                candidate_receipt_digest=receipt["receipt_digest"],
                reconciler_id="fixture-adapter",
                reconciler_generation=1,
                reconciliation_action="settle",
            ),
            idempotency_key="late-deadline-settlement",
        )
        assert settled.state == "settled_success"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == ("pending",)
        detached.close()
        detached = None
        detached_journal.close()
        detached_journal = None

        reopened = ReferenceHost(
            database,
            now_ms=lambda: clock[0],
            workbench_planner=cast(Any, BrokerRoundTripPlanner()),
            enable_durable_dispatch=True,
            dispatcher_concurrency=1,
        )
        assert reopened.dispatcher is not None
        terminal = reopened.dispatcher.wait(admitted.operation, timeout_s=60)
        assert terminal.state.value == "timed_out"
        assert reopened.rlm_workbench is not None
        snapshot = reopened.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "timed_out"
        assert snapshot.failure.code == "DEADLINE_EXCEEDED"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == ("consumed",)
            terminal_event = connection.execute(
                "SELECT payload_json FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_successor_terminal'",
                (admitted.operation.value,),
            ).fetchone()
            assert terminal_event is not None
            terminal_payload = json.loads(terminal_event[0])
            assert terminal_payload["terminal_state"] == "timed_out"
            assert terminal_payload["settlement_digest"] == receipt["receipt_digest"]
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (0,)
    finally:
        if detached is not None:
            detached.close()
        if detached_journal is not None:
            detached_journal.close()
        if reopened is not None:
            reopened.close()
        if host is not None:
            host.close()


def test_settlement_directly_notifies_live_dispatcher_callback(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=cast(Any, BrokerRoundTripPlanner()),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        coordinator = host.rlm_workbench
        assert repository is not None and coordinator is not None
        pending = repository.get(ticket_id)
        dispatcher = host.dispatcher
        assert dispatcher is not None
        dispatcher.close()
        host.dispatcher = None
        notifications: list[tuple[Any, str]] = []
        coordinator.bind_runtime_callbacks(
            dispatch_notifier=lambda operation, kind: notifications.append((operation, kind)),
            deadline_terminalizer=host._terminalize_waiting_workbench_deadline,
        )

        commit_pending_ticket_success(
            repository,
            database,
            request,
            pending,
            prefix="direct-notify",
        )
        assert notifications == [(admitted.operation, "rlm.workbench.execute")]
    finally:
        host.close()
