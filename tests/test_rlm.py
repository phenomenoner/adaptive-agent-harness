from __future__ import annotations

import json
from pathlib import Path

import pytest

from aar.canonical import canonical_json_bytes
from aar.rlm_models import RlmJobSpec, RlmResult
from aar.runtime.reference_host import GrantDenied, ReferenceHost
from aar.runtime.registry import StaleRuntimeGeneration
from aar.runtime.rlm import SimulatedRlmProcessLoss
from aar.schemas import (
    Budget,
    FailureCategory,
    OperationState,
    PrincipalRef,
    SessionRef,
)


class ManualClock:
    def __init__(self, now: int = 1_700_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


def prepare(
    host: ReferenceHost,
    clock: ManualClock,
    *,
    strategy: str = "baseline",
    idempotency_key: str = "idem-rlm-0001",
    budget: Budget | None = None,
):
    max_steps = 1 if strategy == "baseline" else 2
    spec = RlmJobSpec(query="portable runtime", strategy=strategy, max_steps=max_steps)
    envelope = host.request_rlm_envelope(
        request_id=f"request-{idempotency_key}",
        idempotency_key=idempotency_key,
        principal=PrincipalRef(value="principal-rlm"),
        session=SessionRef(value="session-rlm"),
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
        budget=budget,
    )
    return spec, envelope


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def host(tmp_path: Path, clock: ManualClock) -> ReferenceHost:
    instance = ReferenceHost(
        tmp_path / "rlm.sqlite3", now_ms=clock, programmable_backend="plain"
    )
    yield instance
    instance.close()


def test_baseline_rlm_persists_bounded_trace(host: ReferenceHost, clock: ManualClock) -> None:
    spec, envelope = prepare(host, clock)
    record = host.execute_rlm(envelope, spec)

    assert record.state is OperationState.SUCCEEDED
    result = RlmResult.model_validate_json(record.result_json or "null", strict=True)
    assert result.strategy == "baseline"
    assert len(result.steps) == 1
    assert result.steps[0].action == "model.request"
    assert result.usage.model_requests == 1
    assert [call.method for call in result.broker_trace] == ["model.request"]
    assert host.rlm_status(record.operation).result == result
    assert [event.state for event in host.registry.events(record.operation)] == [
        OperationState.ACCEPTED,
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
    ]


def test_evidence_strategy_discloses_and_uses_only_its_broker_slice(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(host, clock, strategy="evidence_synthesis")
    record = host.execute_rlm(envelope, spec)
    result = RlmResult.model_validate_json(record.result_json or "null", strict=True)

    assert [step.action for step in result.steps] == [
        "evidence.query",
        "model.request",
    ]
    catalog = host.brokers.bind(envelope, record.operation).catalog()
    assert [method.name for method in catalog.methods] == [
        "evidence.query",
        "model.request",
    ]
    assert result.usage.model_requests == 1


def test_missing_strategy_grant_is_rejected_before_acceptance(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(host, clock, strategy="evidence_synthesis")
    missing = envelope.model_copy(
        update={
            "grants": tuple(
                grant for grant in envelope.grants if grant.capability != "evidence.query"
            )
        }
    )

    with pytest.raises(GrantDenied):
        host.submit_rlm(missing, spec)


def test_broker_budget_exhaustion_is_a_classified_terminal_failure(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(
        host,
        clock,
        budget=Budget(wall_time_ms=10_000),
    )
    record = host.execute_rlm(envelope, spec)

    assert record.state is OperationState.FAILED
    assert record.failure is not None
    assert record.failure.category is FailureCategory.BUDGET
    assert record.failure.code == "BROKER_BUDGET_EXCEEDED"
    assert host.rlm_status(record.operation).state is OperationState.FAILED


def test_cancellation_before_run_is_terminal_for_outer_and_rlm_state(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(host, clock)
    accepted = host.submit_rlm(envelope, spec)

    cancelled = host.cancel(accepted.operation)
    assert cancelled.state is OperationState.CANCELLED
    assert host.rlm_status(accepted.operation).state is OperationState.CANCELLED
    assert host.run_rlm(accepted.operation).state is OperationState.CANCELLED


def test_stale_runtime_generation_is_rejected_before_rlm_acceptance(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(host, clock)
    stale = envelope.model_copy(
        update={"runtime_generation": host.runtime_generation + 1}
    )
    with pytest.raises(StaleRuntimeGeneration):
        host.submit_rlm(stale, spec)


def test_restart_reconciles_persisted_terminal_rlm_receipt_without_reexecution(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "rlm-restart-terminal.sqlite3"
    first = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    spec, envelope = prepare(first, clock)
    accepted = first.submit_rlm(envelope, spec)
    with pytest.raises(SimulatedRlmProcessLoss):
        first.run_rlm(
            accepted.operation, failpoint="process_loss_after_terminal_receipt"
        )
    assert first.status(accepted.operation).state is OperationState.RUNNING
    assert first.rlm.store.terminal_result(accepted.operation) is not None
    first.close()

    second = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    try:
        recovered = second.status(accepted.operation)
        assert recovered.state is OperationState.INDETERMINATE
        assert second.reconcile_rlm(accepted.operation).state is OperationState.SUCCEEDED
        result = RlmResult.model_validate_json(
            second.status(accepted.operation).result_json or "null", strict=True
        )
        assert len(result.broker_trace) == 1
        assert len(second.brokers.bind(envelope, accepted.operation).traces()) == 1
    finally:
        second.close()


def test_restart_without_terminal_receipt_remains_indeterminate(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "rlm-restart-uncertain.sqlite3"
    first = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    spec, envelope = prepare(first, clock)
    accepted = first.submit_rlm(envelope, spec)
    with pytest.raises(SimulatedRlmProcessLoss):
        first.run_rlm(accepted.operation, failpoint="process_loss_before_first_broker")
    first.close()

    second = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    try:
        report = second.reconcile_rlm(accepted.operation)
        assert report.state is OperationState.INDETERMINATE
        assert report.reconciliation_required
        assert second.rlm.store.terminal_result(accepted.operation) is None
        assert second.brokers.bind(envelope, accepted.operation).traces() == ()
    finally:
        second.close()


def test_restart_rebuilds_missing_rlm_store_after_outer_accept(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "rlm-restart-before-store.sqlite3"
    first = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    spec, envelope = prepare(first, clock)
    accepted, created = first.registry.accept(
        envelope, canonical_json_bytes(spec).decode()
    )
    assert created
    assert not first.rlm.store.exists(accepted.operation)
    first.close()

    second = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    try:
        completed = second.run_rlm(accepted.operation)
        assert completed.state is OperationState.SUCCEEDED
        assert second.rlm.store.exists(accepted.operation)
        result = RlmResult.model_validate_json(
            completed.result_json or "null", strict=True
        )
        assert len(result.steps) == 1
    finally:
        second.close()


def test_restart_reconcile_rebuilds_missing_store_but_preserves_uncertainty(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "rlm-restart-running-before-store.sqlite3"
    first = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    spec, envelope = prepare(first, clock)
    accepted, _created = first.registry.accept(
        envelope, canonical_json_bytes(spec).decode()
    )
    first.registry.begin(accepted.operation, first.runtime_generation)
    assert not first.rlm.store.exists(accepted.operation)
    first.close()

    second = ReferenceHost(database, now_ms=clock, programmable_backend="plain")
    try:
        report = second.reconcile_rlm(accepted.operation)
        assert second.rlm.store.exists(accepted.operation)
        assert report.state is OperationState.INDETERMINATE
        assert report.reconciliation_required
        assert second.rlm.store.terminal_result(accepted.operation) is None
    finally:
        second.close()


def test_deadline_after_acceptance_times_out_without_broker_call(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec, envelope = prepare(host, clock)
    accepted = host.submit_rlm(envelope, spec)
    clock.now = envelope.deadline_unix_ms

    record = host.run_rlm(accepted.operation)
    assert record.state is OperationState.TIMED_OUT
    assert host.brokers.bind(envelope, accepted.operation).traces() == ()
    assert json.loads(record.result_json or "null") is None
