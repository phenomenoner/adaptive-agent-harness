from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from aar.canonical import canonical_sha256
from aar.mcp.assets import asset_documents, verify_assets
from aar.mcp.server import FIXTURE_SET_DIGEST, SCHEMA_BUNDLE_DIGEST

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_TOOLS = (
    "aar_capabilities",
    "aar_reference_context",
    "aar_workspace_create",
    "aar_workspace_attach",
    "aar_workspace_execute",
    "aar_workspace_inspect",
    "aar_program_workspace_create",
    "aar_program_workspace_attach",
    "aar_program_workspace_execute",
    "aar_program_workspace_inspect",
    "aar_program_workspace_interrupt",
    "aar_program_workspace_checkpoint",
    "aar_program_workspace_restore",
    "aar_program_workspace_health",
    "aar_program_workspace_reconcile",
    "aar_program_workspace_close",
    "aar_asset_get",
    "aar_asset_export",
    "aar_asset_import",
    "aar_asset_outcome",
    "aar_rlm_execute",
    "aar_rlm_status",
    "aar_broker_catalog",
    "aar_broker_describe",
    "aar_operation_status",
    "aar_operation_events",
    "aar_operation_cancel",
    "aar_operation_reconcile",
    "aar_checkpoint_describe",
    "aar_artifact_resolve",
)


def test_checked_in_mcp_assets_match_executable_surface() -> None:
    assert verify_assets(ROOT) == []


def test_tool_manifest_is_deterministic_and_self_digesting() -> None:
    first_tool, first_metadata = asyncio.run(asset_documents(ROOT))
    second_tool, second_metadata = asyncio.run(asset_documents(ROOT))

    assert first_tool == second_tool
    assert first_metadata == second_metadata
    assert tuple(tool["name"] for tool in first_tool["tools"]) == EXPECTED_TOOLS
    core = {key: value for key, value in first_tool.items() if key != "tool_surface_digest"}
    assert first_tool["tool_surface_digest"] == canonical_sha256(core)


def test_tool_schemas_teach_the_exact_flat_context_shape() -> None:
    tool_manifest, _metadata = asyncio.run(asset_documents(ROOT))

    for tool in tool_manifest["tools"]:
        if tool["name"] in {"aar_capabilities", "aar_reference_context"}:
            continue
        input_schema = tool["inputSchema"]
        assert "context" in input_schema["required"]
        assert "mutation_context" not in input_schema["properties"]
        context_schema = input_schema["properties"]["context"]
        assert 'outer argument name: "context"' in context_schema["description"]
        context_name = context_schema["$ref"].rsplit("/", 1)[-1]
        context_definition = input_schema["$defs"][context_name]
        assert context_definition["additionalProperties"] is False
        assert "schema_version" not in context_definition["properties"]

    create_schema = next(
        tool["inputSchema"]
        for tool in tool_manifest["tools"]
        if tool["name"] == "aar_program_workspace_create"
    )
    mutation = create_schema["$defs"]["McpMutationContext"]
    assert mutation["required"] == [
        "runtime_generation",
        "capability_digest",
        "principal_id",
        "session_id",
        "deadline_unix_ms",
        "request_id",
        "idempotency_key",
        "grant_id",
        "budget_wall_time_ms",
    ]
    assert "grant" not in mutation["properties"]
    assert "budget" not in mutation["properties"]
    assert "reference_grants" in mutation["properties"]["grant_id"]["description"]


def test_skill_metadata_binds_exact_skill_and_tool_bytes() -> None:
    metadata = json.loads(
        (ROOT / "skills" / "aar-operations" / "metadata.json").read_text(
            encoding="utf-8"
        )
    )
    skill_bytes = (ROOT / "skills" / "aar-operations" / "SKILL.md").read_bytes()
    tool_manifest = json.loads(
        (ROOT / "schemas" / "aar-mcp-tools-v7.json").read_text(encoding="utf-8")
    )

    assert metadata["skill_digest"] == (
        f"sha256:{hashlib.sha256(skill_bytes).hexdigest()}"
    )
    assert metadata["tool_surface_digest"] == tool_manifest["tool_surface_digest"]
    assert metadata["skill_version"] == "0.9.1"
    skill_text = skill_bytes.decode("utf-8").lower()
    assert "tool use or analysis" in skill_text
    assert "software planning, development, testing, and troubleshooting" in skill_text
    assert "consider aar mcp early" in skill_text
    assert "host-owned model route" in skill_text
    assert "aar.model-receipt.v1" in skill_text
    assert "formal evaluation" in skill_text
    assert "openai-codex / gpt-5.6-luna / max" in skill_text
    assert "host-neutral workflow for any ai-agent client" in skill_text
    assert all(
        host_name not in skill_text
        for host_name in ("hermes agent", "codex app", "telegram", "nous research")
    )
    assert metadata["protocol_versions"] == [
        "2026-07-28",
        "2025-11-25",
        "2025-06-18",
        "2025-03-26",
        "2024-11-05",
    ]


def test_runtime_and_packaged_metadata_bind_canonical_schema_and_fixture_assets() -> None:
    schema = json.loads(
        (ROOT / "schemas" / "aar-schemas-v1.json").read_text(encoding="utf-8")
    )
    fixtures = json.loads(
        (ROOT / "tests" / "fixtures" / "manifest.json").read_text(encoding="utf-8")
    )
    assert schema["bundle_digest"] == SCHEMA_BUNDLE_DIGEST
    assert fixtures["fixture_set_digest"] == FIXTURE_SET_DIGEST

    metadata_paths = (
        ROOT / "skills" / "aar-operations" / "metadata.json",
        ROOT
        / "profiles"
        / "codex"
        / "plugins"
        / "adaptive-agent-runtime"
        / "skills"
        / "aar-operations"
        / "metadata.json",
        ROOT
        / "profiles"
        / "hermes"
        / "adaptive-agent-runtime"
        / "skills"
        / "aar-operations"
        / "metadata.json",
    )
    for path in metadata_paths:
        metadata = json.loads(path.read_text(encoding="utf-8"))
        assert metadata["schema_bundle_digest"] == schema["bundle_digest"]
        assert metadata["fixture_set_digest"] == fixtures["fixture_set_digest"]
