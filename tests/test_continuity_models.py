from __future__ import annotations

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.continuity_models import (
    CONTINUITY_SCHEMA_MODELS,
    JsonObject,
    OperationAttemptRecordV1,
    OperationAttemptRefV1,
    OperationCheckpointBindingV1,
    OperationContinuitySnapshotV1,
    OperationControlStateV1,
    OperationEventEnvelopeV1,
    OperationEventPageV1,
    OperationLeaseRecordV1,
    OperationRecoveryDecisionV1,
    OperationRecoveryPolicyBindingV1,
    OperationRecoveryPolicyV1,
    OperationRlmStepBoundaryV1,
)
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    StrictModel,
)

DIGEST = canonical_sha256({"fixture": "continuity"})
OPERATION = OperationRef(value="operation-1")
OTHER_OPERATION = OperationRef(value="operation-2")


def attempt(attempt_no: int, *, operation: OperationRef = OPERATION) -> OperationAttemptRefV1:
    return OperationAttemptRefV1(
        operation=operation,
        attempt_no=attempt_no,
        attempt_id=f"attempt-{attempt_no}",
    )


def event(
    sequence: int,
    *,
    operation: OperationRef = OPERATION,
    payload: JsonObject | None = None,
    payload_digest: str | None = None,
) -> OperationEventEnvelopeV1:
    event_payload: JsonObject = {"sequence": sequence} if payload is None else payload
    return OperationEventEnvelopeV1(
        sequence=sequence,
        operation=operation,
        attempt=attempt(1, operation=operation),
        event_kind="attempt.updated",
        state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=sequence,
        at_unix_ms=100 + sequence,
        payload=event_payload,
        payload_digest=(
            canonical_sha256(event_payload) if payload_digest is None else payload_digest
        ),
    )


def valid_models() -> tuple[StrictModel, ...]:
    policy = OperationRecoveryPolicyV1(
        policy_version=1,
        policy_id="rlm.step-boundary.v1",
        operation_kind="rlm.execute",
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
        effect_reconcile_required=True,
    )
    policy_binding = OperationRecoveryPolicyBindingV1.issue(
        operation=OPERATION,
        operation_kind="rlm.execute",
        policy=policy,
        environment_digest=DIGEST,
        created_at_unix_ms=120,
    )
    boundary = OperationRlmStepBoundaryV1.issue(
        operation=OPERATION,
        prior_attempt=attempt(1),
        runtime_generation=1,
        dispatcher_generation=2,
        lease_epoch=1,
        policy_digest=policy_binding.policy_digest,
        input_digest=DIGEST,
        strategy="baseline",
        strategy_digest=DIGEST,
        capability_digest=DIGEST,
        environment_digest=DIGEST,
        deadline_unix_ms=1_000,
        committed_step_count=0,
        last_step_digest=None,
        steps_digest=DIGEST,
        broker_trace_digest=DIGEST,
        usage_digest=DIGEST,
        created_at_unix_ms=130,
    )
    control = OperationControlStateV1(
        operation=OPERATION,
        control_revision=1,
        cancellation_requested=True,
        requested_at_unix_ms=125,
        requested_by_digest=DIGEST,
        reason_code="user_requested",
    )
    snapshot = OperationContinuitySnapshotV1(
        operation=OPERATION,
        operation_state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=2,
        current_attempt=attempt(2),
        last_attempt=attempt(1),
        control=control,
        dispatcher_state="running",
        last_event_sequence=2,
        reconciliation_required=True,
        recovery_reason="lease_expired",
    )
    return (
        attempt(1),
        OperationAttemptRecordV1(
            ref=attempt(1),
            runtime_generation=1,
            dispatcher_generation=2,
            state="completed",
            certainty=OutcomeCertainty.CERTAIN,
            recovery_reason="recovered",
            created_at_unix_ms=100,
            started_at_unix_ms=110,
            ended_at_unix_ms=120,
        ),
        OperationLeaseRecordV1(
            attempt=attempt(1),
            runtime_generation=1,
            dispatcher_generation=2,
            lease_epoch=1,
            owner_digest=DIGEST,
            acquired_at_unix_ms=100,
            heartbeat_at_unix_ms=110,
            expires_at_unix_ms=130,
            released_at_unix_ms=140,
        ),
        control,
        policy,
        policy_binding,
        boundary,
        OperationCheckpointBindingV1(
            operation=OPERATION,
            attempt=attempt(1),
            checkpoint=ArtifactReference(
                artifact=ArtifactIdRef(value="checkpoint-1"),
                digest=DIGEST,
                media_type="application/json",
                size_bytes=128,
                created_by=OPERATION,
                redacted=False,
            ),
            environment_digest=DIGEST,
            created_at_unix_ms=145,
        ),
        OperationRecoveryDecisionV1(
            operation=OPERATION,
            decision_no=1,
            policy_version=1,
            prior_attempt=attempt(1),
            decision="start_successor",
            reason_code="lease_expired",
            input_digest=DIGEST,
            policy_digest=policy_binding.policy_digest,
            continuation_boundary_digest=boundary.boundary_digest,
            successor_attempt=attempt(2),
            created_at_unix_ms=150,
        ),
        event(1),
        snapshot,
        OperationEventPageV1(
            operation=OPERATION,
            after_sequence=0,
            events=(event(1), event(2)),
            next_sequence=2,
            has_more=False,
            terminal_snapshot=snapshot,
        ),
    )


def test_continuity_models_round_trip_and_are_registered() -> None:
    expected_names = {
        "operation_attempt_ref",
        "operation_attempt_record",
        "operation_lease_record",
        "operation_control_state",
        "operation_recovery_policy",
        "operation_recovery_policy_binding",
        "operation_rlm_step_boundary",
        "operation_checkpoint_binding",
        "operation_recovery_decision",
        "operation_event_envelope",
        "operation_continuity_snapshot",
        "operation_event_page",
    }
    assert set(CONTINUITY_SCHEMA_MODELS) == expected_names

    for model in valid_models():
        restored = type(model).model_validate_json(model.model_dump_json(), strict=True)
        assert restored == model
        assert restored.model_dump(mode="json")["schema_version"] == "aar.operation-continuity.v1"


def test_event_rejects_wrong_payload_digest() -> None:
    with pytest.raises(ValidationError, match="payload digest"):
        event(
            1,
            payload={"sequence": 1, "status": "tampered"},
            payload_digest=DIGEST,
        )


def test_event_page_rejects_unordered_or_wrong_operation_events() -> None:
    with pytest.raises(ValidationError, match="ordered"):
        OperationEventPageV1(
            operation=OPERATION,
            after_sequence=0,
            events=(event(2), event(1)),
            next_sequence=1,
            has_more=False,
        )

    with pytest.raises(ValidationError, match="operation"):
        OperationEventPageV1(
            operation=OPERATION,
            after_sequence=0,
            events=(event(1, operation=OTHER_OPERATION),),
            next_sequence=1,
            has_more=False,
        )


def test_lease_timestamps_are_ordered() -> None:
    base = {
        "attempt": attempt(1),
        "runtime_generation": 1,
        "dispatcher_generation": 1,
        "lease_epoch": 1,
        "owner_digest": DIGEST,
        "acquired_at_unix_ms": 100,
        "heartbeat_at_unix_ms": 110,
        "expires_at_unix_ms": 130,
    }
    with pytest.raises(ValidationError, match="heartbeat"):
        OperationLeaseRecordV1(**{**base, "heartbeat_at_unix_ms": 99})
    with pytest.raises(ValidationError, match="expires"):
        OperationLeaseRecordV1(**{**base, "expires_at_unix_ms": 109})
    with pytest.raises(ValidationError, match="released"):
        OperationLeaseRecordV1(**{**base, "released_at_unix_ms": 109})


def test_recovery_decision_requires_successor_attempt_to_advance() -> None:
    with pytest.raises(ValidationError, match="successor attempt"):
        OperationRecoveryDecisionV1(
            operation=OPERATION,
            decision_no=1,
            policy_version=1,
            prior_attempt=attempt(2),
            decision="start_successor",
            reason_code="lease_expired",
            input_digest=DIGEST,
            successor_attempt=attempt(2),
            created_at_unix_ms=150,
        )


def test_continuity_models_reject_unknown_fields() -> None:
    document = event(1).model_dump(mode="json")
    document["unexpected"] = True
    with pytest.raises(ValidationError, match="unexpected"):
        OperationEventEnvelopeV1.model_validate(document, strict=True)
