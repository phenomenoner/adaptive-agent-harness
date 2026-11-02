"""Boundary checks for owner-budgeted time and shared workspace ranges."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from aar.provider_ready_models import ActivationGrantPolicy, GrantBudgetCeiling
from aar.provider_ready_runtime_models import WorkbenchGrantSet
from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.workspace_models import WorkspaceProgramSpec
from aar.schema_generator import build_mcp_tools_v8_manifest, build_mcp_tools_v9_manifest
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


@pytest.mark.parametrize("wall_time_ms", [900_001, 3_600_000, 86_400_000])
def test_grant_time_is_owner_configurable_beyond_old_quarter_hour(wall_time_ms: int) -> None:
    budget = GrantBudgetCeiling(
        wall_time_ms=wall_time_ms,
        model_requests=1,
        input_tokens=10,
        output_tokens=10,
        child_operations=0,
        artifact_bytes=0,
    )
    assert budget.wall_time_ms == wall_time_ms


@pytest.mark.parametrize("wall_time_ms", [60_001, 3_600_000])
def test_cell_time_does_not_hide_a_one_minute_policy_cap(wall_time_ms: int) -> None:
    assert WorkspaceProgramSpec(code="1", wall_time_ms=wall_time_ms).wall_time_ms == wall_time_ms


@pytest.mark.parametrize("events", [1, 7, 256, 257, 512])
@pytest.mark.parametrize("output", [1, 255, 65_537, 262_144])
def test_workbench_advertised_event_and_output_range_is_constructible(
    events: int, output: int
) -> None:
    spec = WorkspaceProgramSpec(code="1", max_events=events, max_output_chars=output)
    assert spec.max_events == events
    assert spec.max_output_chars == output


@pytest.mark.parametrize(
    "fields",
    [
        {"wall_time_ms": True},
        {"wall_time_ms": 0},
        {"wall_time_ms": 2**63},
        {"max_events": 0},
        {"max_events": 513},
        {"max_output_chars": 0},
        {"max_output_chars": 262_145},
    ],
)
def test_relaxed_policy_keeps_strict_scalar_and_technical_resource_bounds(fields: dict) -> None:
    with pytest.raises(ValidationError):
        WorkspaceProgramSpec(code="1", **fields)


@pytest.mark.parametrize(
    "model,field", [(ActivationGrantPolicy, "max_deadline_ms"), (WorkbenchGrantSet, "max_ttl_ms")]
)
def test_owner_authority_fields_allow_long_time_without_scalar_coercion(model, field: str) -> None:
    adapter = TypeAdapter(model.model_fields[field].rebuild_annotation())
    assert adapter.validate_python(3_600_000) == 3_600_000
    for bad in (True, 999, 2**63, "3600000"):
        with pytest.raises(ValidationError):
            adapter.validate_python(bad)


def test_additive_v9_keeps_frozen_v7_prefix_and_v8_input_bytes() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    frozen = root / "schemas/aar-mcp-tools-v8-combined.json"
    before = frozen.read_bytes()
    old = build_mcp_tools_v8_manifest(root)
    current = build_mcp_tools_v9_manifest(root)
    assert current["tool_surface_version"] == "aar.mcp-tools.v9"
    assert len(current["tools"]) == 38
    assert current["tools"][:30] == old["tools"][:30]
    assert [t["name"] for t in current["tools"]] == [t["name"] for t in old["tools"]]
    assert frozen.read_bytes() == before


@pytest.mark.parametrize("events,output", [(1, 1), (512, 262_144)])
def test_actual_ipython_backend_accepts_the_advertised_range(events: int, output: int) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    handle = backend.create(
        WorkspaceRef(value=f"limits-workspace-{events}"),
        SessionRef(value=f"limits-session-{events}"),
    )
    try:
        result = backend.execute(
            OperationRef(value=f"limits-operation-{events}"),
            handle,
            WorkspaceProgramSpec(
                code="answer = 42\nanswer",
                wall_time_ms=60_001,
                max_events=events,
                max_output_chars=output,
            ),
        )
        assert result.status == "succeeded"
        assert result.result == 42
        assert len(result.events) <= events
    finally:
        backend.shutdown()
