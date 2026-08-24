import json
from pathlib import Path

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    caller_common_input,
    digest_bytes,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.reference_host as reference_host_module
from aar.broker_models import (
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_sha256
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.ipython_backend import WorkspaceWorkerBinding
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import InvalidTransition, PlannerOwner, StaleAttemptFence
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceBackendDescriptor,
    WorkspaceCheckpointManifest,
    WorkspaceEnvironmentFingerprint,
)
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


class _FakeIPythonWorkspaceBackend:
    descriptor = WorkspaceBackendDescriptor.issue(
        kind="ipython",
        version="test-ipython",
        checkpoint_formats=("aar.workspace-checkpoint.v1",),
        features=(),
    )
    environment = WorkspaceEnvironmentFingerprint.current()

    def __init__(self, *, artifact_sink: object, worker_manager: object) -> None:
        del artifact_sink, worker_manager

    def create(self, workspace: WorkspaceRef, session: SessionRef) -> ProgrammableWorkspaceHandle:
        del session
        return ProgrammableWorkspaceHandle(
            workspace=workspace,
            backend=self.descriptor,
            generation=1,
            revision=0,
        )

    def attach(
        self, handle: ProgrammableWorkspaceHandle, session: SessionRef
    ) -> ProgrammableWorkspaceHandle:
        del session
        return handle

    def bind_broker_handler(self, handler: object) -> None:
        del handler

    def checkpoint(
        self,
        operation: OperationRef,
        handle: ProgrammableWorkspaceHandle,
        _policy: object,
        *,
        trace_id: str,
    ) -> WorkspaceCheckpointManifest:
        return WorkspaceCheckpointManifest.issue(
            source_handle=handle,
            creation_operation=operation,
            trace_id=trace_id,
            environment=self.environment,
            values=(),
            exclusions=(),
        )

    def worker_binding(
        self, _handle: ProgrammableWorkspaceHandle
    ) -> WorkspaceWorkerBinding:
        return WorkspaceWorkerBinding(
            owner_generation=1,
            process_identity_digest="sha256:" + "9" * 64,
        )

    def restore(
        self, manifest: WorkspaceCheckpointManifest, _spec: object
    ) -> ProgrammableWorkspaceHandle:
        return manifest.source_handle

    def execute(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("crash discriminator must stop before backend execution")

    def shutdown(self) -> None:
        return None


class _FailIfCalledPlanner:
    calls = 0

    def plan(self, *_args: object, **_kwargs: object) -> object:
        self.calls += 1
        raise AssertionError("caller-delegated root planner must not run synchronously")


def _model_commit_evidence(
    ticket: object,
    output_text: str,
) -> tuple[dict[str, str], dict[str, object]]:
    root = ticket.root  # type: ignore[attr-defined]
    request_document = dict(root["request"])
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
        provider_response_id="provider-planner-1",
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


def test_planner_owner_uses_exact_wire_identity_without_ordinal_alias() -> None:
    owner = PlannerOwner(phase="initial", step_index=0)

    assert owner.as_wire() == {"kind": "planner", "phase": "initial", "step_index": 0}
    assert "ordinal" not in owner.as_wire()


@pytest.mark.parametrize(
    ("phase", "step_index"),
    (
        ("unexpected", 0),
        ("initial", True),
        ("initial", 1.0),
        ("initial", -1),
    ),
)
def test_planner_owner_rejects_non_frozen_or_coerced_values(
    phase: object,
    step_index: object,
) -> None:
    with pytest.raises(ValueError):
        PlannerOwner(phase=phase, step_index=step_index)  # type: ignore[arg-type]


def test_caller_delegated_waiting_releases_attempt_and_never_projects_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    planner = _FailIfCalledPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
        )
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        snapshot = host.rlm_workbench.snapshot(record.operation)
        stored = host.registry.get(record.operation)
        with host.registry._connection:
            suspension = host.registry._connection.execute(
                "SELECT cell_execution_id, logical_owner_json FROM rlm_workbench_suspensions "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            attempt = host.registry._connection.execute(
                "SELECT state FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            lease = host.registry._connection.execute(
                "SELECT released_at_unix_ms FROM operation_leases WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
        assert planner.calls == 0
        assert ticket_id.startswith("planner-")
        assert snapshot.phase == "waiting_external"
        assert stored.state.value == "accepted"
        assert suspension is not None and suspension["cell_execution_id"] is None
        assert suspension["logical_owner_json"] == (
            '{"kind":"planner","phase":"initial","step_index":0}'
        )
        assert attempt is not None and attempt["state"] == "suspended_external"
        assert lease is not None and lease["released_at_unix_ms"] is not None
    finally:
        host.close()


def test_caller_delegated_start_only_false_rejects_before_operation_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
        )
        envelope = workbench_envelope(host, request)
        before = tuple(
            host.registry._connection.execute(
                "SELECT operation_id FROM operations ORDER BY operation_id"
            )
        )
        invalid = dict(request.root)
        invalid["start_only"] = False
        with pytest.raises(ValueError, match="start_only"):
            host.submit_rlm_workbench(envelope, invalid)
        after = tuple(
            host.registry._connection.execute(
                "SELECT operation_id FROM operations ORDER BY operation_id"
            )
        )
        assert after == before
    finally:
        host.close()


@pytest.mark.parametrize("journal_tamper", ("route", "usage", "host", "outcome"))
def test_planner_successor_rejects_forged_model_journal_lineage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    journal_tamper: str,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=_FailIfCalledPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
        )
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        assert host.dispatcher is not None and host.caller_work is not None
        host.dispatcher.close()
        host.dispatcher = None
        pending = host.caller_work.get(ticket_id)
        claimed = host.caller_work.claim(
            {
                **caller_common_input(
                    database, request, pending, idempotency_key="journal-claim"
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert claimed.claimant is not None and claimed.physical_attempt is not None
        started = host.caller_work.mark_send_started(
            {
                **caller_common_input(
                    database, request, claimed, idempotency_key="journal-send"
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
        assert started.claimant is not None and started.physical_attempt is not None
        output = {
            "kind": "finalize",
            "output": {"answer": "journal", "artifacts": []},
            "artifact_stage_ids": [],
        }
        output_text = json.dumps(output, sort_keys=True, separators=(",", ":"))
        evidence, model_response = _model_commit_evidence(started, output_text)
        host.caller_work.commit(
            {
                **caller_common_input(
                    database, request, started, idempotency_key="journal-commit"
                ),
                "claim_id": started.claimant.claim_id,
                "claim_fence": started.claimant.claim_fence,
                "physical_attempt_id": started.physical_attempt.physical_attempt_id,
                "sent_request_digest": started.request_digest,
                "sent_at_unix_ms": FIXED_NOW_MS + 2,
                "provider_or_child_request_id": "provider-journal",
                "observation": {
                    "kind": "model",
                    "outcome": "succeeded",
                    "output_text": output_text,
                    "output_digest": digest_bytes(output_text.encode()),
                    **evidence,
                },
                "model_response": model_response,
            }
        )
        execution_key = started.physical_attempt.provider_or_child_idempotency_key
        with host.registry._connection:
            if journal_tamper == "route":
                host.registry._connection.execute(
                    "UPDATE model_executions SET response_json = "
                    "json_set(response_json, '$.route_receipt.receipt_digest', ?) "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    ("sha256:" + "0" * 64, record.operation.value, execution_key),
                )
            elif journal_tamper == "usage":
                host.registry._connection.execute(
                    "UPDATE model_executions SET usage_json = "
                    "json_set(usage_json, '$.input_tokens', 8, '$.total_tokens', 11) "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    (record.operation.value, execution_key),
                )
            elif journal_tamper == "host":
                host.registry._connection.execute(
                    "UPDATE model_executions SET response_digest = ? "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    ("sha256:" + "0" * 64, record.operation.value, execution_key),
                )
            else:
                host.registry._connection.execute(
                    "UPDATE model_executions SET state = 'failed', response_digest = NULL, "
                    "response_json = NULL, usage_json = NULL, failure_code = 'provider_error' "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    (record.operation.value, execution_key),
                )
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=canonical_sha256("journal-successor-owner"),
            lease_duration_ms=30_000,
        )
        assert claim is not None and host.rlm_workbench is not None
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=claim.owner_digest,
        )
        with pytest.raises(InvalidTransition, match="model journal"):
            host.rlm_workbench._consume_root_planner(claim.attempt, fence)
        assert host.registry._connection.execute(
            "SELECT state FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()[0] == "prepared"
        assert host.registry._connection.execute(
            "SELECT phase FROM rlm_workbench_jobs WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()[0] == "accepted"
        assert host.registry._connection.execute(
            "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
            "AND event_kind = 'planner_successor_consumed'",
            (record.operation.value,),
        ).fetchone()[0] == 0
    finally:
        host.close()


@pytest.mark.parametrize(
    (
        "planner_output",
        "candidate_outcome",
        "restart_before_consume",
        "control_override",
        "max_model_calls",
        "expected_phase",
    ),
    (
        (
            {
                "kind": "finalize",
                "output": {"answer": "ok", "artifacts": []},
                "artifact_stage_ids": [],
            },
            "succeeded",
            False,
            None,
            40,
            "running",
        ),
        ("not-json", "succeeded", False, None, 40, "corrected_running"),
        ("not-json", "succeeded", False, None, 1, "failed"),
        (
            {
                "kind": "finalize",
                "output": {"answer": "restarted", "artifacts": []},
                "artifact_stage_ids": [],
            },
            "succeeded",
            True,
            None,
            40,
            "running",
        ),
        (None, "failed_certain", False, None, 40, "failed"),
        (
            {
                "kind": "finalize",
                "output": {"answer": "late", "artifacts": []},
                "artifact_stage_ids": [],
            },
            "succeeded",
            False,
            "cancel",
            40,
            "cancelled",
        ),
        (
            {
                "kind": "finalize",
                "output": {"answer": "late", "artifacts": []},
                "artifact_stage_ids": [],
            },
            "succeeded",
            False,
            "deadline",
            40,
            "timed_out",
        ),
    ),
)
def test_settled_planner_directive_consumes_exact_prepared_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    planner_output: object,
    candidate_outcome: str,
    restart_before_consume: bool,
    control_override: str | None,
    max_model_calls: int,
    expected_phase: str,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    clock = [FIXED_NOW_MS]
    host = ReferenceHost(
        database,
        now_ms=lambda: clock[0],
        workbench_planner=_FailIfCalledPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
            max_model_calls=max_model_calls,
        )
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        repository = host.caller_work
        dispatcher = host.dispatcher
        coordinator = host.rlm_workbench
        assert repository is not None and dispatcher is not None and coordinator is not None
        pending = repository.get(ticket_id)
        planner_request = dict(pending.root["request"])
        planner_prompt = json.loads(str(planner_request["prompt"]))
        assert planner_request["route_binding"] == request.root["spec"]["model"][
            "route_binding"
        ]
        assert planner_prompt["planner_owner"] == {
            "kind": "planner",
            "phase": "initial",
            "step_index": 0,
        }
        assert planner_prompt["workbench_snapshot"]["phase"] == "running"
        assert planner_prompt["workbench_snapshot"]["workspace"] is not None
        assert planner_prompt["last_committed_cell"] is None
        dispatcher.close()
        host.dispatcher = None
        claimed = repository.claim(
            {
                **caller_common_input(
                    database, request, pending, idempotency_key="planner-claim"
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
                    database, request, claimed, idempotency_key="planner-send-start"
                ),
                "claim_id": claimed.claimant.claim_id,
                "claim_fence": claimed.claimant.claim_fence,
                "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
                "expected_claim_expires_at_unix_ms": (
                    claimed.claimant.claim_expires_at_unix_ms
                ),
                "provider_or_child_idempotency_key": (
                    claimed.physical_attempt.provider_or_child_idempotency_key
                ),
                "sent_request_digest": claimed.request_digest,
                "lookup_supported": True,
                "cancel_supported": True,
            }
        )
        assert started.claimant is not None and started.physical_attempt is not None
        output_text = (
            json.dumps(planner_output, sort_keys=True, separators=(",", ":"))
            if isinstance(planner_output, dict)
            else (str(planner_output) if planner_output is not None else None)
        )
        if output_text is not None and candidate_outcome == "succeeded":
            model_evidence, model_response = _model_commit_evidence(started, output_text)
        else:
            model_evidence = {
                "route_receipt_digest": None,
                "usage_receipt_digest": None,
                "host_receipt_digest": "sha256:" + "d" * 64,
            }
            model_response = None
        settled = repository.commit(
            {
                **caller_common_input(
                    database, request, started, idempotency_key="planner-commit"
                ),
                "claim_id": started.claimant.claim_id,
                "claim_fence": started.claimant.claim_fence,
                "physical_attempt_id": started.physical_attempt.physical_attempt_id,
                "sent_request_digest": started.request_digest,
                "sent_at_unix_ms": FIXED_NOW_MS + 2,
                "provider_or_child_request_id": "provider-planner-1",
                "observation": {
                    "kind": "model",
                    "outcome": candidate_outcome,
                    "output_text": output_text,
                    "output_digest": (
                        digest_bytes(output_text.encode())
                        if output_text is not None
                        else None
                    ),
                    **model_evidence,
                },
                "model_response": model_response,
            }
        )
        assert settled.state == (
            "settled_success" if candidate_outcome == "succeeded" else "settled_failure"
        )
        owner_digest = canonical_sha256("planner-successor-owner")
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
        if restart_before_consume:
            old_attempt = claim.attempt
            old_fence = fence
            before_takeover = host.registry._connection.execute(
                "SELECT rebind_generation, successor_attempt_id, successor_attempt_fence "
                "FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            before_event_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind LIKE 'planner_successor_prepare%'",
                (record.operation.value,),
            ).fetchone()[0]
            before_attempt_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()[0]
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE operations SET state = 'accepted' WHERE operation_id = ?",
                    (record.operation.value,),
                )
                host.registry._connection.execute(
                    "UPDATE operation_dispatch SET state = 'queued' WHERE operation_id = ?",
                    (record.operation.value,),
                )
            with pytest.raises(StaleAttemptFence, match="owner is still live"):
                host.registry.claim_next(
                    runtime_generation=host.runtime_generation,
                    dispatcher_generation=host.runtime_generation,
                    owner_digest=canonical_sha256("live-competitor"),
                    lease_duration_ms=30_000,
                )
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE operations SET state = 'running' WHERE operation_id = ?",
                    (record.operation.value,),
                )
                host.registry._connection.execute(
                    "UPDATE operation_dispatch SET state = 'running' WHERE operation_id = ?",
                    (record.operation.value,),
                )
            after_takeover = host.registry._connection.execute(
                "SELECT rebind_generation, successor_attempt_id, successor_attempt_fence "
                "FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            after_event_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind LIKE 'planner_successor_prepare%'",
                (record.operation.value,),
            ).fetchone()[0]
            assert tuple(after_takeover) == tuple(before_takeover)
            assert after_event_count == before_event_count
            assert host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()[0] == before_attempt_count
            host.close()
            host = ReferenceHost(
                database,
                now_ms=lambda: clock[0],
                workbench_planner=_FailIfCalledPlanner(),
                enable_durable_dispatch=False,
                dispatcher_concurrency=1,
            )
            coordinator = host.rlm_workbench
            assert coordinator is not None
            assert host.status(record.operation).state.value == "indeterminate"
            assert host.recover_durable_workbench() == 1
            successor_owner = canonical_sha256("planner-restart-successor-owner")
            claim = host.registry.claim_next(
                runtime_generation=host.runtime_generation,
                dispatcher_generation=host.runtime_generation,
                owner_digest=successor_owner,
                lease_duration_ms=30_000,
            )
            assert claim is not None
            fence = AttemptFence(
                dispatcher_generation=claim.dispatcher_generation,
                lease_epoch=claim.lease_epoch,
                owner_digest=claim.owner_digest,
            )
            token = host.registry.prepared_planner_successor(
                claim.attempt,
                fence.dispatcher_generation,
                fence.lease_epoch,
                fence.owner_digest,
            )
            assert token is not None
            assert token["rebind_generation"] == 2
            assert token["successor_attempt_id"] != old_attempt.attempt_id
            rebound = host.registry._connection.execute(
                "SELECT payload_json FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_successor_prepare_rebound'",
                (record.operation.value,),
            ).fetchone()
            assert rebound is not None
            rebound_payload = json.loads(str(rebound["payload_json"]))
            assert rebound_payload == {
                "outbox_digest": token["outbox_digest"],
                "suspension_revision": token["suspension_revision"],
                "rebind_generation": 2,
                "successor_attempt_id": token["successor_attempt_id"],
                "successor_attempt_fence": token["successor_attempt_fence"],
                "predecessor_attempt_no": old_attempt.attempt_no - 1,
                "old_successor_attempt_id": old_attempt.attempt_id,
                "old_successor_attempt_fence": canonical_sha256(
                    {
                        "dispatcher_generation": old_fence.dispatcher_generation,
                        "lease_epoch": old_fence.lease_epoch,
                        "owner_digest": old_fence.owner_digest,
                    }
                ),
                "old_rebind_generation": 1,
                "new_successor_attempt_id": token["successor_attempt_id"],
                "new_successor_attempt_fence": token["successor_attempt_fence"],
                "new_rebind_generation": 2,
            }
            with pytest.raises(StaleAttemptFence):
                host.registry.prepared_planner_successor(
                    old_attempt,
                    old_fence.dispatcher_generation,
                    old_fence.lease_epoch,
                    old_fence.owner_digest,
                )
        if control_override == "cancel":
            host.cancel(record.operation)
        elif control_override == "deadline":
            deadline = host.registry._connection.execute(
                "SELECT cumulative_deadline_unix_ms FROM rlm_workbench_jobs "
                "WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()[0]
            clock[0] = int(deadline)
        if expected_phase == "running":
            if (
                planner_output == {
                    "kind": "finalize",
                    "output": {"answer": "ok", "artifacts": []},
                    "artifact_stage_ids": [],
                }
                and not restart_before_consume
            ):
                token = host.registry.prepared_planner_successor(
                    claim.attempt,
                    fence.dispatcher_generation,
                    fence.lease_epoch,
                    fence.owner_digest,
                )
                assert token is not None
                with pytest.raises(InvalidTransition, match="directive is invalid"):
                    host.registry.consume_planner_successor(
                        claim.attempt,
                        fence.dispatcher_generation,
                        fence.lease_epoch,
                        fence.owner_digest,
                        token=token,
                        directive={"kind": "finalize"},
                    )
                assert host.registry._connection.execute(
                    "SELECT state FROM rlm_workbench_successor_outbox "
                    "WHERE operation_id = ?",
                    (record.operation.value,),
                ).fetchone()[0] == "prepared"
                assert host.registry._connection.execute(
                    "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                    "AND event_kind = 'planner_successor_consumed'",
                    (record.operation.value,),
                ).fetchone()[0] == 0
            consumed = coordinator._consume_root_planner(claim.attempt, fence)
            assert consumed.root == planner_output
            assert output_text is not None
            (
                model_calls,
                directive_digests,
                usage_receipt_digests,
                broker_trace,
            ) = coordinator._planner_trace(record.operation)
            assert model_calls == 1
            assert directive_digests == (canonical_sha256(planner_output),)
            assert usage_receipt_digests == (model_evidence["usage_receipt_digest"],)
            assert broker_trace == (
                {
                    "suspension_revision": 1,
                    "ticket_id": settled.ticket_id,
                    "settlement_digest": settled.settled_receipt_digest,
                    "route_receipt_digest": model_evidence["route_receipt_digest"],
                    "usage_receipt_digest": model_evidence["usage_receipt_digest"],
                    "host_receipt_digest": model_evidence["host_receipt_digest"],
                    "output_digest": digest_bytes(output_text.encode()),
                },
            )
        elif expected_phase == "corrected_running":
            correction = coordinator._consume_root_planner(claim.attempt, fence)
            assert correction.phase == "waiting_external"
            correction_row = host.registry._connection.execute(
                "SELECT ticket_id, logical_owner_json FROM rlm_workbench_suspensions "
                "WHERE operation_id = ? ORDER BY suspension_revision DESC LIMIT 1",
                (record.operation.value,),
            ).fetchone()
            correction_ticket = repository.get(str(correction_row["ticket_id"]))
            correction_prompt = json.loads(
                str(correction_ticket.root["request"]["prompt"])
            )
            assert json.loads(str(correction_row["logical_owner_json"])) == {
                "kind": "planner",
                "phase": "correction",
                "step_index": 1,
            }
            assert correction_prompt["correction"]["invalid_output_digest"] == (
                digest_bytes(b"not-json")
            )
            assert correction_prompt["correction"]["errors"]
            assert "not-json" not in json.dumps(correction_prompt["correction"])
            correction_claim = repository.claim(
                {
                    **caller_common_input(
                        database,
                        request,
                        correction_ticket,
                        idempotency_key="correction-claim",
                    ),
                    "adapter_id": "fixture-adapter",
                    "adapter_generation": 1,
                    "claim_lease_ms": 5_000,
                }
            )
            assert (
                correction_claim.claimant is not None
                and correction_claim.physical_attempt is not None
            )
            correction_started = repository.mark_send_started(
                {
                    **caller_common_input(
                        database,
                        request,
                        correction_claim,
                        idempotency_key="correction-send-start",
                    ),
                    "claim_id": correction_claim.claimant.claim_id,
                    "claim_fence": correction_claim.claimant.claim_fence,
                    "physical_attempt_id": (
                        correction_claim.physical_attempt.physical_attempt_id
                    ),
                    "expected_claim_expires_at_unix_ms": (
                        correction_claim.claimant.claim_expires_at_unix_ms
                    ),
                    "provider_or_child_idempotency_key": (
                        correction_claim.physical_attempt.provider_or_child_idempotency_key
                    ),
                    "sent_request_digest": correction_claim.request_digest,
                    "lookup_supported": True,
                    "cancel_supported": True,
                }
            )
            assert (
                correction_started.claimant is not None
                and correction_started.physical_attempt is not None
            )
            replacement = {
                "kind": "finalize",
                "output": {"answer": "corrected", "artifacts": []},
                "artifact_stage_ids": [],
            }
            replacement_text = json.dumps(
                replacement, sort_keys=True, separators=(",", ":")
            )
            correction_evidence, correction_response = _model_commit_evidence(
                correction_started, replacement_text
            )
            repository.commit(
                {
                    **caller_common_input(
                        database,
                        request,
                        correction_started,
                        idempotency_key="correction-commit",
                    ),
                    "claim_id": correction_started.claimant.claim_id,
                    "claim_fence": correction_started.claimant.claim_fence,
                    "physical_attempt_id": (
                        correction_started.physical_attempt.physical_attempt_id
                    ),
                    "sent_request_digest": correction_started.request_digest,
                    "sent_at_unix_ms": FIXED_NOW_MS + 4,
                    "provider_or_child_request_id": "provider-planner-correction-1",
                    "observation": {
                        "kind": "model",
                        "outcome": "succeeded",
                        "output_text": replacement_text,
                        "output_digest": digest_bytes(replacement_text.encode()),
                        **correction_evidence,
                    },
                    "model_response": correction_response,
                }
            )
            correction_successor = host.registry.claim_next(
                runtime_generation=host.runtime_generation,
                dispatcher_generation=host.runtime_generation,
                owner_digest=canonical_sha256("planner-correction-successor-owner"),
                lease_duration_ms=30_000,
            )
            assert correction_successor is not None
            replacement_directive = coordinator._consume_root_planner(
                correction_successor.attempt,
                AttemptFence(
                    dispatcher_generation=correction_successor.dispatcher_generation,
                    lease_epoch=correction_successor.lease_epoch,
                    owner_digest=correction_successor.owner_digest,
                ),
            )
            assert replacement_directive.root == replacement
            model_calls, directive_digests, usage_digests, broker_trace = (
                coordinator._planner_trace(record.operation)
            )
            assert model_calls == 2
            assert directive_digests == (canonical_sha256(replacement),)
            assert usage_digests == (
                model_evidence["usage_receipt_digest"],
                correction_evidence["usage_receipt_digest"],
            )
            assert len(broker_trace) == 2
        else:
            terminal = coordinator._consume_root_planner(claim.attempt, fence)
            assert terminal.phase == expected_phase
            assert host.registry.get(record.operation).state.value == expected_phase
        with host.registry._connection:
            outbox = host.registry._connection.execute(
                "SELECT state, rebind_generation FROM rlm_workbench_successor_outbox "
                "WHERE operation_id = ? ORDER BY suspension_revision DESC LIMIT 1",
                (record.operation.value,),
            ).fetchone()
            job = host.registry._connection.execute(
                "SELECT phase FROM rlm_workbench_jobs WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
            event_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_successor_consumed'",
                (record.operation.value,),
            ).fetchone()[0]
            terminal_event_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_successor_terminal'",
                (record.operation.value,),
            ).fetchone()[0]
            correction_event_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_correction_requested'",
                (record.operation.value,),
            ).fetchone()[0]
            cell_count = host.registry._connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()[0]
        expected_state = "consumed"
        expected_generation = 2 if restart_before_consume else 1
        assert outbox is not None and tuple(outbox) == (
            expected_state,
            expected_generation,
        )
        expected_job_phase = (
            "running" if expected_phase == "corrected_running" else expected_phase
        )
        assert job is not None and tuple(job) == (expected_job_phase,)
        assert event_count == int(expected_job_phase == "running")
        assert terminal_event_count == int(
            expected_phase in {"failed", "cancelled", "timed_out"}
        )
        assert correction_event_count == int(expected_phase == "corrected_running")
        assert cell_count == 0
    finally:
        host.close()


def test_execute_cell_consume_atomically_prepares_cell_before_physical_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=_FailIfCalledPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
        )
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        ticket_id = wait_for_pending_ticket(database)
        assert host.dispatcher is not None and host.caller_work is not None
        host.dispatcher.close()
        host.dispatcher = None
        pending = host.caller_work.get(ticket_id)
        claimed = host.caller_work.claim(
            {
                **caller_common_input(
                    database, request, pending, idempotency_key="atomic-cell-claim"
                ),
                "adapter_id": "fixture-adapter",
                "adapter_generation": 1,
                "claim_lease_ms": 5_000,
            }
        )
        assert claimed.claimant is not None and claimed.physical_attempt is not None
        started = host.caller_work.mark_send_started(
            {
                **caller_common_input(
                    database, request, claimed, idempotency_key="atomic-cell-send"
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
        assert started.claimant is not None and started.physical_attempt is not None
        output = {
            "kind": "execute_cell",
            "code": "answer = 42\nanswer",
            "expected_result_hint": "42",
        }
        output_text = json.dumps(output, sort_keys=True, separators=(",", ":"))
        atomic_evidence, atomic_response = _model_commit_evidence(
            started, output_text
        )
        host.caller_work.commit(
            {
                **caller_common_input(
                    database, request, started, idempotency_key="atomic-cell-commit"
                ),
                "claim_id": started.claimant.claim_id,
                "claim_fence": started.claimant.claim_fence,
                "physical_attempt_id": started.physical_attempt.physical_attempt_id,
                "sent_request_digest": started.request_digest,
                "sent_at_unix_ms": FIXED_NOW_MS + 2,
                "provider_or_child_request_id": "provider-atomic-cell",
                "observation": {
                    "kind": "model",
                    "outcome": "succeeded",
                    "output_text": output_text,
                    "output_digest": digest_bytes(output_text.encode()),
                    **atomic_evidence,
                },
                "model_response": atomic_response,
            }
        )
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=canonical_sha256("atomic-cell-owner"),
            lease_duration_ms=30_000,
        )
        assert claim is not None and host.rlm_workbench is not None
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=claim.owner_digest,
        )
        original_consume = host.registry.consume_planner_successor

        def crash_after_consume(*args: object, **kwargs: object) -> object:
            original_consume(*args, **kwargs)
            raise BaseException("simulated process loss after planner consume")

        monkeypatch.setattr(host.registry, "consume_planner_successor", crash_after_consume)
        with pytest.raises(BaseException, match="after planner consume"):
            host.rlm_workbench._consume_root_planner(claim.attempt, fence)
        row = host.registry._connection.execute(
            """
            SELECT outbox.state AS outbox_state, cell.state AS cell_state,
                   cell.cell_execution_id, cell.pre_checkpoint_digest,
                   authority.attempt_id, authority.attempt_fence,
                   event.payload_json
            FROM rlm_workbench_successor_outbox AS outbox
            JOIN rlm_workbench_cells AS cell ON cell.operation_id = outbox.operation_id
            JOIN rlm_workbench_attempt_authority AS authority
              ON authority.operation_id = outbox.operation_id
            JOIN operation_events AS event ON event.operation_id = outbox.operation_id
             AND event.event_kind = 'planner_successor_consumed'
            WHERE outbox.operation_id = ?
            """,
            (record.operation.value,),
        ).fetchone()
        assert row is not None
        payload = json.loads(str(row["payload_json"]))
        assert row["outbox_state"] == "consumed"
        assert row["cell_state"] == "prepared"
        assert row["attempt_id"] == claim.attempt.attempt_id
        assert row["attempt_fence"] == canonical_sha256(
            {
                "dispatcher_generation": fence.dispatcher_generation,
                "lease_epoch": fence.lease_epoch,
                "owner_digest": fence.owner_digest,
            }
        )
        assert payload["cell_execution_id"] == row["cell_execution_id"]
        assert payload["pre_checkpoint_digest"] == row["pre_checkpoint_digest"]
        prepared_cell_id = str(row["cell_execution_id"])
        first_attempt_id = claim.attempt.attempt_id
    finally:
        host.close()

    restart_planner = _FailIfCalledPlanner()
    restarted = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=restart_planner,
        enable_durable_dispatch=False,
    )
    try:
        assert restarted.registry.get(record.operation).state.value == "indeterminate"
        assert restarted.rlm_workbench is not None
        assert restarted.rlm_workbench.recover_indeterminate(
            restarted.runtime_generation
        ) == 1
        successor = restarted.registry.claim_next(
            runtime_generation=restarted.runtime_generation,
            dispatcher_generation=restarted.runtime_generation,
            owner_digest=canonical_sha256("atomic-cell-restart-owner"),
            lease_duration_ms=30_000,
        )
        assert successor is not None
        successor_fence = AttemptFence(
            dispatcher_generation=successor.dispatcher_generation,
            lease_epoch=successor.lease_epoch,
            owner_digest=successor.owner_digest,
        )
        recovered_directive = restarted.rlm_workbench._consume_root_planner(
            successor.attempt, successor_fence
        )
        assert recovered_directive.root == output
        recovered_cell = restarted.registry._connection.execute(
            """
            SELECT cell_execution_id, state, attempt_id, attempt_fence
            FROM rlm_workbench_cells WHERE operation_id = ?
            """,
            (record.operation.value,),
        ).fetchone()
        assert recovered_cell is not None
        assert recovered_cell["cell_execution_id"] == prepared_cell_id
        assert recovered_cell["state"] == "prepared"
        assert recovered_cell["attempt_id"] == successor.attempt.attempt_id
        assert recovered_cell["attempt_id"] != first_attempt_id
        assert recovered_cell["attempt_fence"] == canonical_sha256(
            {
                "dispatcher_generation": successor_fence.dispatcher_generation,
                "lease_epoch": successor_fence.lease_epoch,
                "owner_digest": successor_fence.owner_digest,
            }
        )
        assert restart_planner.calls == 0
    finally:
        restarted.close()
