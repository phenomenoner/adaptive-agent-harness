"""Offline source-projected completion selection and terminal ownership."""

import json
import sqlite3

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    commit_pending_ticket_success,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)
from test_source10_completion import candidate_worker as candidate_worker

from aar.canonical import canonical_json_bytes
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.rlm_workbench import RlmWorkbenchConflict, _InvalidCompletionSelection


def test_empty_selection_after_committed_artifact_is_certain_failure(tmp_path):
    db = tmp_path / "registry.sqlite"
    prepare_v6_registry(db, tmp_path / "snapshot.sqlite")
    host = ReferenceHost(
        db, now_ms=lambda: FIXED_NOW_MS, enable_durable_dispatch=True, dispatcher_concurrency=1
    )
    try:
        req = workbench_request(
            host, require_named_artifacts=True, execution_mode="caller_delegated", max_model_calls=2
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, req), req)
        ticket = host.caller_work.get(wait_for_pending_ticket(db))
        prompt = json.loads(ticket.root["request"]["prompt"])
        instructions = prompt["instructions"]
        assert len(instructions) <= 2048
        assert "selects no existing stages" in instructions
        directive = {
            "kind": "execute_cell",
            "code": (
                "aar_artifact('a.txt', b'abc', "
                "'text/plain')\naar_complete({'answer':'ok','artifacts':[]})"
            ),
            "expected_result_hint": None,
        }
        settled = commit_pending_ticket_success(
            host.caller_work,
            db,
            req,
            ticket,
            prefix="empty",
            output_text=canonical_json_bytes(directive).decode(),
        )
        assert settled.state == "settled_success"
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=15)
        assert terminal.state.value == "failed"
        assert terminal.failure.code == "WORKSPACE_FAILED"
        assert terminal.failure.certainty.value == "certain"
        assert {d.name: d.value for d in terminal.failure.details} == {
            "completion_decision": "invalid",
            "completion_reason": "missing_named_artifacts",
        }
        snap = host.rlm_workbench.snapshot(admitted.operation)
        assert snap.phase == "failed" and snap.result is None and not snap.pending_tickets
        with sqlite3.connect(db) as con:
            assert (
                con.execute(
                    "select count(*) from rlm_workbench_artifact_stages where state='committed'"
                ).fetchone()[0]
                == 1
            )
            assert (
                con.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        host.close()


@pytest.mark.parametrize("route", ["inline", "manual"])
def test_required_named_success_routes(tmp_path, route):
    db = tmp_path / "registry.sqlite"
    prepare_v6_registry(db, tmp_path / "snapshot.sqlite")
    host = ReferenceHost(
        db, now_ms=lambda: FIXED_NOW_MS, enable_durable_dispatch=True, dispatcher_concurrency=1
    )
    try:
        req = workbench_request(
            host, require_named_artifacts=True, execution_mode="caller_delegated", max_model_calls=2
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, req), req)
        ticket = host.caller_work.get(wait_for_pending_ticket(db))
        code = (
            "aar_complete({'answer':'ok','artifacts':[]}, {'a.txt': (b'abc', 'text/plain')})"
            if route == "inline"
            else "aar_artifact('a.txt', b'abc', 'text/plain')"
        )
        directive = {"kind": "execute_cell", "code": code, "expected_result_hint": None}
        commit_pending_ticket_success(
            host.caller_work,
            db,
            req,
            ticket,
            prefix="valid-cell",
            output_text=canonical_json_bytes(directive).decode(),
        )
        if route == "manual":
            ticket = host.caller_work.get(wait_for_pending_ticket(db))
            prompt = json.loads(ticket.root["request"]["prompt"])
            stages = prompt["last_committed_cell"]["artifact_stage_ids"]
            assert len(stages) == 1
            directive = {
                "kind": "finalize",
                "output": {"answer": "ok", "artifacts": []},
                "artifact_stage_ids": stages,
            }
            commit_pending_ticket_success(
                host.caller_work,
                db,
                req,
                ticket,
                prefix="valid-final",
                output_text=canonical_json_bytes(directive).decode(),
            )
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=15)
        assert terminal.state.value == "succeeded"
        snap = host.rlm_workbench.snapshot(admitted.operation)
        assert len(snap.result.artifact_bindings) == 1
    finally:
        host.close()


@pytest.mark.parametrize(
    "fault", ["forged", "conflict", "rollback", "pending_cell", "marked_ticket", "lost_fence"]
)
def test_uncertain_ownership_never_becomes_certain(tmp_path, monkeypatch, fault):
    db = tmp_path / "registry.sqlite"
    prepare_v6_registry(db, tmp_path / "snapshot.sqlite")
    host = ReferenceHost(
        db, now_ms=lambda: FIXED_NOW_MS, enable_durable_dispatch=True, dispatcher_concurrency=1
    )
    guard_hits = []
    try:
        coordinator = host.rlm_workbench
        if fault == "forged":
            original = coordinator._completion_directive

            def forged(operation, *, connection=None):
                if connection is None:
                    raise _InvalidCompletionSelection("fake", "fake", "fake")
                return original(operation, connection=connection)

            monkeypatch.setattr(coordinator, "_completion_directive", forged)
        elif fault == "conflict":

            def conflict(*args, **kwargs):
                raise RlmWorkbenchConflict("synthetic authority conflict")

            monkeypatch.setattr(coordinator, "_completion_directive", conflict)
        elif fault == "rollback":
            original = coordinator.mark_planner_failure_terminal

            def rollback(*args, **kwargs):
                original(*args, **kwargs)
                raise RuntimeError("synthetic atomic failure")

            monkeypatch.setattr(coordinator, "mark_planner_failure_terminal", rollback)
        elif fault == "lost_fence":
            original = coordinator.mark_planner_failure_terminal

            def lost_fence(*args, **kwargs):
                def deny(*_args, **_kwargs):
                    guard_hits.append("lost_attempt_fence")
                    raise RlmWorkbenchConflict("synthetic lost attempt fence")

                with monkeypatch.context() as scoped:
                    scoped.setattr(coordinator, "_assert_attempt_fence", deny)
                    return original(*args, **kwargs)

            monkeypatch.setattr(coordinator, "mark_planner_failure_terminal", lost_fence)
        else:
            original = coordinator.mark_planner_failure_terminal

            def pending(*args, **kwargs):
                con = kwargs["connection"]
                if fault == "pending_cell":
                    con.execute("update rlm_workbench_cells set state='running'")
                else:
                    # A schema-valid counterfactual marked row must not retain
                    # terminal settlement fields. The predecessor fixture hit
                    # the SQLite CHECK before the unresolved-work guard.
                    con.execute(
                        "update caller_work_tickets set state='send_started', "
                        "settled_receipt_digest=NULL, settled_at_unix_ms=NULL"
                    )
                try:
                    return original(*args, **kwargs)
                except RlmWorkbenchConflict as exc:
                    guard_hits.append(str(exc))
                    raise

            monkeypatch.setattr(coordinator, "mark_planner_failure_terminal", pending)
        req = workbench_request(
            host, require_named_artifacts=True, execution_mode="caller_delegated", max_model_calls=2
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, req), req)
        ticket = host.caller_work.get(wait_for_pending_ticket(db))
        code = "aar_complete({'answer':'ok','artifacts':[]})"
        directive = {"kind": "execute_cell", "code": code, "expected_result_hint": None}
        commit_pending_ticket_success(
            host.caller_work,
            db,
            req,
            ticket,
            prefix="fault",
            output_text=canonical_json_bytes(directive).decode(),
        )
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=15)
        assert terminal.state.value == "indeterminate"
        if fault in ("pending_cell", "marked_ticket"):
            assert guard_hits == ["planner terminalization has unresolved work"]
        if fault == "lost_fence":
            assert guard_hits == ["lost_attempt_fence"]
        snap = coordinator.snapshot(admitted.operation)
        assert snap.phase == "parked" and snap.certainty == "indeterminate"
        with sqlite3.connect(db) as con:
            assert (
                con.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        host.close()
