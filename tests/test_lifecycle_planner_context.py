"""Stateless caller-planner lifecycle discriminators, no provider/model call."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from test_workbench_cell_feedback import CallerCase

import aar
import aar.runtime.ipython_backend as backend
from aar.canonical import canonical_sha256


@pytest.fixture(autouse=True)
def selected_candidate_worker(monkeypatch):
    source = Path(aar.__file__).resolve().parent.parent
    expected = Path(__file__).resolve().parents[1] / "src"
    assert source == expected, (
        "Tests must import the prospective candidate, not retained installed SDK"
    )

    def command(module, arguments=(), *, isolated):
        assert isolated and module == "aar.runtime.ipython_worker"
        bootstrap = (
            f"import sys,runpy;sys.path.insert(0,{str(source)!r});"
            f'runpy.run_module({module!r},run_name="__main__")'
        )
        return [sys.executable, "-I", "-B", "-c", bootstrap, *arguments]

    monkeypatch.setattr(backend, "exact_module_command", command)


def cell(code):
    return {"kind": "execute_cell", "code": code, "expected_result_hint": None}


def prompt(case):
    return json.loads(case.pending().root["request"]["prompt"])


def test_stateless_planner_receives_actual_output_contract(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        current = prompt(case)
        assert current.get("completion_contract") == case.request.root["spec"]["completion"], (
            "The native output schema is not the directive response schema; "
            "expose the admitted completion contract"
        )
    finally:
        case.host.close()


def test_successful_committed_cell_source_and_remaining_cells_are_visible(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        directive = cell("draft_answer = '42'\nprint('draft validated')")
        case.send(directive)
        current = prompt(case)
        assert current["last_committed_cell"].get("source") == directive, (
            "A stateless planner must see its own successful action as well as the output"
        )
        assert current["last_committed_cell"]["source_digest"] == canonical_sha256(directive)
        assert (
            current["execution_budget"]["remaining_cells"]
            == case.request.root["spec"]["budgets"]["max_cells"] - 1
        )
    finally:
        case.host.close()


def test_artifacts_remain_discoverable_after_intervening_verification_cell(tmp_path):
    case = CallerCase(tmp_path, max_calls=4)
    try:
        case.run()
        case.send(cell("aar_artifact('reports/result.txt', '42', 'text/plain')"))
        initial = prompt(case)
        stage_ids = initial["last_committed_cell"]["artifact_stage_ids"]
        assert len(stage_ids) == 1
        case.send(cell("assert 6 * 7 == 42\nprint('verification completed')"))
        current = prompt(case)
        assert current["last_committed_cell"]["artifact_stage_ids"] == []
        inventory = current.get("committed_artifacts", [])
        assert {item["stage_id"] for item in inventory} == set(stage_ids), (
            "Finalization must not depend on a stateless model remembering an "
            "older vanished request"
        )
        assert [item["logical_name"] for item in inventory] == ["reports/result.txt"]
        final = case.send(
            {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": [item["stage_id"] for item in inventory],
            }
        )
        assert len(final.artifact_bindings) == 1
        assert case.host.rlm_workbench.snapshot(case.record.operation).phase == "succeeded"
    finally:
        case.host.close()
