from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any, Literal

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    prepare_v6_registry,
    workbench_envelope,
    workbench_request,
)

from aar.broker_models import BrokerReceipt, BrokerUsage, EvidenceQuery, ModelRequest
from aar.canonical import canonical_sha256
from aar.runtime.brokers import BrokerBudgetExceeded, BrokerCallIndeterminate, BrokerJournal
from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.rlm_workbench import RlmWorkbenchConflict
from aar.schemas import Budget, OperationRef


@pytest.mark.parametrize(
    "counter",
    ["model_requests", "input_tokens", "output_tokens", "child_operations", "artifact_bytes"],
)
@pytest.mark.parametrize("separate_connection", [False, True])
def test_pending_broker_reservation_blocks_a_second_admission(
    tmp_path: Path, counter: str, separate_connection: bool
) -> None:
    database = tmp_path / "broker.sqlite3"
    journal = BrokerJournal(database)
    contender = BrokerJournal(database) if separate_connection else journal
    operation = OperationRef(value="operation-reserved-budget")
    entered = Event()
    release = Event()
    second_calls: list[bool] = []
    usage = BrokerUsage(**{counter: 1})
    budget = Budget(wall_time_ms=60_000, **{counter: 1})
    response = BrokerReceipt(
        kind="model", handle="held-result", digest="sha256:" + "a" * 64, value="done"
    )

    def held_call() -> BrokerReceipt:
        entered.set()
        assert release.wait(timeout=5)
        return response

    def second_call() -> BrokerReceipt:
        second_calls.append(True)
        return response

    def invoke(owner: BrokerJournal, key: str, call: Any) -> Any:
        return owner.invoke(
            operation=operation,
            method="model.request",
            grant_id="grant-model-request",
            idempotency_key=key,
            request=ModelRequest(prompt=key),
            response_type=BrokerReceipt,
            usage=usage,
            budget=budget,
            call=call,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(invoke, journal, "first", held_call)
            try:
                assert entered.wait(timeout=5)
                # Reservations constrain admission but are not reported as committed usage.
                assert journal.usage(operation) == BrokerUsage()
                with pytest.raises(BrokerBudgetExceeded, match=counter):
                    invoke(contender, "second", second_call)
                assert second_calls == []
            finally:
                release.set()
            assert first.result(timeout=5) == response
        assert journal.usage(operation) == usage
        assert len(journal.traces(operation)) == 1
        assert invoke(contender, "first", second_call) == response
        assert second_calls == []
    finally:
        release.set()
        if contender is not journal:
            contender.close()
        journal.close()


def test_broker_admission_serializes_budget_read_with_another_journal(
    tmp_path: Path,
) -> None:
    database = tmp_path / "broker-race.sqlite3"
    first_owner = BrokerJournal(database)
    contender = BrokerJournal(database)
    insert_reached = Event()
    release_insert = Event()
    operation = OperationRef(value="operation-concurrent-admission")
    response = BrokerReceipt(
        kind="model", handle="one-result", digest="sha256:" + "a" * 64, value="done"
    )
    calls: list[str] = []

    def hold_first_insert(statement: str) -> None:
        if "INSERT INTO broker_calls" in statement:
            insert_reached.set()
            assert release_insert.wait(timeout=5)

    def release_on_contender_write(statement: str) -> None:
        if statement == "BEGIN IMMEDIATE":
            release_insert.set()

    first_owner._connection.set_trace_callback(hold_first_insert)
    contender._connection.set_trace_callback(release_on_contender_write)

    def invoke(owner: BrokerJournal, key: str) -> Any:
        def call() -> BrokerReceipt:
            calls.append(key)
            return response

        return owner.invoke(
            operation=operation,
            method="model.request",
            grant_id="grant-model-request",
            idempotency_key=key,
            request=ModelRequest(prompt=key),
            response_type=BrokerReceipt,
            usage=BrokerUsage(model_requests=1),
            budget=Budget(wall_time_ms=60_000, model_requests=1),
            call=call,
        )

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            first = executor.submit(invoke, first_owner, "first")
            try:
                assert insert_reached.wait(timeout=5)
                with pytest.raises(BrokerBudgetExceeded, match="model_requests"):
                    invoke(contender, "contender")
            finally:
                release_insert.set()
            assert first.result(timeout=5) == response
        assert calls == ["first"]
        assert len(first_owner.traces(operation)) == 1
    finally:
        release_insert.set()
        contender.close()
        first_owner.close()


@pytest.mark.parametrize("action", ["pending", "quarantine", "compensation_proposed"])
def test_unresolved_reconciliation_cannot_reopen_a_budgeted_admission(
    tmp_path: Path, action: Literal["pending", "quarantine", "compensation_proposed"]
) -> None:
    journal = BrokerJournal(tmp_path / "unresolved-budget.sqlite3")
    operation = OperationRef(value="operation-unresolved-budget")
    request = ModelRequest(prompt="possibly sent")
    budget = Budget(wall_time_ms=60_000, model_requests=1)
    calls: list[str] = []
    receipt = BrokerReceipt(
        kind="evidence", handle="inspection", digest="sha256:" + "c" * 64, value="observed"
    )

    def uncertain_call() -> BrokerReceipt:
        calls.append("uncertain")
        raise BrokerCallIndeterminate("no authoritative outcome")

    def forbidden_call() -> BrokerReceipt:
        calls.append("new-budgeted-call")
        return receipt

    def inspection(key: str) -> Any:
        return journal.invoke(
            operation=operation,
            method="evidence.query",
            grant_id="grant-evidence-query",
            idempotency_key=key,
            request=EvidenceQuery(text=key),
            response_type=BrokerReceipt,
            usage=BrokerUsage(),
            budget=budget,
            call=lambda: receipt,
        )

    try:
        assert inspection("before") == receipt
        with pytest.raises(BrokerCallIndeterminate):
            journal.invoke(
                operation=operation,
                method="model.request",
                grant_id="grant-model-request",
                idempotency_key="uncertain",
                request=request,
                response_type=BrokerReceipt,
                usage=BrokerUsage(model_requests=1),
                budget=budget,
                call=uncertain_call,
            )
        journal.record_unresolved_reconciliation(
            operation=operation,
            sequence=2,
            method="model.request",
            request_digest=canonical_sha256(request),
            authority_digest="sha256:" + "d" * 64,
            action=action,
            reason_code="RECEIPT_UNAVAILABLE",
            now_unix_ms=FIXED_NOW_MS,
            compensation_receipt=(
                BrokerReceipt(
                    kind="effect",
                    handle="proposal-only",
                    digest="sha256:" + "e" * 64,
                    value="proposed",
                )
                if action == "compensation_proposed"
                else None
            ),
        )
        with pytest.raises((BrokerBudgetExceeded, BrokerCallIndeterminate)):
            journal.invoke(
                operation=operation,
                method="model.request",
                grant_id="grant-model-request",
                idempotency_key="new-budgeted-key",
                request=ModelRequest(prompt="must not spend"),
                response_type=BrokerReceipt,
                usage=BrokerUsage(model_requests=1),
                budget=budget,
                call=forbidden_call,
            )
        assert calls == ["uncertain"]
        assert journal.has_unresolved_calls(operation)
        assert journal.usage(operation) == BrokerUsage()
        assert inspection("before") == receipt
        assert inspection("after") == receipt
    finally:
        journal.close()


def _authority(database: Path) -> tuple[Any, ...]:
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT j.phase, j.control_revision, j.workspace_id, "
            "c.control_revision, c.cancellation_requested "
            "FROM rlm_workbench_jobs AS j JOIN operation_controls AS c USING(operation_id)"
        ).fetchone()
    assert row is not None
    return row


@pytest.mark.parametrize("interruption", ["deadline", "cancel"])
def test_workspace_preparation_cannot_publish_after_losing_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interruption: str
) -> None:
    database = tmp_path / "registry.sqlite3"
    prepare_v6_registry(database, tmp_path / "v5-snapshot.sqlite3")
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    created_states: list[Any] = []
    try:
        request = workbench_request(host)
        coordinator = host.rlm_workbench
        assert coordinator is not None
        record, _ = host.registry.accept(
            workbench_envelope(host, request), request.model_dump_json()
        )
        backend = host.program_workspace
        assert isinstance(backend, SupervisedIPythonWorkspaceBackend)
        original_create = backend.create
        interrupted: list[tuple[Any, ...]] = []

        def create_then_interrupt(*args: Any, **kwargs: Any) -> Any:
            handle = original_create(*args, **kwargs)
            created_states.append(backend._state(handle.workspace))
            if interruption == "deadline":
                coordinator._now_ms = lambda: request.root["context"]["deadline_unix_ms"]
                coordinator.mark_deadline_terminal(record.operation)
            else:
                host.registry.request_cancel(record.operation, "sha256:" + "b" * 64, "test-cancel")
            interrupted.append(_authority(database))
            return handle

        monkeypatch.setattr(host.program_workspace, "create", create_then_interrupt)
        with pytest.raises(RlmWorkbenchConflict):
            coordinator.admit(record.operation, request)
        assert len(interrupted) == 1
        # No false return of an unbound handle or mutation of the winning control epoch.
        assert _authority(database) == interrupted[0]
        assert _authority(database)[2] is None
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM operation_dispatch").fetchone() == (0,)
    finally:
        host.close()
    assert created_states
    assert all(state.closed and state.process.poll() is not None for state in created_states)


def test_cancelled_control_prevents_the_preparation_transition_and_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "registry.sqlite3"
    prepare_v6_registry(database, tmp_path / "v5-snapshot.sqlite3")
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        request = workbench_request(host)
        coordinator = host.rlm_workbench
        assert coordinator is not None
        record, _ = host.registry.accept(
            workbench_envelope(host, request), request.model_dump_json()
        )
        with monkeypatch.context() as patch:
            patch.setattr(coordinator, "_prepare_workspace", lambda *_args: None)
            coordinator.admit(record.operation, request)
        host.registry.request_cancel(record.operation, "sha256:" + "b" * 64, "test-cancel")
        prior = _authority(database)
        spawned: list[bool] = []

        def forbidden_create(*_args: Any, **_kwargs: Any) -> Any:
            spawned.append(True)
            raise AssertionError("cancelled preparation must not spawn a worker")

        monkeypatch.setattr(host.program_workspace, "create", forbidden_create)
        with pytest.raises(RlmWorkbenchConflict):
            coordinator._prepare_workspace(
                record.operation, workbench_envelope(host, request).session
            )
        assert spawned == []
        assert _authority(database) == prior
    finally:
        host.close()
