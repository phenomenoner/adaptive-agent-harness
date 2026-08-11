"""Fail-closed AR-LT3 recovery planning at certain durable boundaries."""

from __future__ import annotations

from typing import Literal

from aar.broker_models import (
    BrokerCallTrace,
    BrokerUsage,
    EvidenceQuery,
    ModelRequest,
)
from aar.canonical import canonical_sha256
from aar.continuity_models import (
    OperationAttemptRefV1,
    OperationRecoveryPolicyBindingV1,
    OperationRecoveryPolicyV1,
    OperationRlmStepBoundaryV1,
    RecoveryDecisionKind,
)
from aar.rlm_models import RlmAction, RlmJobSpec, RlmStep
from aar.runtime.rlm import next_rlm_action
from aar.schemas import Digest, OperationRef, RequestEnvelope, StrictModel
from aar.versions import PACKAGE_VERSION, RLM_SCHEMA_VERSION

RLM_OPERATION_KIND = "rlm.execute"
RLM_STRATEGY_CONTRACT_VERSION = "aar.rlm-strategy.v1"


class RlmRecoveryPlan(StrictModel):
    """One deterministic decision over frozen recovery observations."""

    decision: RecoveryDecisionKind
    reason_code: str
    boundary: OperationRlmStepBoundaryV1 | None = None


def rlm_step_boundary_policy() -> OperationRecoveryPolicyV1:
    """Return the first explicit policy for replay-safe, receipt-backed RLM continuation."""

    return OperationRecoveryPolicyV1(
        policy_version=1,
        policy_id="rlm.step-boundary.v1",
        operation_kind=RLM_OPERATION_KIND,
        allowed_decisions=(
            "needs_user",
            "quarantine",
            "reconcile_effect",
            "start_successor",
            "terminal_from_receipt",
        ),
        max_successor_attempts=3,
        safe_replay_no_effect=False,
        checkpoint_required=False,
        allow_partial_checkpoint=False,
        environment_compatibility="capability-exact",
        broker_uncertainty_policy="receipt-or-park",
        cancellation_policy="block-successor",
        deadline_policy="preserve-original",
        effect_reconcile_required=True,
    )


def rlm_strategy_digest(spec: RlmJobSpec) -> Digest:
    """Bind recovery to an explicit strategy contract version and strategy name."""

    return canonical_sha256(
        {
            "contract_version": RLM_STRATEGY_CONTRACT_VERSION,
            "strategy": spec.strategy,
        }
    )


def rlm_recovery_environment_digest(
    *,
    spec: RlmJobSpec,
    capability_digest: Digest,
    policy: OperationRecoveryPolicyV1,
) -> Digest:
    """Return the portable exact-compatibility identity used at admission and recovery."""

    return canonical_sha256(
        {
            "package_version": PACKAGE_VERSION,
            "rlm_schema_version": RLM_SCHEMA_VERSION,
            "strategy_digest": rlm_strategy_digest(spec),
            "capability_digest": capability_digest,
            "policy_digest": canonical_sha256(policy),
        }
    )


def _request_digest(action: RlmAction) -> Digest:
    if action.kind == "evidence.query":
        return canonical_sha256(EvidenceQuery(text=action.text))
    return canonical_sha256(ModelRequest(prompt=action.text))


def _trace_grant_is_authorized(
    trace: BrokerCallTrace,
    action: RlmAction,
    envelope: RequestEnvelope,
) -> bool:
    return any(
        grant.grant_id == trace.grant_id
        and grant.capability == action.kind
        and grant.issued_to == envelope.principal
        and grant.expires_at_unix_ms >= envelope.deadline_unix_ms
        for grant in envelope.grants
    )


def _blocked(
    decision: Literal["needs_user", "quarantine", "reconcile_effect"],
    reason_code: str,
) -> RlmRecoveryPlan:
    return RlmRecoveryPlan(decision=decision, reason_code=reason_code)


def _usage_exceeds_authority(usage: BrokerUsage, envelope: RequestEnvelope) -> bool:
    budget = envelope.budget
    return any(
        (
            usage.model_requests > budget.model_requests,
            usage.input_tokens > budget.input_tokens,
            usage.output_tokens > budget.output_tokens,
            usage.child_operations > budget.child_operations,
            usage.artifact_bytes > budget.artifact_bytes,
        )
    )


def _next_action_has_no_budget(
    action: RlmAction | None,
    usage: BrokerUsage,
    envelope: RequestEnvelope,
) -> bool:
    if action is None or action.kind != "model.request":
        return False
    budget = envelope.budget
    return (
        usage.model_requests >= budget.model_requests
        or usage.input_tokens >= budget.input_tokens
        or usage.output_tokens >= budget.output_tokens
    )


def plan_rlm_step_successor(
    *,
    operation: OperationRef,
    input_digest: Digest,
    envelope: RequestEnvelope,
    spec: RlmJobSpec,
    prior_attempt: OperationAttemptRefV1,
    prior_runtime_generation: int,
    prior_dispatcher_generation: int,
    prior_lease_epoch: int,
    policy_binding: OperationRecoveryPolicyBindingV1,
    current_capability_digest: Digest,
    steps: tuple[RlmStep, ...],
    traces: tuple[BrokerCallTrace, ...],
    usage: BrokerUsage,
    cancellation_requested: bool,
    unresolved_calls: bool,
    now_unix_ms: int,
) -> RlmRecoveryPlan:
    """Plan one successor only when the complete persisted RLM boundary is certain."""

    policy = policy_binding.policy
    if policy_binding.operation != operation or prior_attempt.operation != operation:
        return _blocked("quarantine", "recovery_identity_mismatch")
    if policy.policy_id != "rlm.step-boundary.v1" or policy.policy_version != 1:
        return _blocked("quarantine", "unknown_recovery_policy")
    if policy.operation_kind != RLM_OPERATION_KIND:
        return _blocked("quarantine", "recovery_operation_kind_mismatch")
    if "start_successor" not in policy.allowed_decisions:
        return _blocked("needs_user", "successor_not_allowed")
    if input_digest != envelope.input_digest:
        return _blocked("quarantine", "recovery_input_digest_mismatch")
    if canonical_sha256(spec) != input_digest:
        return _blocked("quarantine", "recovery_payload_digest_mismatch")
    if envelope.capability_digest != current_capability_digest:
        return _blocked("needs_user", "recovery_capability_digest_mismatch")
    environment_digest = rlm_recovery_environment_digest(
        spec=spec,
        capability_digest=current_capability_digest,
        policy=policy,
    )
    if environment_digest != policy_binding.environment_digest:
        return _blocked("needs_user", "recovery_environment_mismatch")
    if cancellation_requested:
        return _blocked("needs_user", "cancellation_requested")
    if now_unix_ms >= envelope.deadline_unix_ms:
        return _blocked("needs_user", "deadline_expired")
    if unresolved_calls:
        return _blocked("reconcile_effect", "broker_call_unresolved")
    if _usage_exceeds_authority(usage, envelope):
        return _blocked("quarantine", "cumulative_usage_exceeds_budget")

    if tuple(step.index for step in steps) != tuple(range(len(steps))):
        return _blocked("quarantine", "rlm_step_sequence_mismatch")
    trace_sequences = tuple(trace.sequence for trace in traces)
    if trace_sequences != tuple(range(1, len(traces) + 1)):
        return _blocked("quarantine", "broker_trace_sequence_mismatch")
    traces_by_sequence = {trace.sequence: trace for trace in traces}
    referenced_sequences: set[int] = set()

    for index, step in enumerate(steps):
        expected_action = next_rlm_action(spec, steps[:index])
        if (
            expected_action is None
            or step.action != expected_action.kind
            or step.request_digest != canonical_sha256(expected_action)
        ):
            return _blocked("quarantine", "rlm_strategy_or_step_digest_mismatch")
        trace = traces_by_sequence.get(step.broker_call_sequence)
        if trace is None:
            return _blocked("quarantine", "rlm_step_broker_trace_missing")
        if trace.sequence in referenced_sequences:
            return _blocked("quarantine", "rlm_step_broker_trace_reused")
        referenced_sequences.add(trace.sequence)
        if (
            trace.parent_operation != operation
            or trace.method != expected_action.kind
            or trace.grant_id != step.grant_id
            or not _trace_grant_is_authorized(trace, expected_action, envelope)
            or trace.request_digest != _request_digest(expected_action)
            or trace.state != "succeeded"
            or trace.response_digest != canonical_sha256(step.receipt)
        ):
            return _blocked("quarantine", "rlm_step_broker_receipt_mismatch")

    next_action = next_rlm_action(spec, steps)
    extra_traces = tuple(trace for trace in traces if trace.sequence not in referenced_sequences)
    if len(extra_traces) > 1:
        return _blocked("quarantine", "multiple_uncommitted_broker_receipts")
    if extra_traces:
        trace = extra_traces[0]
        if (
            next_action is None
            or trace.parent_operation != operation
            or trace.method != next_action.kind
            or not _trace_grant_is_authorized(trace, next_action, envelope)
            or trace.request_digest != _request_digest(next_action)
            or trace.state != "succeeded"
            or trace.response_digest is None
        ):
            return _blocked("quarantine", "uncommitted_broker_receipt_mismatch")
    if next_action is not None and len(steps) >= spec.max_steps:
        return _blocked("needs_user", "rlm_step_bound_exhausted")
    if not extra_traces and _next_action_has_no_budget(next_action, usage, envelope):
        return _blocked("needs_user", "cumulative_budget_exhausted")

    boundary = OperationRlmStepBoundaryV1.issue(
        operation=operation,
        prior_attempt=prior_attempt,
        runtime_generation=prior_runtime_generation,
        dispatcher_generation=prior_dispatcher_generation,
        lease_epoch=prior_lease_epoch,
        policy_digest=policy_binding.policy_digest,
        input_digest=input_digest,
        strategy=spec.strategy,
        strategy_digest=rlm_strategy_digest(spec),
        capability_digest=current_capability_digest,
        environment_digest=environment_digest,
        deadline_unix_ms=envelope.deadline_unix_ms,
        committed_step_count=len(steps),
        last_step_digest=None if not steps else canonical_sha256(steps[-1]),
        steps_digest=canonical_sha256(steps),
        broker_trace_digest=canonical_sha256(traces),
        usage_digest=canonical_sha256(usage),
        created_at_unix_ms=now_unix_ms,
    )
    return RlmRecoveryPlan(
        decision="start_successor",
        reason_code="rlm_certain_step_boundary",
        boundary=boundary,
    )
