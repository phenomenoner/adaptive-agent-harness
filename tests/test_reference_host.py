from __future__ import annotations

import json
from pathlib import Path
from threading import Event, Thread

import pytest

from aar.canonical import canonical_sha256
from aar.rlm_models import RlmJobSpec
from aar.runtime.brokers import BrokerContext
from aar.runtime.dispatcher import DispatcherDrainTimeout
from aar.runtime.models import WorkspaceExecuteSpec
from aar.runtime.reference_host import (
    BudgetDenied,
    CapabilityMismatch,
    DeadlineExpired,
    GrantDenied,
    InputDigestMismatch,
    ReferenceHost,
    ReferenceHostError,
    SimulatedProcessLoss,
    WorkspaceBindingDenied,
)
from aar.runtime.registry import IdempotencyConflict, StaleRuntimeGeneration
from aar.runtime.workspace import SimulatedWorkspaceTransactionLoss
from aar.schemas import Budget, OperationRef, OperationState, PrincipalRef, SessionRef, WorkspaceRef


class ManualClock:
    def __init__(self, now: int = 1_700_000_000_000) -> None:
        self.now = now

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def host(tmp_path: Path, clock: ManualClock) -> ReferenceHost:
    instance = ReferenceHost(tmp_path / "runtime.sqlite3", now_ms=clock)
    yield instance
    instance.close()


def prepare(
    host: ReferenceHost,
    clock: ManualClock,
    *,
    key: str = "answer",
    value: int = 42,
    idempotency_key: str = "idem-reference-0001",
):
    principal = PrincipalRef(value="principal-test")
    session = SessionRef(value="session-test")
    handle = host.create_workspace(WorkspaceRef(value="workspace-test"), session)
    spec = WorkspaceExecuteSpec(
        workspace=handle.workspace,
        expected_generation=handle.generation,
        expected_revision=handle.revision,
        action="set",
        key=key,
        value=value,
    )
    envelope = host.request_envelope(
        request_id=f"request-{idempotency_key}",
        idempotency_key=idempotency_key,
        principal=principal,
        session=session,
        workspace=handle,
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
    )
    return handle, spec, envelope


def test_execute_persists_progress_and_inspect_round_trip(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock)
    record = host.execute(envelope, spec)

    assert record.state is OperationState.SUCCEEDED
    assert json.loads(record.result_json or "null")["revision_after"] == 1
    events = host.registry.events(record.operation)
    assert [event.state for event in events] == [
        OperationState.ACCEPTED,
        OperationState.RUNNING,
        OperationState.SUCCEEDED,
    ]
    current = host.workspace.current_handle(handle.workspace)
    assert host.inspect(current).values == (("answer", 42),)


def test_ready_returns_exact_generation_and_content_bound_capabilities(
    host: ReferenceHost,
) -> None:
    ready = host.ready()
    assert ready.runtime_generation == host.runtime_generation
    assert ready.capabilities.digest == host.capabilities.digest
    assert [item.name for item in ready.capabilities.capabilities] == sorted(
        item.name for item in ready.capabilities.capabilities
    )


def test_same_idempotency_key_returns_prior_state_without_reexecution(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock)
    first = host.execute(envelope, spec)
    second = host.execute(envelope, spec)
    assert second == first
    assert host.workspace.current_handle(handle.workspace).revision == 1


def test_same_idempotency_key_with_different_digest_conflicts(
    host: ReferenceHost, clock: ManualClock
) -> None:
    _handle, spec, envelope = prepare(host, clock)
    host.submit_execute(envelope, spec)
    changed = envelope.model_copy(update={"input_digest": f"sha256:{'0' * 64}"})
    with pytest.raises(InputDigestMismatch):
        host.submit_execute(changed, spec)

    changed_spec = spec.model_copy(update={"value": 7})
    changed_envelope = changed.model_copy(update={"input_digest": canonical_sha256(changed_spec)})
    with pytest.raises(IdempotencyConflict):
        host.submit_execute(changed_envelope, changed_spec)


def test_cancel_before_run_is_terminal(host: ReferenceHost, clock: ManualClock) -> None:
    _handle, spec, envelope = prepare(host, clock)
    accepted = host.submit_execute(envelope, spec)
    cancelled = host.cancel(accepted.operation)
    assert cancelled.state is OperationState.CANCELLED
    assert host.run_execute(accepted.operation).state is OperationState.CANCELLED


def test_deadline_after_acceptance_times_out_without_workspace_mutation(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock)
    accepted = host.submit_execute(envelope, spec)
    clock.now = envelope.deadline_unix_ms
    timed_out = host.run_execute(accepted.operation)
    assert timed_out.state is OperationState.TIMED_OUT
    assert host.workspace.current_handle(handle.workspace).revision == 0


def test_stale_workspace_revision_fails_closed(host: ReferenceHost, clock: ManualClock) -> None:
    handle, first_spec, first_envelope = prepare(host, clock)
    host.execute(first_envelope, first_spec)
    stale_spec = WorkspaceExecuteSpec(
        workspace=handle.workspace,
        expected_generation=handle.generation,
        expected_revision=handle.revision,
        action="set",
        key="other",
        value=7,
    )
    stale_envelope = host.request_envelope(
        request_id="request-stale-revision",
        idempotency_key="idem-reference-0002",
        principal=PrincipalRef(value="principal-test"),
        session=SessionRef(value="session-test"),
        workspace=handle,
        spec=stale_spec,
        deadline_unix_ms=clock.now + 10_000,
    )
    failed = host.execute(stale_envelope, stale_spec)
    assert failed.state is OperationState.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "WORKSPACE_REVISION_CONFLICT"


def test_stale_workspace_generation_fails_closed(host: ReferenceHost, clock: ManualClock) -> None:
    handle, _spec, _envelope = prepare(host, clock)
    stale_spec = WorkspaceExecuteSpec(
        workspace=handle.workspace,
        expected_generation=handle.generation + 1,
        expected_revision=handle.revision,
        action="set",
        key="other",
        value=7,
    )
    stale_handle = handle.model_copy(update={"generation": handle.generation + 1})
    stale_envelope = host.request_envelope(
        request_id="request-stale-generation",
        idempotency_key="idem-reference-0002",
        principal=PrincipalRef(value="principal-test"),
        session=SessionRef(value="session-test"),
        workspace=stale_handle,
        spec=stale_spec,
        deadline_unix_ms=clock.now + 10_000,
    )
    failed = host.execute(stale_envelope, stale_spec)
    assert failed.state is OperationState.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "STALE_WORKSPACE_GENERATION"


def test_stale_runtime_generation_rejected_before_acceptance(
    host: ReferenceHost, clock: ManualClock
) -> None:
    _handle, spec, envelope = prepare(host, clock)
    stale = envelope.model_copy(update={"runtime_generation": host.runtime_generation + 1})
    with pytest.raises(StaleRuntimeGeneration):
        host.submit_execute(stale, spec)


def test_capability_grant_budget_and_deadline_fail_before_acceptance(
    host: ReferenceHost, clock: ManualClock
) -> None:
    _handle, spec, envelope = prepare(host, clock)
    with pytest.raises(CapabilityMismatch):
        host.submit_execute(
            envelope.model_copy(update={"capability_digest": f"sha256:{'0' * 64}"}), spec
        )
    with pytest.raises(GrantDenied):
        host.submit_execute(envelope.model_copy(update={"grants": ()}), spec)
    with pytest.raises(BudgetDenied):
        host.submit_execute(
            envelope.model_copy(
                update={"budget": envelope.budget.model_copy(update={"wall_time_ms": 0})}
            ),
            spec,
        )
    clock.now = envelope.deadline_unix_ms
    with pytest.raises(DeadlineExpired):
        host.submit_execute(envelope, spec)


def test_cross_session_workspace_binding_fails_before_acceptance(
    host: ReferenceHost, clock: ManualClock
) -> None:
    _handle, spec, envelope = prepare(host, clock)
    other_session = SessionRef(value="session-other")
    cross_session = envelope.model_copy(update={"session": other_session})
    with pytest.raises(WorkspaceBindingDenied):
        host.submit_execute(cross_session, spec)


def test_transport_loss_after_workspace_commit_requires_reconciliation(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock)
    uncertain = host.execute(envelope, spec, failpoint="transport_loss_after_workspace_commit")
    assert uncertain.state is OperationState.INDETERMINATE
    assert uncertain.reconciliation_required
    assert host.workspace.current_handle(handle.workspace).revision == 1

    report = host.reconcile(uncertain.operation)
    assert report.state is OperationState.SUCCEEDED
    assert report.observed_revision == 1
    assert host.workspace.current_handle(handle.workspace).revision == 1


def test_workspace_state_and_receipt_rollback_together_on_transaction_loss(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock)
    accepted = host.submit_execute(envelope, spec)
    with pytest.raises(SimulatedWorkspaceTransactionLoss):
        host.workspace.execute(
            accepted.operation,
            spec,
            failpoint="loss_after_state_before_receipt",
        )
    assert host.workspace.current_handle(handle.workspace).revision == 0
    assert host.workspace.receipt(accepted.operation) is None


def test_restart_recovers_committed_receipt_without_reexecution(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "restart.sqlite3"
    first = ReferenceHost(database, now_ms=clock)
    handle, spec, envelope = prepare(first, clock)
    uncertain = first.execute(envelope, spec, failpoint="transport_loss_after_workspace_commit")
    first.close()

    second = ReferenceHost(database, now_ms=clock)
    try:
        assert second.runtime_generation == envelope.runtime_generation + 1
        report = second.reconcile(uncertain.operation)
        assert report.state is OperationState.SUCCEEDED
        assert second.workspace.current_handle(handle.workspace).revision == 1
    finally:
        second.close()


def test_restart_rebinds_accepted_intent_to_new_runtime_generation(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "accepted.sqlite3"
    first = ReferenceHost(database, now_ms=clock)
    handle, spec, envelope = prepare(first, clock)
    accepted = first.submit_execute(envelope, spec)
    first.close()

    second = ReferenceHost(database, now_ms=clock)
    try:
        rebound = second.status(accepted.operation)
        assert rebound.state is OperationState.ACCEPTED
        assert rebound.runtime_generation == second.runtime_generation
        succeeded = second.run_execute(accepted.operation)
        assert succeeded.state is OperationState.SUCCEEDED
        assert second.workspace.current_handle(handle.workspace).revision == 1
    finally:
        second.close()


def test_restart_classifies_interrupted_running_operation_then_reconciles_failure(
    tmp_path: Path, clock: ManualClock
) -> None:
    database = tmp_path / "interrupted.sqlite3"
    first = ReferenceHost(database, now_ms=clock)
    _handle, spec, envelope = prepare(first, clock)
    accepted = first.submit_execute(envelope, spec)
    with pytest.raises(SimulatedProcessLoss):
        first.run_execute(accepted.operation, failpoint="process_loss_before_workspace")
    first.close()

    second = ReferenceHost(database, now_ms=clock)
    try:
        recovered = second.status(accepted.operation)
        assert recovered.state is OperationState.INDETERMINATE
        report = second.reconcile(accepted.operation)
        assert report.state is OperationState.FAILED
        assert second.status(accepted.operation).failure is not None
        assert second.status(accepted.operation).failure.code == "NO_WORKSPACE_RECEIPT"
    finally:
        second.close()


def test_fake_brokers_are_deterministic_and_effects_are_proposal_only(
    host: ReferenceHost,
) -> None:
    operation = OperationRef(value="operation-broker-test")
    context = BrokerContext(
        parent_operation=operation,
        grant_id="grant-broker-test",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="idem-broker-test",
    )
    assert host.models.request("question", context) == host.models.request("question", context)
    child = host.subagents.submit("bounded task", context)
    assert host.subagents.result(child).value == "completed:bounded task"
    proposal = host.effects.propose("write.file", canonical_sha256({"path": "output.txt"}), context)
    assert proposal.kind == "effect_proposal"
    assert proposal.value == "proposal_only"
    assert not hasattr(host.effects, "execute")
    artifact = host.artifacts.put(b"evidence", "text/plain", operation)
    assert host.artifacts.read(artifact) == b"evidence"


def test_invalid_increment_target_is_classified_failure(
    host: ReferenceHost, clock: ManualClock
) -> None:
    handle, spec, envelope = prepare(host, clock, value=42)
    first = spec.model_copy(update={"value": "not-an-integer"})
    first_envelope = envelope.model_copy(update={"input_digest": canonical_sha256(first)})
    assert host.execute(first_envelope, first).state is OperationState.SUCCEEDED

    current = host.workspace.current_handle(handle.workspace)
    increment = WorkspaceExecuteSpec(
        workspace=current.workspace,
        expected_generation=current.generation,
        expected_revision=current.revision,
        action="increment",
        key="answer",
        value=1,
    )
    increment_envelope = host.request_envelope(
        request_id="request-invalid-increment",
        idempotency_key="idem-reference-0002",
        principal=PrincipalRef(value="principal-test"),
        session=SessionRef(value="session-test"),
        workspace=current,
        spec=increment,
        deadline_unix_ms=clock.now + 10_000,
    )
    failed = host.execute(increment_envelope, increment)
    assert failed.state is OperationState.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "INVALID_WORKSPACE_MUTATION"


def test_durable_dispatch_can_start_only_after_owner_ready_gate(
    tmp_path: Path, clock: ManualClock
) -> None:
    host = ReferenceHost(
        tmp_path / "deferred-dispatch.sqlite3",
        now_ms=clock,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        assert host.dispatcher is None
        first = host.start_durable_dispatch()
        assert host.dispatcher is first
        assert host.start_durable_dispatch() is first
    finally:
        host.close()
    host.close()


def test_close_preserves_runtime_stores_when_dispatcher_drain_times_out(
    tmp_path: Path, clock: ManualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ReferenceHost(
        tmp_path / "close-fence.sqlite3",
        now_ms=clock,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    dispatcher = host.start_durable_dispatch()
    real_close = dispatcher.close
    close_calls = 0

    def close_with_one_timeout(drain_timeout_s: float = 5) -> None:
        nonlocal close_calls
        close_calls += 1
        if close_calls == 1:
            raise DispatcherDrainTimeout(("test-dispatcher-worker",))
        real_close(drain_timeout_s)

    monkeypatch.setattr(dispatcher, "close", close_with_one_timeout)

    with pytest.raises(DispatcherDrainTimeout, match="still running"):
        host.close()

    assert host._runtime_resources_closed is False
    assert host._closing is True
    assert host._closed is False
    assert host.ready().runtime_generation == host.runtime_generation

    host.close()
    assert host._runtime_resources_closed is True
    assert host._closed is True
    assert close_calls == 2
    host.close()
    assert close_calls == 2


def test_closing_host_rejects_durable_admission_before_registry_mutation(
    tmp_path: Path, clock: ManualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ReferenceHost(
        tmp_path / "closing-admission.sqlite3",
        now_ms=clock,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    host.start_durable_dispatch()
    spec = RlmJobSpec(query="must not persist", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-closing-admission",
        idempotency_key="idempotency-closing-admission",
        principal=PrincipalRef(value="principal-closing-admission"),
        session=SessionRef(value="session-closing-admission"),
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
        budget=Budget(
            wall_time_ms=10_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    accept_calls = 0

    def fail_if_accepted(*_args: object, **_kwargs: object) -> None:
        nonlocal accept_calls
        accept_calls += 1
        raise AssertionError("closing host must reject before registry.accept")

    monkeypatch.setattr(host.registry, "accept", fail_if_accepted)
    host._closing = True
    try:
        with pytest.raises(ReferenceHostError, match="closing"):
            host.start_durable_dispatch()
        with pytest.raises(ReferenceHostError, match="closing"):
            host.submit_rlm_durable(envelope, spec)
        with pytest.raises(ReferenceHostError, match="closing"):
            host.submit_program_workspace_durable(envelope, None, None)  # type: ignore[arg-type]
        with pytest.raises(ReferenceHostError, match="closing"):
            host.submit_rlm_workbench(envelope, {})
        assert accept_calls == 0
    finally:
        host._closing = False
        host.close()


def test_close_waits_for_admission_to_reach_dispatch_boundary(
    tmp_path: Path, clock: ManualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = ReferenceHost(
        tmp_path / "admission-close-barrier.sqlite3",
        now_ms=clock,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    host.start_durable_dispatch()
    spec = RlmJobSpec(query="linearize admission", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-admission-close-barrier",
        idempotency_key="idempotency-admission-close-barrier",
        principal=PrincipalRef(value="principal-admission-close-barrier"),
        session=SessionRef(value="session-admission-close-barrier"),
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
        budget=Budget(
            wall_time_ms=10_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    real_accept = host.registry.accept
    real_drain = host.drain_runtime_resources
    real_registry_close = host.registry.close
    close_attempted = Event()
    close_was_blocked = False
    errors: list[BaseException] = []

    def close() -> None:
        close_attempted.set()
        try:
            host.close()
        except BaseException as error:
            errors.append(error)

    close_thread = Thread(target=close)

    def blocked_accept(*args: object, **kwargs: object) -> object:
        nonlocal close_was_blocked
        close_thread.start()
        assert close_attempted.wait(2)
        close_was_blocked = close_thread.is_alive()
        return real_accept(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(host, "drain_runtime_resources", lambda: None)
    monkeypatch.setattr(host.registry, "close", lambda: None)
    monkeypatch.setattr(host.registry, "accept", blocked_accept)

    submitted = host.submit_rlm_durable(envelope, spec)
    close_thread.join(5)

    assert close_was_blocked is True
    assert not close_thread.is_alive()
    assert errors == []
    assert submitted.state is OperationState.ACCEPTED
    assert host._closed is True

    monkeypatch.setattr(host, "drain_runtime_resources", real_drain)
    monkeypatch.setattr(host.registry, "close", real_registry_close)
    host._closed = False
    host.close()


def test_reentrant_close_during_admission_fails_the_operation_without_closing_host(
    host: ReferenceHost, clock: ManualClock
) -> None:
    host.start_durable_dispatch()
    spec = RlmJobSpec(query="reject reentrant close", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-reentrant-close",
        idempotency_key="idempotency-reentrant-close",
        principal=PrincipalRef(value="principal-reentrant-close"),
        session=SessionRef(value="session-reentrant-close"),
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
        budget=Budget(
            wall_time_ms=10_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )

    failed = host.submit_rlm_durable(
        envelope,
        spec,
        before_dispatch=lambda _operation: host.close(),
    )

    assert failed.state is OperationState.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "DISPATCH_PREREQUISITE_FAILED"
    assert host._closing is False
    assert host._closed is False


def test_durable_rlm_binding_failure_is_terminal_and_idempotently_observable(
    host: ReferenceHost, clock: ManualClock
) -> None:
    spec = RlmJobSpec(query="bind before dispatch", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-binding-failure",
        idempotency_key="idempotency-binding-failure",
        principal=PrincipalRef(value="principal-binding-failure"),
        session=SessionRef(value="session-binding-failure"),
        spec=spec,
        deadline_unix_ms=clock.now + 10_000,
        budget=Budget(
            wall_time_ms=10_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    binding_attempts = 0
    host.start_durable_dispatch()

    def reject_binding(_operation: OperationRef) -> None:
        nonlocal binding_attempts
        binding_attempts += 1
        raise RuntimeError("sensitive owner-session binding detail")

    failed = host.submit_rlm_durable(
        envelope,
        spec,
        before_dispatch=reject_binding,
    )

    assert failed.state is OperationState.FAILED
    assert failed.failure is not None
    assert failed.failure.code == "DISPATCH_PREREQUISITE_FAILED"
    assert failed.failure.message == "durable dispatch prerequisite failed"
    assert failed.failure.operation == failed.operation
    assert binding_attempts == 1
    events = host.registry.events(failed.operation)
    assert [event.state for event in events] == [
        OperationState.ACCEPTED,
        OperationState.ACCEPTED,
        OperationState.FAILED,
    ]
    assert events[1].note == "recovery_policy_bound"
    assert events[-1].note == "execution_failed"
    assert not {"dispatch_requested", "execution_started"}.intersection(
        event.note for event in events
    )

    replayed = host.submit_rlm_durable(
        envelope,
        spec,
        before_dispatch=lambda _operation: (_ for _ in ()).throw(
            AssertionError("terminal replay must not bind or dispatch")
        ),
    )
    assert replayed == failed
