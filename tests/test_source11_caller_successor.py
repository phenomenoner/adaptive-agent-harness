"""Controlled caller-root nested worker continuation regressions."""

import json
import sqlite3

import pytest
from test_rlm_workbench import BrokerRoundTripPlanner, commit_pending_ticket_success
from test_source10_parent import OUTPUT, counts, execute, make_case
from test_source10_parent import candidate_worker_bootstrap as candidate_worker_bootstrap

from aar.rlm_workbench_models import RlmWorkbenchResult


@pytest.mark.parametrize("timing", ["before", "after", "none"])
def test_nested_worker_successor_resumes_original_cell(tmp_path, timing):
    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        before = f"aar_complete({OUTPUT!r})\n" if timing == "before" else ""
        after = f"\naar_complete({OUTPUT!r})" if timing == "after" else "\n42"
        code = (
            "runs = globals().get('runs', 0) + 1\n"
            + before
            + f"value = aar_broker({BrokerRoundTripPlanner().request!r})\n"
            + "assert runs == 1 and value['observation']['output_text'] == 'ok'"
            + after
        )
        assert case.send(execute(code)).phase == "waiting_external"
        with sqlite3.connect(case.database) as db:
            (cell_id,) = db.execute("SELECT cell_execution_id FROM rlm_workbench_cells").fetchone()
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
        assert (
            commit_pending_ticket_success(
                case.host.caller_work,
                case.database,
                case.request,
                case.pending(),
                prefix="source11-nested",
                output_text="ok",
            ).state
            == "settled_success"
        )
        result = case.run()
        with sqlite3.connect(case.database) as db:
            rows = db.execute(
                "SELECT cell_execution_id,state,result_json FROM rlm_workbench_cells"
            ).fetchall()
        assert len(rows) == 1 and rows[0][0] == cell_id and rows[0][1] == "committed"
        assert json.loads(rows[0][2])["status"] == "succeeded"
        if timing == "none":
            assert result.phase == "waiting_external"
            assert counts(case)["rlm_workbench_finalization_manifests"] == 0
            prompt = json.loads(case.pending().root["request"]["prompt"])
            assert prompt["last_committed_cell"]["result"]["status"] == "succeeded"
            result = case.send({"kind": "finalize", "output": OUTPUT, "artifact_stage_ids": []})
            assert counts(case)["caller_work_tickets"] == 3
        else:
            assert counts(case)["caller_work_tickets"] == 2
        assert isinstance(result, RlmWorkbenchResult)
        assert result.root["output"] == OUTPUT
        assert result.root["model_calls"] == (2 if timing == "none" else 1)
        assert len(result.root["usage_receipt_digests"]) == (2 if timing == "none" else 1)
        assert result.root["python_cells"] == 1
        assert counts(case)["rlm_workbench_finalization_manifests"] == 1
    finally:
        case.host.close()


@pytest.mark.parametrize("condition", ["pending", "stale", "cancelled", "deadline"])
def test_nested_successor_rejects_unsafe_claim(tmp_path, condition):
    from aar.canonical import canonical_sha256
    from aar.runtime.registry import RegistryError

    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        code = f"aar_complete({OUTPUT!r})\naar_broker({BrokerRoundTripPlanner().request!r})"
        assert case.send(execute(code)).phase == "waiting_external"
        if condition != "pending":
            assert (
                commit_pending_ticket_success(
                    case.host.caller_work,
                    case.database,
                    case.request,
                    case.pending(),
                    prefix="source11-guard",
                    output_text="ok",
                ).state
                == "settled_success"
            )
        if condition == "stale":
            with sqlite3.connect(case.database) as db:
                db.execute("UPDATE rlm_workbench_attempt_authority SET cell_execution_id='stale'")
        elif condition == "cancelled":
            assert (
                case.host.cancel(case.record.operation, reason_code="user_requested").state.value
                == "cancelled"
            )
        elif condition == "deadline":
            with sqlite3.connect(case.database) as db:
                db.execute("UPDATE rlm_workbench_jobs SET cumulative_deadline_unix_ms=1")

        def claim():
            return case.host.registry.claim_next(
                case.host.runtime_generation,
                case.host.runtime_generation,
                canonical_sha256("source11-guard-owner"),
                30000,
            )

        if condition in {"stale", "deadline"}:
            with pytest.raises(RegistryError):
                claim()
        else:
            assert claim() is None
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("SELECT COUNT(*) FROM rlm_workbench_rebind_transfers").fetchone()[0] == 0
            )
            assert db.execute("SELECT COUNT(*) FROM rlm_workbench_cells").fetchone()[0] == 1
    finally:
        case.host.close()


def test_completion_before_broker_not_published_after_cell_failure(tmp_path):
    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        code = (
            f"aar_complete({OUTPUT!r})\n"
            f"aar_broker({BrokerRoundTripPlanner().request!r})\n"
            "raise RuntimeError('controlled failure')"
        )
        assert case.send(execute(code)).phase == "waiting_external"
        assert (
            commit_pending_ticket_success(
                case.host.caller_work,
                case.database,
                case.request,
                case.pending(),
                prefix="source11-failure",
                output_text="ok",
            ).state
            == "settled_success"
        )
        assert case.run().phase == "waiting_external"
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
        with sqlite3.connect(case.database) as db:
            row = db.execute("SELECT result_json FROM rlm_workbench_cells").fetchone()
            assert json.loads(row[0])["status"] == "failed"
    finally:
        case.host.close()


def test_nested_successor_duplicate_claim_and_wrong_fence(tmp_path):
    from aar.canonical import canonical_sha256
    from aar.runtime.registry import RegistryError

    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        code = f"aar_broker({BrokerRoundTripPlanner().request!r})"
        assert case.send(execute(code)).phase == "waiting_external"
        assert (
            commit_pending_ticket_success(
                case.host.caller_work,
                case.database,
                case.request,
                case.pending(),
                prefix="source11-duplicate",
                output_text="ok",
            ).state
            == "settled_success"
        )
        owner = canonical_sha256("source11-claim")
        args = (case.host.runtime_generation, case.host.runtime_generation, owner, 30000)
        claim = case.host.registry.claim_next(*args)
        assert claim is not None
        assert case.host.registry.claim_next(*args) is None
        with pytest.raises(RegistryError):
            case.host.registry.prepared_workbench_rebind(
                claim.attempt,
                claim.dispatcher_generation,
                claim.lease_epoch,
                canonical_sha256("wrong-owner"),
            )
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
        with sqlite3.connect(case.database) as db:
            assert db.execute("SELECT state FROM rlm_workbench_rebind_transfers").fetchall() == [
                ("prepared",)
            ]
    finally:
        case.host.close()
