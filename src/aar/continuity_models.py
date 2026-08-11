"""Transport-neutral continuity contracts for durable long operations."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from aar.canonical import canonical_sha256
from aar.schemas import (
    ArtifactReference,
    Digest,
    OpaqueToken,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PositiveCounter,
    Revision,
    StrictModel,
)
from aar.versions import OPERATION_CONTINUITY_SCHEMA_VERSION

CONTINUITY_SCHEMA_VERSION = OPERATION_CONTINUITY_SCHEMA_VERSION

RecoveryDecisionKind = Literal[
    "resume_native",
    "start_successor",
    "restore_checkpoint",
    "reconcile_effect",
    "needs_user",
    "terminal_from_receipt",
    "quarantine",
]
RecoveryPolicyId = Literal[
    "legacy-safe-replay.v1",
    "no-automatic-continuation.v1",
    "rlm.step-boundary.v1",
    "workspace.checkpoint-boundary.v1",
]
EnvironmentCompatibility = Literal["capability-exact", "environment-exact"]
BrokerUncertaintyPolicy = Literal["receipt-or-park", "no-broker-replay"]
CancellationPolicy = Literal["block-successor"]
DeadlinePolicy = Literal["preserve-original"]
DispatchState = Literal["queued", "running", "completed", "parked", "cancelled"]
ContinuityText = Annotated[str, Field(min_length=1, max_length=512, strict=True)]
EventKind = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
JsonObject = dict[str, JsonValue]


class OperationAttemptRefV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    attempt_no: PositiveCounter
    attempt_id: OpaqueToken


class OperationAttemptRecordV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    ref: OperationAttemptRefV1
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    state: DispatchState
    certainty: OutcomeCertainty
    recovery_reason: ContinuityText | None = None
    created_at_unix_ms: PositiveCounter
    started_at_unix_ms: PositiveCounter | None = None
    ended_at_unix_ms: PositiveCounter | None = None

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if (
            self.started_at_unix_ms is not None
            and self.started_at_unix_ms < self.created_at_unix_ms
        ):
            raise ValueError("attempt started_at_unix_ms cannot precede created_at_unix_ms")
        if self.ended_at_unix_ms is not None:
            lower_bound = self.started_at_unix_ms or self.created_at_unix_ms
            if self.ended_at_unix_ms < lower_bound:
                raise ValueError("attempt ended_at_unix_ms cannot precede its prior timestamp")
        return self


class OperationLeaseRecordV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    attempt: OperationAttemptRefV1
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    lease_epoch: PositiveCounter
    owner_digest: Digest
    acquired_at_unix_ms: PositiveCounter
    heartbeat_at_unix_ms: PositiveCounter
    expires_at_unix_ms: PositiveCounter
    released_at_unix_ms: PositiveCounter | None = None

    @model_validator(mode="after")
    def timestamps_are_ordered(self) -> Self:
        if self.heartbeat_at_unix_ms < self.acquired_at_unix_ms:
            raise ValueError("lease heartbeat_at_unix_ms must be at or after acquired_at_unix_ms")
        if self.expires_at_unix_ms < self.heartbeat_at_unix_ms:
            raise ValueError("lease expires_at_unix_ms must be at or after heartbeat_at_unix_ms")
        if (
            self.released_at_unix_ms is not None
            and self.released_at_unix_ms < self.heartbeat_at_unix_ms
        ):
            raise ValueError("lease released_at_unix_ms must be at or after heartbeat_at_unix_ms")
        return self


class OperationControlStateV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    control_revision: Revision
    cancellation_requested: bool
    requested_at_unix_ms: PositiveCounter | None = None
    requested_by_digest: Digest | None = None
    reason_code: ContinuityText | None = None

    @model_validator(mode="after")
    def cancellation_request_is_bound(self) -> Self:
        if self.cancellation_requested and (
            self.requested_at_unix_ms is None
            or self.requested_by_digest is None
            or self.reason_code is None
        ):
            raise ValueError(
                "a cancellation request requires requested_at_unix_ms, "
                "requested_by_digest, and reason_code"
            )
        return self


class OperationRecoveryPolicyV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    policy_version: PositiveCounter
    policy_id: RecoveryPolicyId = "legacy-safe-replay.v1"
    operation_kind: ContinuityText = "legacy"
    allowed_decisions: tuple[RecoveryDecisionKind, ...] = ("start_successor",)
    max_successor_attempts: Revision = 1
    safe_replay_no_effect: bool
    checkpoint_required: bool
    allow_partial_checkpoint: bool = False
    environment_compatibility: EnvironmentCompatibility = "capability-exact"
    broker_uncertainty_policy: BrokerUncertaintyPolicy = "receipt-or-park"
    cancellation_policy: CancellationPolicy = "block-successor"
    deadline_policy: DeadlinePolicy = "preserve-original"
    effect_reconcile_required: bool

    @model_validator(mode="after")
    def decisions_are_canonical(self) -> Self:
        decisions = list(self.allowed_decisions)
        if decisions != sorted(decisions) or len(decisions) != len(set(decisions)):
            raise ValueError("recovery policy decisions must be sorted and unique")
        if "start_successor" in decisions and self.max_successor_attempts == 0:
            raise ValueError("successor recovery requires a positive successor bound")
        if "start_successor" not in decisions and self.max_successor_attempts != 0:
            raise ValueError("non-successor policy must set max_successor_attempts to zero")
        return self


class OperationRecoveryPolicyBindingV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    operation_kind: ContinuityText
    policy: OperationRecoveryPolicyV1
    policy_digest: Digest
    environment_digest: Digest
    created_at_unix_ms: PositiveCounter

    @classmethod
    def issue(
        cls,
        *,
        operation: OperationRef,
        operation_kind: str,
        policy: OperationRecoveryPolicyV1,
        environment_digest: Digest,
        created_at_unix_ms: int,
    ) -> OperationRecoveryPolicyBindingV1:
        return cls(
            operation=operation,
            operation_kind=operation_kind,
            policy=policy,
            policy_digest=canonical_sha256(policy),
            environment_digest=environment_digest,
            created_at_unix_ms=created_at_unix_ms,
        )

    @model_validator(mode="after")
    def policy_is_content_and_kind_bound(self) -> Self:
        if self.policy.operation_kind != self.operation_kind:
            raise ValueError("recovery policy operation kind does not match binding")
        if self.policy_digest != canonical_sha256(self.policy):
            raise ValueError("recovery policy digest does not match canonical policy bytes")
        return self


class OperationRlmStepBoundaryV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    prior_attempt: OperationAttemptRefV1
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    lease_epoch: PositiveCounter
    policy_digest: Digest
    input_digest: Digest
    strategy: ContinuityText
    strategy_digest: Digest
    capability_digest: Digest
    environment_digest: Digest
    deadline_unix_ms: PositiveCounter
    committed_step_count: Revision
    last_step_index: Revision | None = None
    last_step_digest: Digest | None = None
    steps_digest: Digest
    broker_trace_digest: Digest
    usage_digest: Digest
    created_at_unix_ms: PositiveCounter
    boundary_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        operation: OperationRef,
        prior_attempt: OperationAttemptRefV1,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        policy_digest: Digest,
        input_digest: Digest,
        strategy: str,
        strategy_digest: Digest,
        capability_digest: Digest,
        environment_digest: Digest,
        deadline_unix_ms: int,
        committed_step_count: int,
        last_step_digest: Digest | None,
        steps_digest: Digest,
        broker_trace_digest: Digest,
        usage_digest: Digest,
        created_at_unix_ms: int,
    ) -> OperationRlmStepBoundaryV1:
        last_step_index = committed_step_count - 1 if committed_step_count else None
        payload = {
            "operation": operation,
            "prior_attempt": prior_attempt,
            "runtime_generation": runtime_generation,
            "dispatcher_generation": dispatcher_generation,
            "lease_epoch": lease_epoch,
            "policy_digest": policy_digest,
            "input_digest": input_digest,
            "strategy": strategy,
            "strategy_digest": strategy_digest,
            "capability_digest": capability_digest,
            "environment_digest": environment_digest,
            "deadline_unix_ms": deadline_unix_ms,
            "committed_step_count": committed_step_count,
            "last_step_index": last_step_index,
            "last_step_digest": last_step_digest,
            "steps_digest": steps_digest,
            "broker_trace_digest": broker_trace_digest,
            "usage_digest": usage_digest,
            "created_at_unix_ms": created_at_unix_ms,
        }
        return cls(**payload, boundary_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def boundary_is_exact_and_content_bound(self) -> Self:
        if self.prior_attempt.operation != self.operation:
            raise ValueError("RLM boundary attempt operation must match binding operation")
        if self.committed_step_count == 0:
            if self.last_step_index is not None or self.last_step_digest is not None:
                raise ValueError("empty RLM boundary cannot bind a last step")
        elif (
            self.last_step_index != self.committed_step_count - 1
            or self.last_step_digest is None
        ):
            raise ValueError("RLM boundary must bind its highest committed step")
        payload = self.model_dump(mode="json", exclude={"schema_version", "boundary_digest"})
        if self.boundary_digest != canonical_sha256(payload):
            raise ValueError("RLM boundary digest does not match canonical boundary bytes")
        return self


class OperationCheckpointBindingV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    attempt: OperationAttemptRefV1
    checkpoint: ArtifactReference
    environment_digest: Digest
    created_at_unix_ms: PositiveCounter

    @model_validator(mode="after")
    def attempt_is_bound(self) -> Self:
        if self.attempt.operation != self.operation:
            raise ValueError("checkpoint attempt operation must match binding operation")
        return self


class OperationRecoveryDecisionV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    decision_no: PositiveCounter
    policy_version: PositiveCounter
    prior_attempt: OperationAttemptRefV1 | None = None
    decision: RecoveryDecisionKind
    reason_code: ContinuityText
    input_digest: Digest
    policy_digest: Digest | None = None
    checkpoint_digest: Digest | None = None
    effect_receipt_digest: Digest | None = None
    continuation_boundary_digest: Digest | None = None
    successor_attempt: OperationAttemptRefV1 | None = None
    created_at_unix_ms: PositiveCounter

    @model_validator(mode="after")
    def attempts_are_bound_and_ordered(self) -> Self:
        if self.prior_attempt is not None and self.prior_attempt.operation != self.operation:
            raise ValueError("prior attempt operation must match recovery decision operation")
        if (
            self.successor_attempt is not None
            and self.successor_attempt.operation != self.operation
        ):
            raise ValueError("successor attempt operation must match recovery decision operation")
        if (
            self.prior_attempt is not None
            and self.successor_attempt is not None
            and self.successor_attempt.attempt_no <= self.prior_attempt.attempt_no
        ):
            raise ValueError(
                "successor attempt number must be greater than prior attempt number"
            )
        if (
            self.decision == "start_successor"
            and (self.policy_digest is None) != (self.continuation_boundary_digest is None)
        ):
            raise ValueError(
                "policy and continuation boundary digests must be supplied together"
            )
        return self


class OperationEventEnvelopeV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    sequence: PositiveCounter
    operation: OperationRef
    attempt: OperationAttemptRefV1 | None = None
    event_kind: EventKind
    state: OperationState
    certainty: OutcomeCertainty
    record_revision: Revision
    at_unix_ms: PositiveCounter
    payload: JsonObject
    payload_digest: Digest

    @model_validator(mode="after")
    def payload_is_content_bound(self) -> Self:
        if self.attempt is not None and self.attempt.operation != self.operation:
            raise ValueError("event attempt operation must match event operation")
        try:
            expected_digest = canonical_sha256(self.payload)
        except (TypeError, ValueError) as error:
            raise ValueError("event payload must be canonical JSON") from error
        if self.payload_digest != expected_digest:
            raise ValueError("event payload digest does not match canonical payload bytes")
        return self


class OperationContinuitySnapshotV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    operation_state: OperationState
    certainty: OutcomeCertainty
    record_revision: Revision
    current_attempt: OperationAttemptRefV1 | None = None
    last_attempt: OperationAttemptRefV1 | None = None
    control: OperationControlStateV1
    dispatcher_state: DispatchState | None = None
    last_event_sequence: Revision
    reconciliation_required: bool
    recovery_reason: ContinuityText | None = None

    @model_validator(mode="after")
    def references_are_bound_and_ordered(self) -> Self:
        if self.control.operation != self.operation:
            raise ValueError("snapshot control operation must match snapshot operation")
        if self.current_attempt is not None and self.current_attempt.operation != self.operation:
            raise ValueError("current attempt operation must match snapshot operation")
        if self.last_attempt is not None and self.last_attempt.operation != self.operation:
            raise ValueError("last attempt operation must match snapshot operation")
        if (
            self.current_attempt is not None
            and self.last_attempt is not None
            and self.current_attempt.attempt_no < self.last_attempt.attempt_no
        ):
            raise ValueError("current attempt number cannot precede last attempt number")
        return self


class OperationEventPageV1(StrictModel):
    schema_version: Literal["aar.operation-continuity.v1"] = CONTINUITY_SCHEMA_VERSION
    operation: OperationRef
    after_sequence: Revision
    events: tuple[OperationEventEnvelopeV1, ...] = ()
    next_sequence: Revision
    has_more: bool
    terminal_snapshot: OperationContinuitySnapshotV1 | None = None

    @model_validator(mode="after")
    def events_are_ordered_and_cursor_bound(self) -> Self:
        sequences = [event.sequence for event in self.events]
        if any(event.operation != self.operation for event in self.events):
            raise ValueError("event operation must match page operation")
        if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
            raise ValueError("page events must be ordered by unique sequence")
        if sequences and sequences[0] <= self.after_sequence:
            raise ValueError("page events must be after the supplied cursor")
        expected_next_sequence = sequences[-1] if sequences else self.after_sequence
        if self.next_sequence != expected_next_sequence:
            raise ValueError("page next_sequence must equal the page cursor")
        if (
            self.terminal_snapshot is not None
            and self.terminal_snapshot.operation != self.operation
        ):
            raise ValueError("terminal snapshot operation must match page operation")
        return self


CONTINUITY_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "operation_attempt_ref": OperationAttemptRefV1,
    "operation_attempt_record": OperationAttemptRecordV1,
    "operation_lease_record": OperationLeaseRecordV1,
    "operation_control_state": OperationControlStateV1,
    "operation_recovery_policy": OperationRecoveryPolicyV1,
    "operation_recovery_policy_binding": OperationRecoveryPolicyBindingV1,
    "operation_rlm_step_boundary": OperationRlmStepBoundaryV1,
    "operation_checkpoint_binding": OperationCheckpointBindingV1,
    "operation_recovery_decision": OperationRecoveryDecisionV1,
    "operation_event_envelope": OperationEventEnvelopeV1,
    "operation_continuity_snapshot": OperationContinuitySnapshotV1,
    "operation_event_page": OperationEventPageV1,
}
