from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from aar.broker_models import BrokerUsage, ModelRequest
from aar.canonical import canonical_sha256
from aar.continuity_models import (
    OperationAttemptRefV1,
    OperationRecoveryPolicyBindingV1,
)
from aar.rlm_models import RlmJobSpec
from aar.runtime.brokers import BrokerCallConflict
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
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import IdempotencyConflict, InvalidTransition, StaleAttemptFence
from aar.runtime.rlm import next_rlm_action
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceEnvironmentFingerprint,
    WorkspaceProgramSpec,
)
from aar.schemas import (
    Budget,
    OperationRef,
    OperationState,
    PrincipalRef,
    SessionRef,
    WorkspaceRef,
)

NOW_MS = 1_700_000_000_000


def open_host(database: Path, *, durable: bool = False) -> ReferenceHost:
    return ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=durable,
        dispatcher_concurrency=1,
    )


def recovery_context(host: ReferenceHost, suffix: str) -> tuple[RlmJobSpec, Any, Any]:
    spec = RlmJobSpec(
        query=f"recovery policy {suffix}",
        strategy="baseline",
        max_steps=1,
    )
    envelope = host.request_rlm_envelope(
        request_id=f"request-recovery-{suffix}",
        idempotency_key=f"idempotency-recovery-{suffix}",
        principal=PrincipalRef(value="principal-recovery"),
        session=SessionRef(value="session-recovery"),
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=1_024,
            output_tokens=128,
        ),
    )
    policy = rlm_step_boundary_policy()
    binding = OperationRecoveryPolicyBindingV1.issue(
        operation=OperationRef(value=f"operation-recovery-{suffix}"),
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=host.capabilities.digest,
            policy=policy,
        ),
        created_at_unix_ms=NOW_MS,
    )
    return spec, envelope, binding


def plan(
    host: ReferenceHost,
    spec: RlmJobSpec,
    envelope: Any,
    binding: OperationRecoveryPolicyBindingV1,
    **overrides: Any,
) -> Any:
    updates = dict(overrides)
    values: dict[str, Any] = {
        "operation": binding.operation,
        "input_digest": envelope.input_digest,
        "envelope": envelope,
        "spec": updates.pop("spec_override", spec),
        "prior_attempt": OperationAttemptRefV1(
            operation=binding.operation,
            attempt_no=1,
            attempt_id="attempt-recovery-1",
        ),
        "prior_runtime_generation": 1,
        "prior_dispatcher_generation": 1,
        "prior_lease_epoch": 1,
        "policy_binding": binding,
        "current_capability_digest": host.capabilities.digest,
        "steps": (),
        "traces": (),
        "usage": BrokerUsage(),
        "cancellation_requested": False,
        "unresolved_calls": False,
        "now_unix_ms": NOW_MS,
    }
    values.update(updates)
    return plan_rlm_step_successor(**values)


def test_valid_rlm_recovery_plan_binds_certain_empty_step_boundary(tmp_path: Path) -> None:
    host = open_host(tmp_path / "valid.sqlite3")
    try:
        spec, envelope, binding = recovery_context(host, "valid")
        result = plan(host, spec, envelope, binding)
        assert result.decision == "start_successor"
        assert result.reason_code == "rlm_certain_step_boundary"
        assert result.boundary is not None
        assert result.boundary.policy_digest == binding.policy_digest
        assert result.boundary.input_digest == envelope.input_digest
        assert result.boundary.committed_step_count == 0
        assert result.boundary.last_step_index is None
    finally:
        host.close()


@pytest.mark.parametrize(
    ("overrides", "decision", "reason"),
    [
        (
            {"input_digest": canonical_sha256({"different": "input"})},
            "quarantine",
            "recovery_input_digest_mismatch",
        ),
        (
            {
                "spec_override": RlmJobSpec(
                    query="tampered recovery payload",
                    strategy="baseline",
                    max_steps=1,
                )
            },
            "quarantine",
            "recovery_payload_digest_mismatch",
        ),
        (
            {"current_capability_digest": canonical_sha256({"different": "capability"})},
            "needs_user",
            "recovery_capability_digest_mismatch",
        ),
        (
            {"cancellation_requested": True},
            "needs_user",
            "cancellation_requested",
        ),
        (
            {"now_unix_ms": NOW_MS + 60_000},
            "needs_user",
            "deadline_expired",
        ),
        (
            {"usage": BrokerUsage(model_requests=1)},
            "needs_user",
            "cumulative_budget_exhausted",
        ),
        (
            {"unresolved_calls": True},
            "reconcile_effect",
            "broker_call_unresolved",
        ),
    ],
)
def test_rlm_recovery_plan_fails_closed(
    tmp_path: Path,
    overrides: dict[str, Any],
    decision: str,
    reason: str,
) -> None:
    host = open_host(tmp_path / f"blocked-{reason}.sqlite3")
    try:
        spec, envelope, binding = recovery_context(host, reason)
        result = plan(host, spec, envelope, binding, **overrides)
        assert result.decision == decision
        assert result.reason_code == reason
        assert result.boundary is None
    finally:
        host.close()


def test_rlm_recovery_plan_rejects_changed_environment_binding(tmp_path: Path) -> None:
    host = open_host(tmp_path / "environment.sqlite3")
    try:
        spec, envelope, binding = recovery_context(host, "environment")
        changed = OperationRecoveryPolicyBindingV1.issue(
            operation=binding.operation,
            operation_kind=binding.operation_kind,
            policy=binding.policy,
            environment_digest=canonical_sha256({"different": "environment"}),
            created_at_unix_ms=binding.created_at_unix_ms,
        )
        result = plan(host, spec, envelope, changed)
        assert result.decision == "needs_user"
        assert result.reason_code == "recovery_environment_mismatch"
        assert result.boundary is None
    finally:
        host.close()


def test_broker_trace_sequence_and_grant_are_part_of_recovery_authority(
    tmp_path: Path,
) -> None:
    host = open_host(tmp_path / "trace-authority.sqlite3")
    try:
        spec, envelope, binding = recovery_context(host, "trace-authority")
        action = next_rlm_action(spec, ())
        assert action is not None and action.kind == "model.request"
        broker = host.brokers.bind(envelope, binding.operation)
        broker.model_request(ModelRequest(prompt=action.text), step_key="rlm-step-0")
        trace = broker.traces()[0]

        valid = plan(
            host,
            spec,
            envelope,
            binding,
            traces=(trace,),
            usage=broker.usage,
        )
        assert valid.decision == "start_successor"

        bad_sequence = plan(
            host,
            spec,
            envelope,
            binding,
            traces=(trace.model_copy(update={"sequence": 2}),),
            usage=broker.usage,
        )
        assert bad_sequence.decision == "quarantine"
        assert bad_sequence.reason_code == "broker_trace_sequence_mismatch"

        bad_grant = plan(
            host,
            spec,
            envelope,
            binding,
            traces=(trace.model_copy(update={"grant_id": "grant-other"}),),
            usage=broker.usage,
        )
        assert bad_grant.decision == "quarantine"
        assert bad_grant.reason_code == "uncommitted_broker_receipt_mismatch"
    finally:
        host.close()


def test_broker_success_receipt_digest_is_verified_on_read_and_reuse(
    tmp_path: Path,
) -> None:
    database = tmp_path / "receipt-integrity.sqlite3"
    host = open_host(database)
    try:
        spec, envelope, binding = recovery_context(host, "receipt-integrity")
        action = next_rlm_action(spec, ())
        assert action is not None and action.kind == "model.request"
        request = ModelRequest(prompt=action.text)
        broker = host.brokers.bind(envelope, binding.operation)
        broker.model_request(request, step_key="rlm-step-0")

        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE broker_calls SET response_digest = ? WHERE operation_id = ?",
                (
                    canonical_sha256({"tampered": "receipt"}),
                    binding.operation.value,
                ),
            )
            connection.commit()
        finally:
            connection.close()

        trace = broker.traces()[0]
        assert trace.state == "failed"
        assert trace.failure_code == "BrokerReceiptDigestMismatch"
        blocked = plan(
            host,
            spec,
            envelope,
            binding,
            traces=(trace,),
            usage=broker.usage,
        )
        assert blocked.decision == "quarantine"
        assert blocked.reason_code == "uncommitted_broker_receipt_mismatch"
        with pytest.raises(BrokerCallConflict, match="receipt digest"):
            broker.model_request(request, step_key="rlm-step-0")
    finally:
        host.close()


def test_atomic_successor_requeue_rejects_a_stale_lease_boundary(tmp_path: Path) -> None:
    database = tmp_path / "stale-boundary.sqlite3"
    first = open_host(database)
    spec, envelope, _ = recovery_context(first, "stale-boundary")
    record = first.submit_rlm(envelope, spec)
    policy = rlm_step_boundary_policy()
    first.registry.bind_recovery_policy(
        record.operation,
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=first.capabilities.digest,
            policy=policy,
        ),
    )
    first.registry.request_dispatch(record.operation)
    claim = first.registry.claim_next(
        first.runtime_generation,
        first.runtime_generation,
        canonical_sha256({"owner": "stale-boundary"}),
        30_000,
    )
    assert claim is not None
    first.close()

    second = open_host(database)
    try:
        second.registry.fence_expired_attempts(second.runtime_generation)
        assert second.status(record.operation).state is OperationState.INDETERMINATE
        binding = second.registry.recovery_policy_binding(record.operation)
        prior = second.registry.latest_recovery_attempt_fence(record.operation)
        assert binding is not None and prior is not None
        current_record = second.status(record.operation)
        current_envelope = type(envelope).model_validate_json(
            current_record.request_json, strict=True
        )
        broker = second.brokers.bind(current_envelope, record.operation)
        inner = second.rlm.store.snapshot(record.operation)
        recovery_plan = plan_rlm_step_successor(
            operation=record.operation,
            input_digest=current_record.input_digest,
            envelope=current_envelope,
            spec=spec,
            prior_attempt=prior.attempt,
            prior_runtime_generation=prior.runtime_generation,
            prior_dispatcher_generation=prior.dispatcher_generation,
            prior_lease_epoch=prior.lease_epoch,
            policy_binding=binding,
            current_capability_digest=second.capabilities.digest,
            steps=inner.steps,
            traces=broker.traces(),
            usage=broker.usage,
            cancellation_requested=False,
            unresolved_calls=False,
            now_unix_ms=NOW_MS,
        )
        assert recovery_plan.decision == "start_successor"
        assert recovery_plan.boundary is not None
        stale_boundary = recovery_plan.boundary.model_copy(
            update={"lease_epoch": recovery_plan.boundary.lease_epoch + 1}
        )
        with pytest.raises(StaleAttemptFence, match="lease epoch"):
            second.registry.requeue_indeterminate(
                record.operation,
                second.runtime_generation,
                decision="start_successor",
                reason_code=recovery_plan.reason_code,
                input_digest=current_record.input_digest,
                policy_binding=binding,
                continuation_boundary=stale_boundary,
            )
        assert second.status(record.operation).state is OperationState.INDETERMINATE
        assert second.registry.recovery_decisions(record.operation) == ()
        assert second.registry.rlm_step_boundaries(record.operation) == ()
    finally:
        second.close()


def test_invalid_persisted_policy_is_quarantined_without_blocking_dispatcher(
    tmp_path: Path,
) -> None:
    database = tmp_path / "invalid-policy.sqlite3"
    first = open_host(database)
    spec, envelope, _ = recovery_context(first, "invalid-policy")
    record = first.submit_rlm(envelope, spec)
    policy = rlm_step_boundary_policy()
    first.registry.bind_recovery_policy(
        record.operation,
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=first.capabilities.digest,
            policy=policy,
        ),
    )
    first.registry.request_dispatch(record.operation)
    claim = first.registry.claim_next(
        first.runtime_generation,
        first.runtime_generation,
        canonical_sha256({"owner": "invalid-policy"}),
        30_000,
    )
    assert claim is not None
    connection = sqlite3.connect(database)
    try:
        connection.execute(
            "UPDATE operation_recovery_policies SET policy_digest = ? WHERE operation_id = ?",
            (canonical_sha256({"tampered": "policy"}), record.operation.value),
        )
        connection.commit()
    finally:
        connection.close()
    first.close()

    second = open_host(database, durable=True)
    try:
        parked = second.status(record.operation)
        assert parked.state is OperationState.INDETERMINATE
        decisions = second.registry.recovery_decisions(record.operation)
        assert [item.decision for item in decisions] == ["quarantine"]
        assert decisions[0].reason_code == "recovery_policy_invalid"

        healthy_spec, healthy_envelope, _ = recovery_context(second, "healthy-after-quarantine")
        healthy = second.submit_rlm_durable(healthy_envelope, healthy_spec)
        completed = second.wait_rlm(healthy.operation, timeout_s=2)
        assert completed.state is OperationState.SUCCEEDED
    finally:
        second.close()


def test_recovery_policy_is_bound_before_dispatch_and_is_byte_stable(tmp_path: Path) -> None:
    host = open_host(tmp_path / "admission.sqlite3")
    try:
        spec, envelope, _binding = recovery_context(host, "admission")
        record = host.submit_rlm(envelope, spec)
        policy = rlm_step_boundary_policy()
        environment_digest = rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=host.capabilities.digest,
            policy=policy,
        )
        first = host.registry.bind_recovery_policy(
            record.operation,
            operation_kind=RLM_OPERATION_KIND,
            policy=policy,
            environment_digest=environment_digest,
        )
        replay = host.registry.bind_recovery_policy(
            record.operation,
            operation_kind=RLM_OPERATION_KIND,
            policy=policy,
            environment_digest=environment_digest,
        )
        assert replay == first
        assert host.registry.recovery_policy_binding(record.operation) == first
        assert [event.note for event in host.registry.events(record.operation)].count(
            "recovery_policy_bound"
        ) == 1

        changed_policy = policy.model_copy(update={"max_successor_attempts": 2})
        with pytest.raises(IdempotencyConflict):
            host.registry.bind_recovery_policy(
                record.operation,
                operation_kind=RLM_OPERATION_KIND,
                policy=changed_policy,
                environment_digest=environment_digest,
            )

        late_spec, late_envelope, _ = recovery_context(host, "late")
        late_record = host.submit_rlm(late_envelope, late_spec)
        host.registry.request_dispatch(late_record.operation)
        with pytest.raises(InvalidTransition, match="before durable dispatch"):
            host.registry.bind_recovery_policy(
                late_record.operation,
                operation_kind=RLM_OPERATION_KIND,
                policy=policy,
                environment_digest=rlm_recovery_environment_digest(
                    spec=late_spec,
                    capability_digest=host.capabilities.digest,
                    policy=policy,
                ),
            )
    finally:
        host.close()


def workspace_recovery_context(
    host: ReferenceHost,
    suffix: str,
    *,
    partial: bool = False,
    allow_partial: bool = False,
):
    session = SessionRef(value=f"session-workspace-{suffix}")
    handle = host.program_workspace.create(
        WorkspaceRef(value=f"workspace-{suffix}"),
        session,
    )
    setup = host.program_workspace.execute(
        OperationRef(value=f"operation-workspace-setup-{suffix}"),
        handle,
        WorkspaceProgramSpec(
            code=(
                "answer = 41\nunsupported = object()\nanswer"
                if partial
                else "answer = 41\nanswer"
            )
        ),
    )
    handle = ProgrammableWorkspaceHandle(
        workspace=handle.workspace,
        backend=handle.backend,
        generation=handle.generation,
        revision=setup.revision_after,
    )
    manifest = host.program_workspace.checkpoint(
        OperationRef(value=f"operation-workspace-checkpoint-{suffix}"),
        handle,
        WorkspaceCheckpointPolicy(),
        f"trace-workspace-{suffix}",
    )
    spec = WorkspaceProgramSpec(
        code="answer += 1\nanswer",
        checkpoint_replay_safe=True,
    )
    envelope = host.request_program_workspace_envelope(
        request_id=f"request-workspace-{suffix}",
        idempotency_key=f"idempotency-workspace-{suffix}",
        principal=PrincipalRef(value="principal-workspace-recovery"),
        session=session,
        handle=handle,
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
    )
    policy = workspace_checkpoint_boundary_policy().model_copy(
        update={"allow_partial_checkpoint": allow_partial}
    )
    binding = OperationRecoveryPolicyBindingV1.issue(
        operation=OperationRef(value=f"operation-workspace-recovery-{suffix}"),
        operation_kind=WORKSPACE_PROGRAM_OPERATION_KIND,
        policy=policy,
        environment_digest=workspace_recovery_environment_digest(
            backend=host.program_workspace.descriptor,
            environment=host.program_workspace.environment,
            capability_digest=host.capabilities.digest,
            policy=policy,
        ),
        created_at_unix_ms=NOW_MS,
    )
    return handle, spec, envelope, manifest, binding


def workspace_plan(
    host: ReferenceHost,
    handle: ProgrammableWorkspaceHandle,
    spec: WorkspaceProgramSpec,
    envelope: Any,
    manifest: WorkspaceCheckpointManifest | None,
    binding: OperationRecoveryPolicyBindingV1,
    **overrides: Any,
):
    values: dict[str, Any] = {
        "operation": binding.operation,
        "input_digest": envelope.input_digest,
        "envelope": envelope,
        "handle": handle,
        "spec": spec,
        "prior_attempt": OperationAttemptRefV1(
            operation=binding.operation,
            attempt_no=1,
            attempt_id="attempt-workspace-recovery-1",
        ),
        "policy_binding": binding,
        "current_capability_digest": host.capabilities.digest,
        "current_backend": host.program_workspace.descriptor,
        "current_environment": host.program_workspace.environment,
        "manifest": manifest,
        "cancellation_requested": False,
        "now_unix_ms": NOW_MS,
    }
    values.update(overrides)
    return plan_workspace_checkpoint_successor(**values)


def test_workspace_recovery_plan_accepts_exact_checkpoint(tmp_path: Path) -> None:
    host = open_host(tmp_path / "workspace-plan-exact.sqlite3")
    try:
        handle, spec, envelope, manifest, binding = workspace_recovery_context(
            host, "plan-exact"
        )
        result = workspace_plan(host, handle, spec, envelope, manifest, binding)
        assert result.decision == "restore_checkpoint"
        assert result.reason_code == "workspace_exact_checkpoint_boundary"
        assert result.manifest == manifest
    finally:
        host.close()


@pytest.mark.parametrize(
    ("override_key", "override_value", "decision", "reason"),
    (
        ("manifest", None, "needs_user", "workspace_checkpoint_missing"),
        ("cancellation_requested", True, "needs_user", "cancellation_requested"),
        ("now_unix_ms", NOW_MS + 60_000, "needs_user", "deadline_expired"),
        (
            "input_digest",
            canonical_sha256({"different": "workspace-input"}),
            "quarantine",
            "recovery_input_digest_mismatch",
        ),
        (
            "current_capability_digest",
            canonical_sha256({"different": "workspace-capability"}),
            "needs_user",
            "recovery_capability_digest_mismatch",
        ),
    ),
)
def test_workspace_recovery_plan_fails_closed_on_boundary_drift(
    tmp_path: Path,
    override_key: str,
    override_value: Any,
    decision: str,
    reason: str,
) -> None:
    host = open_host(tmp_path / f"workspace-plan-{reason}.sqlite3")
    try:
        handle, spec, envelope, manifest, binding = workspace_recovery_context(host, reason)
        effective_manifest = override_value if override_key == "manifest" else manifest
        effective_overrides = (
            {} if override_key == "manifest" else {override_key: override_value}
        )
        result = workspace_plan(
            host,
            handle,
            spec,
            envelope,
            effective_manifest,
            binding,
            **effective_overrides,
        )
        assert result.decision == decision
        assert result.reason_code == reason
        assert result.manifest is None
    finally:
        host.close()


def test_workspace_recovery_requires_explicit_replay_opt_in(tmp_path: Path) -> None:
    host = open_host(tmp_path / "workspace-plan-opt-in.sqlite3")
    try:
        handle, spec, envelope, manifest, binding = workspace_recovery_context(host, "opt-in")
        result = workspace_plan(
            host,
            handle,
            spec.model_copy(update={"checkpoint_replay_safe": False}),
            envelope,
            manifest,
            binding,
        )
        assert result.decision == "needs_user"
        assert result.reason_code == "workspace_replay_not_declared_safe"
    finally:
        host.close()


def test_workspace_recovery_rejects_foreign_handle_and_environment(tmp_path: Path) -> None:
    host = open_host(tmp_path / "workspace-plan-foreign.sqlite3")
    try:
        handle, spec, envelope, manifest, binding = workspace_recovery_context(
            host, "foreign"
        )
        foreign_handle = handle.model_copy(update={"revision": handle.revision + 1})
        foreign_handle_manifest = WorkspaceCheckpointManifest.issue(
            source_handle=foreign_handle,
            creation_operation=manifest.creation_operation,
            trace_id=manifest.trace_id,
            environment=manifest.environment,
            values=manifest.values,
            exclusions=manifest.exclusions,
            artifacts=manifest.artifacts,
        )
        handle_result = workspace_plan(
            host,
            handle,
            spec,
            envelope,
            foreign_handle_manifest,
            binding,
        )
        assert handle_result.decision == "quarantine"
        assert handle_result.reason_code == "workspace_checkpoint_handle_mismatch"

        foreign_environment = WorkspaceEnvironmentFingerprint.current(
            extra=(("recovery_test", "foreign"),)
        )
        foreign_environment_manifest = WorkspaceCheckpointManifest.issue(
            source_handle=handle,
            creation_operation=manifest.creation_operation,
            trace_id=manifest.trace_id,
            environment=foreign_environment,
            values=manifest.values,
            exclusions=manifest.exclusions,
            artifacts=manifest.artifacts,
        )
        environment_result = workspace_plan(
            host,
            handle,
            spec,
            envelope,
            foreign_environment_manifest,
            binding,
        )
        assert environment_result.decision == "needs_user"
        assert (
            environment_result.reason_code
            == "workspace_checkpoint_environment_mismatch"
        )
    finally:
        host.close()


def test_workspace_partial_checkpoint_requires_explicit_policy(tmp_path: Path) -> None:
    host = open_host(tmp_path / "workspace-plan-partial.sqlite3")
    try:
        handle, spec, envelope, manifest, binding = workspace_recovery_context(
            host, "partial-denied", partial=True
        )
        assert manifest.exclusions
        denied = workspace_plan(host, handle, spec, envelope, manifest, binding)
        assert denied.decision == "needs_user"
        assert denied.reason_code == "partial_checkpoint_not_allowed"

        allowed_handle, allowed_spec, allowed_envelope, allowed_manifest, allowed_binding = (
            workspace_recovery_context(
                host,
                "partial-allowed",
                partial=True,
                allow_partial=True,
            )
        )
        allowed = workspace_plan(
            host,
            allowed_handle,
            allowed_spec,
            allowed_envelope,
            allowed_manifest,
            allowed_binding,
        )
        assert allowed.decision == "restore_checkpoint"
        assert allowed.reason_code == "workspace_exact_checkpoint_boundary"
    finally:
        host.close()
