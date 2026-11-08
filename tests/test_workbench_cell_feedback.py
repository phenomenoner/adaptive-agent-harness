"""Certain cell failures feed the native planner; unknown execution never does.

Model tickets are synthetic. Coordination, SQLite, IPython execution, checkpoints
and artifact publication decisions are real local components; no provider runs.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from pydantic import JsonValue
from test_rlm_workbench import (
    FIXED_NOW_MS,
    BrokerRoundTripPlanner,
    commit_pending_ticket_success,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.rlm_workbench as workbench
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceBackendDescriptor,
    WorkspaceEvent,
    WorkspaceProgramResult,
)
from aar.schemas import OperationRef, WorkspaceRef


class CallerCase:
    def __init__(self, path: Path, *, max_calls: int = 4):
        self.database = path / "registry.sqlite"
        prepare_v6_registry(self.database, path / "snapshot.sqlite")
        self.host = ReferenceHost(
            self.database,
            now_ms=lambda: FIXED_NOW_MS,
            enable_durable_dispatch=False,
            dispatcher_concurrency=1,
        )
        self.request = workbench_request(
            self.host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
            max_model_calls=max_calls,
        )
        self.record = self.host.submit_rlm_workbench(
            workbench_envelope(self.host, self.request),
            self.request,
        )
        self.turn = 0

    def run(self):
        self.turn += 1
        claim = self.host.registry.claim_next(
            runtime_generation=self.host.runtime_generation,
            dispatcher_generation=self.host.runtime_generation,
            owner_digest=canonical_sha256(f"controlled-owner-{self.turn}"),
            lease_duration_ms=30_000,
        )
        assert claim is not None and self.host.rlm_workbench is not None
        return self.host.rlm_workbench.run_claimed(
            self.record.operation,
            claim.attempt,
            AttemptFence(
                dispatcher_generation=claim.dispatcher_generation,
                lease_epoch=claim.lease_epoch,
                owner_digest=claim.owner_digest,
            ),
        )

    def pending(self):
        assert self.host.caller_work is not None
        return self.host.caller_work.get(wait_for_pending_ticket(self.database))

    def send(self, directive):
        assert self.host.caller_work is not None
        ticket = self.pending()
        assert (
            commit_pending_ticket_success(
                self.host.caller_work,
                self.database,
                self.request,
                ticket,
                prefix=f"controlled-{self.turn}",
                output_text=canonical_json_bytes(directive).decode(),
            ).state
            == "settled_success"
        )
        return self.run()


def test_certain_failed_cell_feeds_next_real_caller_request_without_replay_or_publication(
    tmp_path: Path,
):
    case = CallerCase(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        code = (
            "runs = 1\n"
            "aar_artifact('reports/partial.txt', b'partial', 'text/plain')\n"
            "raise RuntimeError('synthetic unit-test failure')"
        )
        failed = {"kind": "execute_cell", "code": code, "expected_result_hint": None}
        after = case.send(failed)
        assert after.phase == "waiting_external", (
            "certain Python failure must become planner feedback"
        )
        prompt = json.loads(case.pending().root["request"]["prompt"])
        last = prompt["last_committed_cell"]
        assert last["result"]["status"] == "failed"
        assert last["result"]["workspace_lost"] is False
        assert last["result"]["revision_before"] == 0
        assert last["result"]["revision_after"] == 1
        assert any(
            e["kind"] == "exception" and e["data"]["type"] == "RuntimeError"
            for e in last["result"]["events"]
        )
        assert last["source"] == failed
        assert last["source_digest"] == canonical_sha256(failed)
        assert last["artifact_stage_ids"] == []
        assert case.host.rlm_workbench is not None
        assert case.host.rlm_workbench.workspace_handle(case.record.operation).revision == 1
        with sqlite3.connect(case.database) as db:
            assert db.execute("SELECT state FROM rlm_workbench_artifact_stages").fetchall() == [
                ("staged",)
            ]
            manifest = json.loads(
                db.execute("SELECT manifest_json FROM rlm_workbench_cell_manifests").fetchone()[0]
            )
            assert manifest["artifact_stage_ids"] == []
        assert (
            case.send(
                {
                    "kind": "execute_cell",
                    "code": "assert runs == 1\nruns += 1\nanswer = 42",
                    "expected_result_hint": "42",
                }
            ).phase
            == "waiting_external"
        )
        terminal = case.send(
            {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": [],
            }
        )
        assert case.host.rlm_workbench.snapshot(case.record.operation).phase == "succeeded"
        assert terminal.python_cells == 2
        assert len(terminal.artifact_bindings) == 0
        assert case.host.rlm_workbench.workspace_handle(case.record.operation).revision == 2
        with sqlite3.connect(case.database) as db:
            rows = db.execute(
                "SELECT state,result_json FROM rlm_workbench_cells ORDER BY cell_index"
            ).fetchall()
            assert [r[0] for r in rows] == ["committed", "committed"]
            assert [json.loads(r[1])["status"] for r in rows] == ["failed", "succeeded"]
            assert db.execute("SELECT state FROM rlm_workbench_artifact_stages").fetchall() == [
                ("staged",)
            ]
            assert db.execute("SELECT COUNT(*) FROM caller_work_tickets").fetchone()[0] == 3
    finally:
        case.host.close()


def test_known_failure_still_consumes_the_existing_model_call_budget(tmp_path: Path):
    case = CallerCase(tmp_path, max_calls=1)
    try:
        assert case.run().phase == "waiting_external"
        with pytest.raises(workbench.RlmWorkbenchModelCallBudgetExceeded):
            case.send(
                {
                    "kind": "execute_cell",
                    "code": "raise RuntimeError('synthetic failure')",
                    "expected_result_hint": None,
                }
            )
        with sqlite3.connect(case.database) as db:
            assert db.execute("SELECT COUNT(*) FROM caller_work_tickets").fetchone()[0] == 1
            assert db.execute("SELECT state FROM rlm_workbench_cells").fetchall() == [
                ("committed",)
            ]
        assert case.host.rlm_workbench is not None
        assert case.host.rlm_workbench.workspace_handle(case.record.operation).revision == 1
    finally:
        case.host.close()


class BrokerFailurePlanner(BrokerRoundTripPlanner):
    def plan(self, _operation, _spec, _snapshot):
        self.calls += 1
        if self.calls == 1:
            request_json = json.dumps(self.request, sort_keys=True)
            return {
                "kind": "execute_cell",
                "code": (
                    "import json\nruns = globals().get('runs', 0) + 1\n"
                    "aar_artifact('reports/pre-broker.txt', b'partial', 'text/plain')\n"
                    f"receipt = aar_broker(json.loads({request_json!r}))\n"
                    "raise RuntimeError('synthetic after-broker failure')"
                ),
                "expected_result_hint": None,
            }
        if self.calls == 2:
            return {
                "kind": "execute_cell",
                "code": "assert runs == 1\nruns += 1\nanswer = 'ok'",
                "expected_result_hint": "ok",
            }
        if self.calls == 3:
            return {
                "kind": "finalize",
                "output": {"answer": "ok", "artifacts": []},
                "artifact_stage_ids": [],
            }
        raise AssertionError("unexpected additional planner call")


def test_known_failure_after_broker_rebind_keeps_receipt_and_never_replays_or_publishes(
    tmp_path: Path,
):
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "snapshot.sqlite")
    planner = BrokerFailurePlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.caller_work is not None and host.dispatcher is not None
        pending = host.caller_work.get(wait_for_pending_ticket(database))
        assert (
            commit_pending_ticket_success(
                host.caller_work,
                database,
                request,
                pending,
                prefix="controlled-rebind",
                output_text="ok",
            ).state
            == "settled_success"
        )
        terminal = host.dispatcher.wait(record.operation, timeout_s=10)
        assert terminal.state.value == "succeeded"
        assert planner.calls == 3
        assert host.rlm_workbench is not None
        snapshot = host.rlm_workbench.snapshot(record.operation)
        assert snapshot.result.python_cells == 2
        assert len(snapshot.result.artifact_bindings) == 0
        with sqlite3.connect(database) as db:
            rows = db.execute(
                "SELECT result_json FROM rlm_workbench_cells ORDER BY cell_index"
            ).fetchall()
            assert [json.loads(r[0])["status"] for r in rows] == ["failed", "succeeded"]
            assert db.execute("SELECT state FROM rlm_workbench_artifact_stages").fetchall() == [
                ("staged",)
            ]
            assert db.execute(
                "SELECT state,consumption_kind FROM rlm_workbench_rebind_transfers"
            ).fetchall() == [("consumed", "worker_ack")]
            assert db.execute("SELECT COUNT(*) FROM caller_work_tickets").fetchone()[0] == 1
    finally:
        host.close()


def result_witness():
    backend = WorkspaceBackendDescriptor.issue(
        kind="ipython",
        version="synthetic",
        checkpoint_formats=("aar.workspace-checkpoint.v1",),
        features=(),
    )
    operation = OperationRef(value="exec-synthetic-cell")
    handle = ProgrammableWorkspaceHandle(
        workspace=WorkspaceRef(value="synthetic-workspace"),
        backend=backend,
        generation=1,
        revision=3,
    )
    result = WorkspaceProgramResult(
        operation=operation,
        workspace=handle.workspace,
        backend=backend,
        generation=1,
        revision_before=3,
        revision_after=4,
        status="failed",
        events=(
            WorkspaceEvent(
                sequence=1,
                kind="exception",
                data={"type": "RuntimeError", "message": "synthetic", "origin": "user_code"},
            ),
        ),
    )
    return operation, handle, result


@pytest.mark.parametrize(
    "change",
    [
        "lost",
        "interrupted",
        "timed_out",
        "different_operation",
        "different_workspace",
        "different_generation",
        "different_backend",
        "stale_revision",
        "no_exception",
        "host_control",
        "missing_origin",
    ],
)
def test_uncertain_or_mismatched_failed_result_cannot_commit(change):
    operation, handle, result = result_witness()
    data = result.model_dump(mode="python")
    if change == "lost":
        data["workspace_lost"] = True
    elif change in {"interrupted", "timed_out"}:
        data["status"] = change
    elif change == "different_operation":
        data["operation"] = OperationRef(value="other-operation")
    elif change == "different_workspace":
        data["workspace"] = WorkspaceRef(value="other-workspace")
    elif change == "different_generation":
        data["generation"] = 2
    elif change == "different_backend":
        data["backend"] = WorkspaceBackendDescriptor.issue(
            kind="ipython",
            version="other",
            checkpoint_formats=("aar.workspace-checkpoint.v1",),
            features=(),
        )
    elif change == "stale_revision":
        data.update(revision_before=2, revision_after=3)
    elif change == "no_exception":
        data["events"] = ()
    elif change in {"host_control", "missing_origin"}:
        exception: dict[str, JsonValue] = {"type": "RuntimeError", "message": "synthetic"}
        if change == "host_control":
            exception["origin"] = "host_control"
        data["events"] = (WorkspaceEvent(sequence=1, kind="exception", data=exception),)
    modified = WorkspaceProgramResult.model_validate(data, strict=True)
    assert not workbench._cell_result_committable(modified, handle, operation)


def test_known_failure_is_committable_but_is_not_success():
    operation, handle, result = result_witness()
    assert workbench._cell_result_committable(result, handle, operation)
    assert result.status == "failed" and result.revision_after == 4
