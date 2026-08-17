from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
FIXTURES = SDD / "fixtures"
CONTRACTS = SDD / "contracts"

VALID_EXECUTE_FIXTURES = (
    "valid-workbench-execute.json",
    "valid-schema-portable-pattern.json",
)

INVALID_EXECUTE_FIXTURES = (
    "invalid-caller-start-only-false.json",
    "invalid-job-supplied-grants.json",
    "invalid-route-missing-profile-digest.json",
    "invalid-route-fallback-explicit.json",
    "invalid-planner-schema-override.json",
    "invalid-remote-schema-ref.json",
    "invalid-schema-format.json",
    "invalid-schema-pattern-properties.json",
    "invalid-schema-missing-local-ref.json",
    "invalid-schema-lookbehind.json",
    "invalid-schema-python-anchors.json",
    "invalid-schema-nested-quantifier.json",
    "invalid-schema-possessive-quantifier.json",
    "invalid-schema-alternation.json",
    "invalid-schema-overlapping-quantified-atoms.json",
    "invalid-schema-trailing-end-anchor.json",
    "invalid-schema-ecmascript-identity-escape.json",
    "invalid-schema-depth.json",
    "invalid-schema-instance-limit.json",
)

INVALID_CAPABILITY_FIXTURES = (
    "invalid-capability-schema-digest.json",
    "invalid-capability-reference-only.json",
    "invalid-capability-planner-digest.json",
)

VALID_WORKER_FIXTURES = tuple(
    path.name for path in sorted(FIXTURES.glob("valid-worker-*.json"))
)
INVALID_WORKER_FIXTURES = tuple(
    path.name for path in sorted(FIXTURES.glob("invalid-worker-*.json"))
)


def fixture(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("fixture_name", VALID_EXECUTE_FIXTURES)
def test_runtime_workbench_model_accepts_reviewed_execute_fixture(
    fixture_name: str,
) -> None:
    from aar.rlm_workbench_models import RlmWorkbenchExecuteInput

    payload = fixture(fixture_name)
    model = RlmWorkbenchExecuteInput.model_validate(payload, strict=True)
    assert model.model_dump(mode="json") == payload


@pytest.mark.parametrize("fixture_name", INVALID_EXECUTE_FIXTURES)
def test_runtime_workbench_model_rejects_reviewed_invalid_execute_fixture(
    fixture_name: str,
) -> None:
    from aar.rlm_workbench_models import RlmWorkbenchExecuteInput

    with pytest.raises(ValidationError):
        RlmWorkbenchExecuteInput.model_validate(fixture(fixture_name), strict=True)


def test_runtime_capability_model_accepts_only_truthful_reviewed_capability() -> None:
    from aar.rlm_workbench_models import RlmWorkbenchCapability

    payload = fixture("valid-workbench-capabilities.json")
    model = RlmWorkbenchCapability.model_validate(payload, strict=True)
    assert model.model_dump(mode="json") == payload


@pytest.mark.parametrize("fixture_name", INVALID_CAPABILITY_FIXTURES)
def test_runtime_capability_model_rejects_false_backend_or_schema_claim(
    fixture_name: str,
) -> None:
    from aar.rlm_workbench_models import RlmWorkbenchCapability

    with pytest.raises(ValidationError):
        RlmWorkbenchCapability.model_validate(fixture(fixture_name), strict=True)


@pytest.mark.parametrize("fixture_name", VALID_WORKER_FIXTURES)
def test_runtime_worker_frame_model_accepts_reviewed_frame(fixture_name: str) -> None:
    from aar.rlm_workbench_models import WorkspaceBrokerFrame

    payload = fixture(fixture_name)
    model = WorkspaceBrokerFrame.model_validate(payload, strict=True)
    assert model.model_dump(mode="json") == payload


@pytest.mark.parametrize("fixture_name", INVALID_WORKER_FIXTURES)
def test_runtime_worker_frame_model_rejects_reviewed_invalid_frame(
    fixture_name: str,
) -> None:
    from aar.rlm_workbench_models import WorkspaceBrokerFrame

    with pytest.raises(ValidationError):
        WorkspaceBrokerFrame.model_validate(fixture(fixture_name), strict=True)


def test_frozen_v7_manifest_bytes_and_v8_prefix_remain_exact() -> None:
    binding = json.loads(
        (CONTRACTS / "aar-mcp-tools-v7-binding.json").read_text(encoding="utf-8")
    )
    v7_bytes = (ROOT / "schemas" / "aar-mcp-tools-v7.json").read_bytes()
    v7 = json.loads(v7_bytes)
    v8 = json.loads(
        (CONTRACTS / "aar-mcp-tools-v8-combined.json").read_text(encoding="utf-8")
    )

    assert len(v7_bytes) == binding["size_bytes"]
    assert hashlib.sha256(v7_bytes).hexdigest() == binding["sha256"].removeprefix(
        "sha256:"
    )
    assert len(v7["tools"]) == binding["tool_count"] == 30
    assert [tool["name"] for tool in v7["tools"]] == binding["tool_names"]
    assert v8["tools"][:30] == v7["tools"]
    assert [tool["name"] for tool in v8["tools"][:30]] == binding["tool_names"]


def test_source_generator_reproduces_reviewed_v8_manifest() -> None:
    from aar.schema_generator import build_mcp_tools_v8_manifest

    reviewed = json.loads(
        (CONTRACTS / "aar-mcp-tools-v8-combined.json").read_text(encoding="utf-8")
    )
    assert build_mcp_tools_v8_manifest(ROOT) == reviewed
