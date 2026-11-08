from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from aar.rlm_workbench_models import RlmWorkbenchJobSpec, RlmWorkbenchSnapshot
from aar.runtime.ipython_worker import _Worker
from aar.runtime.rlm_workbench import _contract_documents, _planner_request

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "docs/sdd/aar-rlm-native-workbench-v2/fixtures/valid-workbench-execute.json"
HELPERS = {"aar_artifact", "aar_broker", "aar_display", "aar_progress"}


def planner_request(last_cell=None):
    payload = json.loads(FIXTURE.read_text())
    spec = RlmWorkbenchJobSpec.model_validate(payload["spec"], strict=True)
    snapshot = cast(
        RlmWorkbenchSnapshot,
        SimpleNamespace(root={"phase": "planning", "controlled_fixture": True}),
    )
    request = _planner_request(
        spec,
        owner={"phase": "initial", "step_index": 0},
        snapshot=snapshot,
        last_committed_cell=last_cell,
    )
    return request, json.loads(request["prompt"]), spec


@pytest.mark.parametrize("last_cell", [None, {"cell_index": 0, "artifact_stage_ids": []}])
def test_every_planner_request_discloses_injected_callable_helpers(last_cell):
    _request, prompt, _spec = planner_request(last_cell)
    instructions = prompt["instructions"]
    assert "injected global callables" in instructions
    assert "not importable modules" in instructions
    assert all(name + "(" in instructions for name in HELPERS)
    assert "artifact_index" in instructions
    assert "last_committed_cell.artifact_stage_ids" in instructions
    assert "not stage IDs" in instructions
    assert len(instructions.encode("utf-8")) < 2048


def test_guidance_preserves_objective_feedback_route_and_response_contract():
    last = {
        "cell_index": 2,
        "artifact_stage_ids": ["controlled-stage"],
        "result": {"events": [{"kind": "stdout", "text": "retained feedback"}]},
    }
    before = copy.deepcopy(last)
    request, prompt, spec = planner_request(last)
    assert prompt["objective"] == spec.root["objective"]
    assert prompt["last_committed_cell"] == before
    assert request["route_binding"] == spec.root["model"]["route_binding"]
    assert (
        request["response_contract"]["max_instance_bytes"]
        == spec.root["budgets"]["max_result_bytes"]
    )
    expected = _contract_documents()["aar-rlm-workbench-v1.schema.json"]["$defs"]["RlmDirective"]
    assert request["response_contract"]["schema"] == expected
    assert last == before


def test_advertised_callable_and_artifact_return_shape_matches_actual_ipython_worker():
    worker = _Worker()
    prior = dict(worker.shell.user_ns)
    try:
        result = worker.execute(
            {
                "code": "\n".join(
                    [
                        "import inspect",
                        "helper_names = ('aar_artifact', 'aar_broker', "
                        "'aar_display', 'aar_progress')",
                        "assert all(callable(globals()[name]) for name in helper_names)",
                        "params = lambda f: list(inspect.signature(f).parameters)",
                        "assert params(aar_artifact) == ['name', 'content', 'media_type']",
                        "assert params(aar_display) == ['value', 'media_type']",
                        "assert params(aar_progress) == ['message', 'completed', 'total']",
                        "assert params(aar_broker) == ['value']",
                        "artifact_result = aar_artifact('example.txt', "
                        "'example body', 'text/plain')",
                        "assert artifact_result == {'artifact_index': 0}",
                        "aar_display({'ok': True})",
                        "aar_progress('controlled', completed=1, total=1)",
                    ]
                ),
                "max_events": 32,
                "max_output_chars": 8192,
                "artifact_enabled": True,
                "broker_enabled": False,
            }
        )
        assert result["status"] == "succeeded"
        assert len(result["artifacts"]) == 1
        artifact = result["artifacts"][0]
        assert artifact["name"] == "example.txt"
        assert artifact["media_type"] == "text/plain"
        assert base64.b64decode(artifact["content_base64"]) == b"example body"
        assert not any(event["kind"] == "exception" for event in result["events"])
    finally:
        worker.shell.user_ns.clear()
        worker.shell.user_ns.update(prior)


@pytest.mark.parametrize("last_cell", [None, {"cell_index": 0, "artifact_stage_ids": []}])
@pytest.mark.parametrize(
    ("allowed_media_types", "max_count", "max_bytes"),
    [
        (["text/plain"], 1, 2048),
        (["application/octet-stream", "application/json"], 3, 8192),
    ],
)
def test_planner_discloses_admitted_artifact_policy_without_granting_more(
    last_cell, allowed_media_types, max_count, max_bytes
):
    payload = json.loads(FIXTURE.read_text())
    payload["spec"]["artifacts"]["allowed_media_types"] = allowed_media_types
    payload["spec"]["budgets"].update(
        max_artifact_count=max_count, max_artifact_bytes=max_bytes
    )
    spec = RlmWorkbenchJobSpec.model_validate(payload["spec"], strict=True)
    before = copy.deepcopy(spec.root)
    request = _planner_request(
        spec,
        owner={"phase": "controlled-policy-test", "step_index": 0},
        snapshot=cast(RlmWorkbenchSnapshot, SimpleNamespace(root={"phase": "planning"})),
        last_committed_cell=last_cell,
    )
    prompt = json.loads(request["prompt"])
    assert prompt["artifact_policy"] == {
        **before["artifacts"],
        "max_artifact_count": max_count,
        "max_artifact_bytes": max_bytes,
    }
    assert "helper default is not an authorization" in prompt["instructions"]
    assert request["route_binding"] == before["model"]["route_binding"]
    assert request["max_output_bytes"] == before["budgets"]["max_result_bytes"]
    assert prompt["last_committed_cell"] == last_cell
    prompt["artifact_policy"]["allowed_media_types"].append("application/not-admitted")
    assert spec.root == before
