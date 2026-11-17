"""Controlled native coordinator/dispatcher regressions; no provider dispatch."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    commit_pending_ticket_success,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.reference_host import ReferenceHost

_PRIVATE_REASON = "TEST_PRIVATE_PLANNER_REASON: task cannot be completed in one response"
_CELL = {"kind": "execute_cell", "code": "answer = 42\nanswer", "expected_result_hint": "42"}
_ABSTAIN = {"kind": "abstain", "reason": _PRIVATE_REASON}


class _AbstainingPlanner:
    def __init__(self, after_cell: bool) -> None:
        self.after_cell = after_cell
        self.calls = 0

    def plan(self, _operation: Any, _spec: Any, _snapshot: Any) -> dict[str, Any]:
        self.calls += 1
        return dict(_CELL if self.after_cell and self.calls == 1 else _ABSTAIN)


def test_abstention_cannot_turn_an_unresolved_cell_into_certain_failure(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")

    class AmbiguousCellPlanner(_AbstainingPlanner):
        def plan(self, operation: Any, spec: Any, snapshot: Any) -> dict[str, Any]:
            directive = super().plan(operation, spec, snapshot)
            if self.calls == 2:
                # Fault injection after a real committed native cell: model a
                # persisted worker outcome that must remain unreconciled.
                with sqlite3.connect(database) as connection:
                    assert (
                        connection.execute(
                            "UPDATE rlm_workbench_cells SET state = 'lost_before_commit' "
                            "WHERE operation_id = ? AND state = 'committed'",
                            (operation.value,),
                        ).rowcount
                        == 1
                    )
            return directive

    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=AmbiguousCellPlanner(after_cell=True),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=True)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        assert terminal.state.value == "indeterminate"
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "parked"
        assert snapshot.certainty == "indeterminate"
        assert snapshot.failure.code == "RECONCILIATION_REQUIRED"
        assert snapshot.result is None
        assert _PRIVATE_REASON not in canonical_json_bytes(snapshot.root).decode()
        assert {detail.name: detail.value for detail in snapshot.failure.details}["error.type"] == (
            "RlmWorkbenchPlannerAbstained"
        )
    finally:
        host.close()


@pytest.mark.parametrize("mode", ["service_managed", "caller_delegated"])
@pytest.mark.parametrize("after_cell", [False, True])
def test_valid_abstention_is_known_failed_delivery_not_worker_loss(
    tmp_path: Path, mode: str, after_cell: bool
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = _AbstainingPlanner(after_cell)
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        assert host.caller_work is not None and host.rlm_workbench is not None
        request = workbench_request(
            host, require_named_artifacts=True, execution_mode=mode, max_model_calls=7
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        prompts: list[dict[str, Any]] = []
        if mode == "caller_delegated":
            for ordinal in range(1 + int(after_cell)):
                pending = host.caller_work.get(wait_for_pending_ticket(database))
                prompts.append(json.loads(pending.root["request"]["prompt"]))
                directive = _CELL if after_cell and ordinal == 0 else _ABSTAIN
                settled = commit_pending_ticket_success(
                    host.caller_work,
                    database,
                    request,
                    pending,
                    prefix=f"abstain-{ordinal}",
                    output_text=canonical_json_bytes(directive).decode(),
                )
                assert settled.state == "settled_success"
        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        assert terminal.state.value == "failed"
        assert terminal.failure is not None
        assert terminal.failure.code == "WORKSPACE_FAILED"
        assert terminal.failure.certainty.value == "certain"
        assert terminal.failure.message == "workbench planner abstained"
        assert terminal.failure.retryable is False
        details = {item.name: item.value for item in terminal.failure.details}
        assert details == {
            "planner_decision": "abstain",
            "reason_digest": canonical_sha256(_PRIVATE_REASON),
        }
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "failed" and snapshot.certainty == "certain"
        assert snapshot.result is None and not snapshot.pending_tickets
        assert snapshot.root["failure"] == terminal.failure.model_dump(mode="json")
        assert _PRIVATE_REASON not in canonical_json_bytes(snapshot.root).decode()
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (int(after_cell),)
            for table in (
                "rlm_workbench_artifact_stages",
                "rlm_workbench_finalization_manifests",
            ):
                assert connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE operation_id = ?",
                    (admitted.operation.value,),
                ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ? "
                "AND state = 'parked'",
                (admitted.operation.value,),
            ).fetchone() == (0,)
        if mode == "service_managed":
            assert planner.calls == 1 + int(after_cell)
        else:
            assert [item["execution_budget"]["remaining_model_calls"] for item in prompts] == [
                7 - ordinal for ordinal in range(1 + int(after_cell))
            ]
    finally:
        host.close()


@pytest.mark.parametrize("corrections", [0, 2])
def test_caller_request_exposes_real_multistep_budget(tmp_path: Path, corrections: int) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        assert host.caller_work is not None
        request = workbench_request(host, execution_mode="caller_delegated", max_model_calls=7)
        host.submit_rlm_workbench(workbench_envelope(host, request), request)
        pending = host.caller_work.get(wait_for_pending_ticket(database))
        prompt = json.loads(pending.root["request"]["prompt"])
        assert prompt["execution_budget"] == {
            **request.root["spec"]["budgets"],
            "remaining_cells": request.root["spec"]["budgets"]["max_cells"],
            "remaining_model_calls": 7,
        }
        assert "multi-step" in prompt["instructions"]
        assert "one directive per response" in prompt["instructions"]
        assert "not the entire task in one response" in prompt["instructions"]
        assert (
            prompt["artifact_policy"]["max_artifact_count"]
            == request.root["spec"]["budgets"]["max_artifact_count"]
        )
        for ordinal in range(corrections):
            assert (
                commit_pending_ticket_success(
                    host.caller_work,
                    database,
                    request,
                    pending,
                    prefix=f"invalid-directive-{ordinal}",
                    output_text='{"kind":"invalid"}',
                ).state
                == "settled_success"
            )
            pending = host.caller_work.get(wait_for_pending_ticket(database))
            prompt = json.loads(pending.root["request"]["prompt"])
            assert prompt["planner_owner"]["phase"] == "correction"
            assert prompt["execution_budget"]["remaining_model_calls"] == 6 - ordinal
    finally:
        host.close()


def test_abstention_outer_write_failure_does_not_leave_split_certainty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=_AbstainingPlanner(False),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    original = host.registry.transition_claimed

    def fail_outer(*args: Any, **kwargs: Any) -> Any:
        if kwargs.get("note") == "workbench_planner_abstained":
            raise sqlite3.OperationalError("injected before outer write")
        return original(*args, **kwargs)

    monkeypatch.setattr(host.registry, "transition_claimed", fail_outer)
    try:
        request = workbench_request(host)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert terminal.state.value == "indeterminate"
        assert snapshot.phase == "parked" and snapshot.certainty == "indeterminate"
    finally:
        host.close()


@pytest.mark.parametrize("fault", ["between_sql", "after_commit"])
@pytest.mark.parametrize("failure_kind", ["abstain", "budget"])
def test_planner_failure_atomic_transaction_faults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str, failure_kind: str
) -> None:
    from aar.runtime.rlm_workbench import RlmWorkbenchModelCallBudgetExceeded

    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")

    class Planner:
        calls = 0

        def plan(self, *_args: Any) -> dict[str, Any]:
            self.calls += 1
            if failure_kind == "budget":
                raise RlmWorkbenchModelCallBudgetExceeded("budget exhausted")
            return dict(_ABSTAIN)

    planner = Planner()
    if fault == "between_sql":
        with sqlite3.connect(database) as conn:
            conn.execute("""CREATE TRIGGER fail_planner_outer BEFORE UPDATE OF state ON operations
                WHEN NEW.state = 'failed' BEGIN SELECT RAISE(ABORT, 'injected outer'); END""")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    original = host.registry.transition_claimed

    def after_commit(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if kwargs.get("inner_transition") is not None:
            raise sqlite3.OperationalError("injected readback failure after commit")
        return result

    if fault == "after_commit":
        monkeypatch.setattr(host.registry, "transition_claimed", after_commit)
    try:
        request = workbench_request(host)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert planner.calls == 1
        if fault == "between_sql":
            assert terminal.state.value == "indeterminate"
            assert snapshot.phase == "parked" and snapshot.certainty == "indeterminate"
        else:
            assert terminal.state.value == "failed"
            assert snapshot.phase == "failed" and snapshot.certainty == "certain"
            assert snapshot.root["failure"] == terminal.failure.model_dump(mode="json")
            assert terminal.failure.code == (
                "WORKSPACE_FAILED" if failure_kind == "abstain" else "BUDGET_EXCEEDED"
            )
    finally:
        host.close()
