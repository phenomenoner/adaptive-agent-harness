from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from aar.broker_models import EvidenceQuery
from aar.canonical import canonical_sha256
from aar.rlm_models import RlmJobSpec, RlmResult
from aar.runtime.continuity import (
    RLM_OPERATION_KIND,
    rlm_recovery_environment_digest,
    rlm_step_boundary_policy,
)
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import StaleAttemptFence
from aar.runtime.rlm import SimulatedRlmProcessLoss
from aar.schemas import Budget, OperationState, PrincipalRef, SessionRef

NOW_MS = 1_700_000_000_000


class SimulatedEffectProcessLoss(BaseException):
    pass


def open_host(database: Path, *, durable: bool) -> ReferenceHost:
    return ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=durable,
        dispatcher_concurrency=1,
    )


def prepare(host: ReferenceHost, suffix: str) -> tuple[RlmJobSpec, Any]:
    spec = RlmJobSpec(
        query=f"durable RLM {suffix}",
        strategy="baseline",
        max_steps=1,
    )
    envelope = host.request_rlm_envelope(
        request_id=f"request-durable-{suffix}",
        idempotency_key=f"idempotency-durable-{suffix}",
        principal=PrincipalRef(value="principal-durable"),
        session=SessionRef(value="session-durable"),
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=1_024,
            output_tokens=128,
        ),
    )
    return spec, envelope


def seed_claimed(
    host: ReferenceHost,
    suffix: str,
) -> tuple[Any, Any, Any, str]:
    spec, envelope = prepare(host, suffix)
    accepted = host.submit_rlm(envelope, spec)
    policy = rlm_step_boundary_policy()
    host.registry.bind_recovery_policy(
        accepted.operation,
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=host.capabilities.digest,
            policy=policy,
        ),
    )
    host.registry.request_dispatch(accepted.operation)
    owner = canonical_sha256({"owner": suffix})
    claim = host.registry.claim_next(
        host.runtime_generation,
        host.runtime_generation,
        owner,
        30_000,
    )
    assert claim is not None
    return accepted, envelope, claim, owner


def test_durable_rlm_completes_with_reconnectable_events_and_result(tmp_path: Path) -> None:
    host = open_host(tmp_path / "normal.sqlite3", durable=True)
    try:
        spec, envelope = prepare(host, "normal")
        accepted = host.submit_rlm_durable(envelope, spec)
        completed = host.wait_rlm(accepted.operation, timeout_s=2)

        assert completed.operation == accepted.operation
        assert completed.state is OperationState.SUCCEEDED
        result = RlmResult.model_validate_json(completed.result_json or "null", strict=True)
        assert len(result.steps) == 1
        snapshot = host.registry.continuity_snapshot(accepted.operation)
        assert snapshot.operation_state is OperationState.SUCCEEDED
        assert snapshot.dispatcher_state == "completed"
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 1
        page = host.registry.event_page(accepted.operation, limit=2)
        sequences = [event.sequence for event in page.events]
        while page.has_more:
            page = host.registry.event_page(
                accepted.operation,
                after_sequence=page.next_sequence,
                limit=2,
            )
            sequences.extend(event.sequence for event in page.events)
        assert sequences == sorted(set(sequences))
        assert page.terminal_snapshot is not None
        assert page.terminal_snapshot.operation_state is OperationState.SUCCEEDED
    finally:
        host.close()


def test_running_cancel_is_durable_and_observed_at_rlm_boundary(tmp_path: Path) -> None:
    host = open_host(tmp_path / "cancel.sqlite3", durable=True)
    entered = threading.Event()
    release = threading.Event()
    original_request = host.models.request

    def blocking_request(prompt: str, context: Any) -> Any:
        entered.set()
        assert release.wait(2)
        return original_request(prompt, context)

    host.models.request = blocking_request  # type: ignore[method-assign]
    try:
        spec, envelope = prepare(host, "cancel")
        accepted = host.submit_rlm_durable(envelope, spec)
        assert entered.wait(2)
        requested = host.cancel(
            accepted.operation,
            requested_by_digest=canonical_sha256({"actor": "test"}),
            reason_code="test_cancel",
        )
        assert requested.state is OperationState.RUNNING
        assert host.registry.cancellation_requested(accepted.operation)
        release.set()
        cancelled = host.wait_rlm(accepted.operation, timeout_s=2)
        assert cancelled.state is OperationState.CANCELLED
        assert host.rlm_status(accepted.operation).state is OperationState.CANCELLED
        kinds = [event.event_kind for event in host.registry.event_page(
            accepted.operation, limit=100
        ).events]
        assert "cancel_requested" in kinds
        assert "cancelled" in kinds
    finally:
        release.set()
        host.close()


def test_restart_fences_attempt_one_and_runs_successor_attempt(tmp_path: Path) -> None:
    database = tmp_path / "successor.sqlite3"
    first = open_host(database, durable=False)
    accepted, _envelope, claim, owner = seed_claimed(first, "successor")
    assert claim.attempt.attempt_no == 1
    first.close()

    second = open_host(database, durable=True)
    try:
        completed = second.wait_rlm(accepted.operation, timeout_s=2)
        assert completed.operation == accepted.operation
        assert completed.state is OperationState.SUCCEEDED
        snapshot = second.registry.continuity_snapshot(accepted.operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == ["start_successor"]
        assert decisions[0].policy_digest is not None
        assert decisions[0].continuation_boundary_digest is not None
        boundaries = second.registry.rlm_step_boundaries(accepted.operation)
        assert len(boundaries) == 1
        assert boundaries[0].prior_attempt == claim.attempt
        with pytest.raises(StaleAttemptFence):
            second.registry.heartbeat(
                claim.attempt,
                first.runtime_generation,
                first.runtime_generation,
                claim.lease_epoch,
                owner,
                30_000,
            )
    finally:
        second.close()


def test_restart_reuses_broker_receipt_after_crash_gap(tmp_path: Path) -> None:
    database = tmp_path / "receipt-reuse.sqlite3"
    first = open_host(database, durable=False)
    accepted, envelope, _claim, _owner = seed_claimed(first, "receipt-reuse")
    with pytest.raises(SimulatedRlmProcessLoss):
        first.rlm.run(
            accepted.operation,
            envelope,
            failpoint="process_loss_after_broker_receipt",
        )
    assert len(first.brokers.bind(envelope, accepted.operation).traces()) == 1
    first.close()

    second = open_host(database, durable=True)
    try:
        completed = second.wait_rlm(accepted.operation, timeout_s=2)
        assert completed.state is OperationState.SUCCEEDED
        result = RlmResult.model_validate_json(completed.result_json or "null", strict=True)
        assert len(result.steps) == 1
        assert len(second.brokers.bind(envelope, accepted.operation).traces()) == 1
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == ["start_successor"]
        assert decisions[0].reason_code == "rlm_certain_step_boundary"
        assert decisions[0].continuation_boundary_digest is not None
        boundaries = second.registry.rlm_step_boundaries(accepted.operation)
        assert len(boundaries) == 1
        assert boundaries[0].committed_step_count == 0
        snapshot = second.registry.continuity_snapshot(accepted.operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
    finally:
        second.close()


def test_restart_commits_terminal_receipt_without_successor(tmp_path: Path) -> None:
    database = tmp_path / "terminal-receipt.sqlite3"
    first = open_host(database, durable=False)
    accepted, envelope, _claim, _owner = seed_claimed(first, "terminal-receipt")
    with pytest.raises(SimulatedRlmProcessLoss):
        first.rlm.run(
            accepted.operation,
            envelope,
            failpoint="process_loss_after_terminal_receipt",
        )
    assert first.rlm.store.terminal_result(accepted.operation) is not None
    first.close()

    second = open_host(database, durable=True)
    try:
        recovered = second.status(accepted.operation)
        assert recovered.state is OperationState.SUCCEEDED
        snapshot = second.registry.continuity_snapshot(accepted.operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 1
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == [
            "terminal_from_receipt"
        ]
    finally:
        second.close()


def test_restart_leaves_unresolved_broker_call_indeterminate(tmp_path: Path) -> None:
    database = tmp_path / "unresolved.sqlite3"
    first = open_host(database, durable=False)
    accepted, envelope, _claim, _owner = seed_claimed(first, "unresolved")

    def abrupt_loss(_prompt: str, _context: Any) -> Any:
        raise SimulatedRlmProcessLoss()

    first.models.request = abrupt_loss  # type: ignore[method-assign]
    with pytest.raises(SimulatedRlmProcessLoss):
        first.rlm.run(accepted.operation, envelope)
    assert first.brokers.has_unresolved_calls(accepted.operation)
    first.close()

    second = open_host(database, durable=True)
    try:
        time.sleep(0.05)
        parked = second.status(accepted.operation)
        assert parked.state is OperationState.INDETERMINATE
        assert parked.reconciliation_required
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == ["reconcile_effect"]
        assert decisions[0].reason_code == "broker_call_unresolved"
        assert decisions[0].policy_digest is not None
        assert decisions[0].continuation_boundary_digest is None
        assert second.brokers.has_unresolved_calls(accepted.operation)
    finally:
        second.close()


def test_capability_drift_parks_rlm_recovery_without_aborting_startup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "capability-drift.sqlite3"
    first = open_host(database, durable=False)
    accepted, envelope, _claim, _owner = seed_claimed(first, "capability-drift")

    def abrupt_loss(_prompt: str, _context: Any) -> Any:
        raise SimulatedRlmProcessLoss()

    first.models.request = abrupt_loss  # type: ignore[method-assign]
    with pytest.raises(SimulatedRlmProcessLoss):
        first.rlm.run(accepted.operation, envelope)
    first.close()

    second = open_host(database, durable=False)
    provider_calls = 0

    def count_provider(_prompt: str, _context: Any) -> Any:
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider must not run after capability drift")

    second.models.request = count_provider  # type: ignore[method-assign]
    second.capabilities = second.capabilities.model_copy(
        update={"digest": canonical_sha256({"different": "capability"})}
    )
    try:
        dispatcher = second.start_durable_dispatch()
        assert dispatcher is second.dispatcher
        assert provider_calls == 0
        parked = second.status(accepted.operation)
        assert parked.state is OperationState.INDETERMINATE
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == ["needs_user"]
        assert decisions[0].reason_code == "recovery_capability_digest_mismatch"
    finally:
        second.close()


def test_effect_prepass_recovers_safe_receipt_before_one_rlm_successor(
    tmp_path: Path,
) -> None:
    database = tmp_path / "effect-prepass.sqlite3"
    first = ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=False,
        dispatcher_concurrency=1,
        evidence_records=("recoverable evidence",),
    )
    spec = RlmJobSpec(
        query="recoverable",
        strategy="evidence_synthesis",
        max_steps=2,
    )
    envelope = first.request_rlm_envelope(
        request_id="request-durable-effect-prepass",
        idempotency_key="idempotency-durable-effect-prepass",
        principal=PrincipalRef(value="principal-durable"),
        session=SessionRef(value="session-durable"),
        spec=spec,
        deadline_unix_ms=NOW_MS + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=1_024,
            output_tokens=128,
        ),
    )
    accepted = first.submit_rlm(envelope, spec)
    policy = rlm_step_boundary_policy()
    first.registry.bind_recovery_policy(
        accepted.operation,
        operation_kind=RLM_OPERATION_KIND,
        policy=policy,
        environment_digest=rlm_recovery_environment_digest(
            spec=spec,
            capability_digest=first.capabilities.digest,
            policy=policy,
        ),
    )
    first.registry.request_dispatch(accepted.operation)
    owner = canonical_sha256({"owner": "effect-prepass"})
    claim = first.registry.claim_next(
        first.runtime_generation,
        first.runtime_generation,
        owner,
        30_000,
    )
    assert claim is not None
    def lose_evidence(_request: EvidenceQuery, _context: Any) -> Any:
        raise SimulatedEffectProcessLoss()

    first.evidence.query = lose_evidence  # type: ignore[method-assign]
    with pytest.raises(SimulatedEffectProcessLoss):
        first.rlm.run(accepted.operation, envelope)
    first.registry.transition_claimed(
        claim.attempt,
        first.runtime_generation,
        claim.dispatcher_generation,
        claim.lease_epoch,
        owner,
        state=OperationState.INDETERMINATE,
        note="effect_process_lost",
    )
    first.close()

    second = ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
        evidence_records=("recoverable evidence",),
    )
    try:
        completed = second.wait_rlm(accepted.operation, timeout_s=2)
        assert completed.state is OperationState.SUCCEEDED
        snapshot = second.registry.continuity_snapshot(accepted.operation)
        assert snapshot.last_attempt is not None
        assert snapshot.last_attempt.attempt_no == 2
        traces = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            accepted.operation,
        ).traces()
        assert traces[0].method == "evidence.query"
        assert traces[0].state == "succeeded"
        assert traces[0].reconciliation_action == "safe_replay"
        decisions = second.registry.recovery_decisions(accepted.operation)
        assert [decision.decision for decision in decisions] == ["start_successor"]
    finally:
        second.close()
