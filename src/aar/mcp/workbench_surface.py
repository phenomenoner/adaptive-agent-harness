"""Frozen successor-only MCP v8 schema bindings and v7 compatibility assets."""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any

from mcp.types import ToolAnnotations

SUCCESSOR_SURFACE_VERSION = "aar.mcp-tools.v8"
SUCCESSOR_TOOL_NAMES = (
    "aar_rlm_workbench_execute",
    "aar_rlm_workbench_capabilities",
    "aar_rlm_workbench_status",
    "aar_broker_work_claim",
    "aar_broker_work_mark_send_started",
    "aar_broker_work_cancel_before_send",
    "aar_broker_work_commit",
    "aar_broker_work_reconcile",
)


def is_successor_compatible_tool_order(
    discovered: list[str] | tuple[str, ...],
    frozen_v7: list[str] | tuple[str, ...],
) -> bool:
    """Accept an exact frozen v7 prefix followed only by the reviewed v8 additions."""

    predecessor = tuple(frozen_v7)
    observed = tuple(discovered)
    return (
        observed[: len(predecessor)] == predecessor
        and observed[len(predecessor) :] == SUCCESSOR_TOOL_NAMES
    )


def _source_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_json(candidates: tuple[Path, ...], bundled_name: str) -> dict[str, Any]:
    for candidate in candidates:
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    bundled = importlib.resources.files("aar").joinpath("bundled", "schemas", bundled_name)
    return json.loads(bundled.read_text(encoding="utf-8"))


def successor_surface_contract() -> dict[str, Any]:
    """Load the reviewed v8 combined projection used to bind runtime tool schemas."""

    root = _source_root()
    return _read_json(
        (
            root
            / "docs"
            / "sdd"
            / "aar-rlm-native-workbench-v2"
            / "contracts"
            / "aar-mcp-tools-v8-combined.json",
            root / "schemas" / "aar-mcp-tools-v8-combined.json",
        ),
        "aar-mcp-tools-v8-combined.json",
    )


def frozen_v7_surface() -> dict[str, Any]:
    """Load the exact predecessor surface reported by frozen aar_capabilities."""

    root = _source_root()
    return _read_json(
        (root / "schemas" / "aar-mcp-tools-v7.json",),
        "aar-mcp-tools-v7.json",
    )


def bind_successor_tool_contract(server: Any, name: str) -> None:
    """Replace generated schemas/metadata with the reviewed frozen successor contract.

    The MCP SDK still owns argument conversion and invocation. The tool body performs a second
    strict contract validation before touching runtime state.
    """

    if name not in SUCCESSOR_TOOL_NAMES:
        raise ValueError(f"not a successor-only MCP tool: {name}")
    entry = next(tool for tool in successor_surface_contract()["tools"] if tool["name"] == name)
    tool = server._tool_manager._tools[name]
    tool.title = entry["title"]
    tool.description = entry["description"]
    tool.parameters = entry["inputSchema"]
    tool.fn_metadata.output_schema = entry["outputSchema"]
    tool.annotations = ToolAnnotations.model_validate(entry["annotations"])
    tool.icons = entry["icons"]
    tool.meta = entry["_meta"]


def assert_successor_surface(manifest: dict[str, Any]) -> None:
    """Fail closed unless the live server exactly matches the reviewed v8 projection."""

    expected = successor_surface_contract()
    if manifest != expected:
        raise RuntimeError("live MCP successor surface differs from frozen v8 contract")
