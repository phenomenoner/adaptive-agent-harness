"""Generate and verify the digest-bound MCP tool surface and operation skill assets."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from aar.canonical import pretty_json_bytes
from aar.mcp.server import (
    FIXTURE_SET_DIGEST,
    MCP_PROTOCOL_VERSIONS,
    MCP_TOOL_SURFACE_VERSION,
    OPERATION_SKILL_VERSION,
    SCHEMA_BUNDLE_DIGEST,
    build_server,
    tool_surface_manifest,
)

TOOL_TOKEN = re.compile(r"`(aar_[a-z0-9_]+)`")


def skill_digest(skill_path: Path) -> str:
    return f"sha256:{hashlib.sha256(skill_path.read_bytes()).hexdigest()}"


async def asset_documents(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    skill_path = root / "skills" / "aar-operations" / "SKILL.md"
    with tempfile.TemporaryDirectory(prefix="aar-mcp-assets-") as directory:
        application = build_server(Path(directory) / "reference.sqlite3")
        try:
            tool_manifest = await tool_surface_manifest(application.server)
        finally:
            application.close()

    metadata = {
        "skill_name": "aar-operations",
        "skill_version": OPERATION_SKILL_VERSION,
        "skill_digest": skill_digest(skill_path),
        "tool_surface_version": MCP_TOOL_SURFACE_VERSION,
        "tool_surface_digest": tool_manifest["tool_surface_digest"],
        "protocol_versions": list(MCP_PROTOCOL_VERSIONS),
        "schema_bundle_digest": SCHEMA_BUNDLE_DIGEST,
        "fixture_set_digest": FIXTURE_SET_DIGEST,
    }
    return tool_manifest, metadata


def _paths(root: Path) -> tuple[Path, Path]:
    return (
        root / "schemas" / "aar-mcp-tools-v8-combined.json",
        root / "skills" / "aar-operations" / "metadata.json",
    )


def generate_assets(root: Path) -> tuple[Path, Path]:
    tool_manifest, metadata = asyncio.run(asset_documents(root))
    tool_path, metadata_path = _paths(root)
    tool_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    tool_path.write_bytes(pretty_json_bytes(tool_manifest))
    metadata_path.write_bytes(pretty_json_bytes(metadata))
    return tool_path, metadata_path


def verify_assets(root: Path) -> list[str]:
    errors: list[str] = []
    tool_path, metadata_path = _paths(root)
    expected_tool, expected_metadata = asyncio.run(asset_documents(root))
    try:
        checked_tool = json.loads(tool_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"tool surface asset unavailable: {error}")
        checked_tool = None
    try:
        checked_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"operation skill metadata unavailable: {error}")
        checked_metadata = None
    if checked_tool != expected_tool:
        errors.append("checked-in MCP tool surface is stale")
    if checked_metadata != expected_metadata:
        errors.append("checked-in operation skill metadata is stale")

    declared_tools = {tool["name"] for tool in expected_tool["tools"]}
    skill_text = (root / "skills" / "aar-operations" / "SKILL.md").read_text(encoding="utf-8")
    referenced_tools = set(TOOL_TOKEN.findall(skill_text))
    for tool_name in sorted(declared_tools - referenced_tools):
        errors.append(f"operation skill omits public tool: {tool_name}")
    for tool_name in sorted(referenced_tools - declared_tools):
        errors.append(f"operation skill references unknown tool: {tool_name}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "generate":
        for path in generate_assets(root):
            print(path.relative_to(root))
        return 0
    errors = verify_assets(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("MCP assets verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
