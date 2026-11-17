"""Focused stateless context edge cases; no provider calls."""

import copy
import json
import sqlite3

import pytest
from test_lifecycle_planner_context import (  # noqa: F401 (imported autouse fixture)
    cell,
    prompt,
    selected_candidate_worker,
)
from test_rlm_workbench import workbench_envelope
from test_workbench_cell_feedback import CallerCase
from test_workbench_helper_guidance import planner_request

from aar.canonical import canonical_sha256
from aar.runtime.rlm_workbench import _planner_prompt


def test_failed_stage_excluded_and_budget_counts_cells(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        initial = prompt(case)
        maximum = case.request.root["spec"]["budgets"]["max_cells"]
        assert initial["execution_budget"]["remaining_cells"] == maximum
        directive = cell(
            "aar_artifact('partial.txt', 'bad', 'text/plain')\nraise ValueError('failed')"
        )
        case.send(directive)
        current = prompt(case)
        assert current["execution_budget"]["remaining_cells"] == maximum - 1
        assert current["last_committed_cell"]["source"] == directive
        assert current["last_committed_cell"]["source_digest"] == canonical_sha256(directive)
        assert current["last_committed_cell"]["artifact_stage_ids"] == []
        assert current["committed_artifacts"] == []
    finally:
        case.host.close()


def test_correction_preserves_context_and_cell_budget(tmp_path):
    case = CallerCase(tmp_path, max_calls=5)
    try:
        case.run()
        case.send(cell("aar_artifact('result.txt', 'ok', 'text/plain')"))
        before = prompt(case)
        case.send({"kind": "not-a-directive"})
        after = prompt(case)
        for name in ("completion_contract", "committed_artifacts", "last_committed_cell"):
            assert after[name] == before[name]
        assert (
            after["execution_budget"]["remaining_cells"]
            == before["execution_budget"]["remaining_cells"]
        )
        assert (
            after["execution_budget"]["remaining_model_calls"]
            == before["execution_budget"]["remaining_model_calls"] - 1
        )
        assert after["planner_owner"]["phase"] == "correction"
    finally:
        case.host.close()


def test_foreign_operation_stage_is_not_disclosed(tmp_path):
    case = CallerCase(tmp_path, max_calls=4)
    try:
        case.run()
        case.send(cell("aar_artifact('foreign.txt', 'secret', 'text/plain')"))
        assert len(prompt(case)["committed_artifacts"]) == 1
        document = copy.deepcopy(case.request.root)
        document["context"]["request_id"] = "other-request"
        document["context"]["idempotency_key"] = "other-idempotency"
        other = type(case.request).model_validate(document, strict=True)
        case.record = case.host.submit_rlm_workbench(workbench_envelope(case.host, other), other)
        case.run()
        with sqlite3.connect(case.database) as db:
            ticket_id = db.execute(
                "SELECT ticket_id FROM rlm_workbench_suspensions WHERE operation_id = ?",
                (case.record.operation.value,),
            ).fetchone()[0]
        other_prompt = json.loads(case.host.caller_work.get(ticket_id).root["request"]["prompt"])
        assert other_prompt["committed_artifacts"] == []
    finally:
        case.host.close()


def test_source_size_omission_and_mandatory_overflow():
    directive = cell("x" * 65536)
    last = {
        "source": directive,
        "source_digest": canonical_sha256(directive),
        "result": {"status": "succeeded"},
    }
    request, document, spec = planner_request(last)
    assert len(request["prompt"]) <= 65536
    assert "source" not in document["last_committed_cell"]
    assert document["last_committed_cell"]["source_digest"] == canonical_sha256(directive)
    assert "omitted" in document["last_committed_cell"]["source_omitted"].lower()
    assert document["completion_contract"] == spec.root["completion"]
    assert document["last_committed_cell"]["result"] == last["result"]
    short = copy.deepcopy(document)
    short["objective"] = "x" * 65536
    from aar.runtime.rlm_workbench import RlmWorkbenchPromptCapacityExceeded

    with pytest.raises(RlmWorkbenchPromptCapacityExceeded) as observed:
        _planner_prompt(short)
    assert observed.value.observed_characters > 65536
    assert observed.value.maximum_characters == 65536
    assert "x" * 100 not in str(observed.value)
