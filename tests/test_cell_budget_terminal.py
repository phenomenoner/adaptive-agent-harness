"""Offline cell-cap regressions with a synthetic workspace and model receipts."""

from __future__ import annotations

import copy
import socket
import sqlite3
import subprocess
from pathlib import Path
from typing import Any, ClassVar

import pytest
from test_provider_ready_planner_runtime import _FakeIPythonWorkspaceBackend
from test_rlm_workbench import (
    FIXED_NOW_MS,
    commit_pending_ticket_success,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.reference_host as host_module
from aar.canonical import canonical_json_bytes
from aar.rlm_workbench_models import RlmWorkbenchExecuteInput
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.rlm_workbench import RlmWorkbenchConflict
from aar.runtime.workspace_models import WorkspaceProgramResult

CELL = {"kind": "execute_cell", "code": "answer = 42", "expected_result_hint": "42"}
FINAL = {"kind": "finalize", "output": {"answer": "42", "artifacts": []}, "artifact_stage_ids": []}


class FakeBackend(_FakeIPythonWorkspaceBackend):
    instances: ClassVar[list[FakeBackend]] = []

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.executions = 0
        self.checkpoints = 0
        self.instances.append(self)

    def checkpoint(self, *args: Any, **kwargs: Any) -> Any:
        self.checkpoints += 1
        return super().checkpoint(*args, **kwargs)

    def execute(self, operation: Any, handle: Any, _spec: Any) -> WorkspaceProgramResult:
        self.executions += 1
        return WorkspaceProgramResult(
            operation=operation,
            workspace=handle.workspace,
            backend=handle.backend,
            generation=handle.generation,
            revision_before=handle.revision,
            revision_after=handle.revision + 1,
            status="succeeded",
            result=42,
            events=(),
        )

    def close(self, *_args: Any, **_kwargs: Any) -> None:
        pass


@pytest.fixture(autouse=True)
def isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("external worker/network boundary blocked")

    FakeBackend.instances.clear()
    monkeypatch.setattr(host_module, "SupervisedIPythonWorkspaceBackend", FakeBackend)
    monkeypatch.setattr(subprocess, "Popen", blocked)
    monkeypatch.setattr(socket, "socket", blocked)


@pytest.mark.parametrize("mode", ["service_managed", "caller_delegated"])
@pytest.mark.parametrize("outcome", ["cap", "finalize"])
def test_cell_cap_and_finalization(tmp_path: Path, mode: str, outcome: str) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "snapshot.sqlite")

    class Planner:
        calls = 0

        def plan(self, *_args: Any) -> dict[str, Any]:
            self.calls += 1
            return dict(FINAL if self.calls == 2 and outcome == "finalize" else CELL)

    planner = Planner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    transition_errors: list[str] = []
    original_transition = host.registry.transition_claimed

    def observed_transition(*args: Any, **kwargs: Any) -> Any:
        try:
            return original_transition(*args, **kwargs)
        except Exception as error:
            transition_errors.append(f"{type(error).__name__}: {error}")
            raise

    host.registry.transition_claimed = observed_transition
    try:
        request = workbench_request(
            host, require_named_artifacts=False, execution_mode=mode, max_model_calls=4
        )
        doc = copy.deepcopy(request.root)
        doc["spec"]["budgets"]["max_cells"] = 1
        request = RlmWorkbenchExecuteInput.model_validate(doc, strict=True)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        if mode == "caller_delegated":
            assert host.caller_work is not None
            for ordinal, directive in enumerate((CELL, FINAL if outcome == "finalize" else CELL)):
                pending = host.caller_work.get(wait_for_pending_ticket(database))
                settled = commit_pending_ticket_success(
                    host.caller_work,
                    database,
                    request,
                    pending,
                    prefix=f"cellcap-{ordinal}",
                    output_text=canonical_json_bytes(directive).decode(),
                )
                assert settled.state == "settled_success"
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        backend = FakeBackend.instances[-1]
        assert backend.executions == 1
        assert backend.checkpoints == (2 if outcome == "cap" else 3)
        with sqlite3.connect(database) as conn:
            assert conn.execute("SELECT state FROM rlm_workbench_cells").fetchall() == [
                ("committed",)
            ]
            if mode == "caller_delegated":
                assert conn.execute(
                    "SELECT state FROM caller_work_tickets ORDER BY rowid"
                ).fetchall() == [("settled_success",), ("settled_success",)]
        if outcome == "finalize":
            assert terminal.state.value == "succeeded" and snapshot.phase == "succeeded"
        else:
            assert terminal.state.value == "failed", transition_errors
            assert terminal.failure is not None
            assert terminal.failure.code == "BUDGET_EXCEEDED"
            assert terminal.failure.category.value == "budget"
            assert terminal.failure.certainty.value == "certain" and not terminal.failure.retryable
            assert {d.name: d.value for d in terminal.failure.details} == {"budget_kind": "cells"}
            assert snapshot.phase == "failed" and snapshot.certainty == "certain"
            assert snapshot.root["failure"] == terminal.failure.model_dump(mode="json")
    finally:
        host.close()


def test_same_message_generic_conflict_remains_unknown(tmp_path: Path) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "snapshot.sqlite")

    class Planner:
        def plan(self, *_args: Any) -> Any:
            raise RlmWorkbenchConflict("workbench cell budget exhausted")

    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=Planner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert terminal.state.value == "indeterminate"
        assert snapshot.phase == "parked" and snapshot.failure.code == "RECONCILIATION_REQUIRED"
    finally:
        host.close()


@pytest.mark.parametrize("fault", ["unresolved_cell", "cancelled"])
def test_uncertain_or_cancelled_authority_never_becomes_certain(tmp_path: Path, fault: str) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "snapshot.sqlite")

    class Planner:
        calls = 0

        def plan(self, *_args: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 2:
                with sqlite3.connect(database) as conn:
                    if fault == "unresolved_cell":
                        assert (
                            conn.execute(
                                "UPDATE rlm_workbench_cells SET state = 'lost_before_commit' "
                                "WHERE state = 'committed'"
                            ).rowcount
                            == 1
                        )
                    else:
                        assert (
                            conn.execute(
                                "UPDATE rlm_workbench_jobs SET cancellation_requested = 1"
                            ).rowcount
                            == 1
                        )
            return dict(CELL)

    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=Planner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False, max_model_calls=4)
        doc = copy.deepcopy(request.root)
        doc["spec"]["budgets"]["max_cells"] = 1
        request = RlmWorkbenchExecuteInput.model_validate(doc, strict=True)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None and host.rlm_workbench is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=10)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert terminal.state.value == "indeterminate"
        assert snapshot.phase == "parked" and snapshot.certainty == "indeterminate"
        assert snapshot.failure.code == "RECONCILIATION_REQUIRED"
        assert FakeBackend.instances[-1].executions == 1
    finally:
        host.close()
