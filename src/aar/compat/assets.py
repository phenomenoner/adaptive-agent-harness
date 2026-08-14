"""Generate and verify Codex and Hermes bundles from the canonical AAR assets."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from aar.canonical import pretty_json_bytes
from aar.mcp.server import (
    MCP_PROTOCOL_VERSIONS,
    MCP_TOOL_SURFACE_VERSION,
    OPERATION_SKILL_VERSION,
)
from aar.versions import PACKAGE_VERSION

PROFILE_CONTRACT_VERSION = "aar.host-profiles.v1"
PROFILE_VERSION = "0.4.0"
CODEX_PROFILE_VERSION = "0.4.0+codex.20260814125137"
SKILL_FILES = ("SKILL.md", "agents/openai.yaml", "metadata.json")
OPTIONAL_SKILL_FILES = ("SKILL.md", "agents/openai.yaml")
CODEX_ROOT = Path("profiles/codex/plugins/adaptive-agent-runtime")
HERMES_ROOT = Path("profiles/hermes/adaptive-agent-runtime")


def _sha256(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _canonical_metadata(root: Path) -> dict[str, Any]:
    return json.loads(
        (root / "skills" / "aar-operations" / "metadata.json").read_text(encoding="utf-8")
    )


def _codex_mcp() -> dict[str, Any]:
    return {
        "mcpServers": {
            "aar": {
                "args": [],
                "command": "aar-mcp",
                "startup_timeout_sec": 30,
                "tool_timeout_sec": 60,
            }
        }
    }


def _hermes_mcp() -> dict[str, Any]:
    return {
        "aar": {
            "args": [],
            "command": "aar-mcp",
            "connect_timeout": 30,
            "timeout": 60,
        }
    }


def _hermes_config() -> bytes:
    return (
        b"mcp_servers:\n"
        b"  aar:\n"
        b"    command: aar-mcp\n"
        b"    args: []\n"
        b"    connect_timeout: 30\n"
        b"    timeout: 60\n"
    )


def host_documents(root: Path) -> dict[Path, bytes]:
    metadata = _canonical_metadata(root)
    codex_mcp = pretty_json_bytes(_codex_mcp())
    hermes_mcp = pretty_json_bytes(_hermes_mcp())
    hermes_config = _hermes_config()

    contract = {
        "contract_version": PROFILE_CONTRACT_VERSION,
        "package": {
            "command": "aar-mcp",
            "name": "adaptive-agent-runtime",
            "python_requires": ">=3.11,<3.15",
            "version": PACKAGE_VERSION,
        },
        "mcp": {
            "protocol_versions": list(MCP_PROTOCOL_VERSIONS),
            "transport": "stdio",
            "tool_surface_digest": metadata["tool_surface_digest"],
            "tool_surface_version": MCP_TOOL_SURFACE_VERSION,
        },
        "operation_skill": {
            "digest": metadata["skill_digest"],
            "name": "aar-operations",
            "version": OPERATION_SKILL_VERSION,
        },
        "profiles": {
            "codex": {
                "bundle": CODEX_ROOT.as_posix(),
                "config_digest": _sha256(codex_mcp),
                "config_file": ".mcp.json",
                "format": "codex-plugin",
            },
            "hermes": {
                "bundle": HERMES_ROOT.as_posix(),
                "config_digest": _sha256(hermes_config),
                "config_file": "config.yaml",
                "format": "hermes-profile-distribution",
            },
        },
    }
    codex_manifest = {
        "author": {"name": "phenomenoner"},
        "description": (
            "Prefer AAR MCP for materially useful tool-use and analysis workflows, then operate "
            "bounded workspaces, brokered RLM jobs, contracts, operations, and immutable assets."
        ),
        "interface": {
            "capabilities": ["Read", "Write"],
            "category": "Developer Tools",
            "defaultPrompt": [
                "Use $aar-operations to consider AAR MCP early for tool use or analysis, including "
                "software planning, development, testing, and troubleshooting, then run only a "
                "materially useful bounded workflow."
            ],
            "developerName": "phenomenoner",
            "displayName": "Adaptive Agent Runtime",
            "longDescription": (
                "Bundles the canonical AAR operation workflow with its local stdio MCP server. "
                "It treats software planning, development, testing, and troubleshooting as "
                "tool-use or analysis work where AAR should be considered early, while retaining "
                "host authority and a simpler direct path when AAR adds no material capability."
            ),
            "shortDescription": "AAR-assisted tool-use and analysis workflows",
        },
        "keywords": ["agents", "assets", "codegraph", "mcp", "rlm", "runtime"],
        "mcpServers": "./.mcp.json",
        "name": "adaptive-agent-runtime",
        "skills": "./skills/",
        "version": CODEX_PROFILE_VERSION,
    }
    marketplace = {
        "interface": {"displayName": "AAR Local"},
        "name": "aar-local",
        "plugins": [
            {
                "category": "Developer Tools",
                "name": "adaptive-agent-runtime",
                "policy": {"authentication": "ON_USE", "installation": "AVAILABLE"},
                "source": {
                    "path": "./plugins/adaptive-agent-runtime",
                    "source": "local",
                },
            }
        ],
    }
    hermes_distribution = (
        "name: adaptive-agent-runtime\n"
        f"version: {PROFILE_VERSION}\n"
        "description: Bounded AAR workspace, RLM, and immutable asset operations over local MCP.\n"
        "hermes_requires: '>=0.19.0'\n"
        "author: phenomenoner\n"
        "distribution_owned:\n"
        "  - config.yaml\n"
        "  - mcp.json\n"
        "  - skills\n"
        "  - distribution.yaml\n"
    ).encode()
    codex_readme = (
        b"# Codex host profile\n\n"
        b"Install the exact `adaptive-agent-runtime` wheel with `uv tool install"
        b" --force <wheel>` so\n"
        b"`aar-mcp` and its declared IPython, NumPy, and pandas dependencies are available, then"
        b" run\n"
        b"`aar-codex-setup`. The setup command uses the marketplace bundled in the installed wheel,"
        b" installs\n"
        b"this plugin as the sole AAR MCP transport authority, and runs a minimal real-worker\n"
        b"dependency preflight. A matching legacy global AAR server is removed; a conflicting"
        b" server\n"
        b"fails closed for operator review. Current plugin state is left untouched on a repeated"
        b" setup; only\n"
        b"restart the Codex App when the receipt reports `restart_required: true`. Then start a"
        b" fresh task and\n"
        b"invoke\n"
        b"`$aar-operations`. Use `$aar-ipython-codegraph` only for explicitly selected,\n"
        b"digest-verified source artifacts and an already available external CodeGraph.\n"
        b"The operation skill also covers bounded RLM jobs and immutable asset bundles; asset\n"
        b"import never activates or mutates host serving state.\n"
    )
    hermes_readme = (
        b"# Hermes host profile\n\n"
        b"Install the exact `adaptive-agent-runtime` wheel with `uv tool install"
        b" --force <wheel>` so\n"
        b"`aar-mcp` and its declared IPython, NumPy, and pandas dependencies are on the Hermes"
        b" host PATH,\n"
        b"then install this directory with `hermes profile install <directory> --name <profile>`."
        b" The runtime\n"
        b"reads `config.yaml.mcp_servers`; `mcp.json` is the equivalent reviewable server map.\n"
        b"Use a fresh isolated profile and invoke the bundled `aar-operations` skill.\n"
        b"The same surface supports bounded RLM jobs and immutable asset bundles without serving\n"
        b"activation authority.\n\n"
        b"Provider-backed RLM calls require a separately configured owner route catalog and\n"
        b"model broker. A Hermes client with MCP Sampling support can own the physical provider\n"
        b"call and return an `aar.model-receipt.v1` receipt. The bundled profile contains no\n"
        b"credentials, does not select a provider account, and does not authorize billable\n"
        b"inference by itself. See\n"
        b"`docs/MODEL-ROUTING-AND-EVALUATION.md` in the repository for route and evaluation\n"
        b"constraints.\n"
    )

    documents = {
        Path("profiles/host-profiles-v1.json"): pretty_json_bytes(contract),
        Path("profiles/codex/.agents/plugins/marketplace.json"): pretty_json_bytes(marketplace),
        CODEX_ROOT / ".codex-plugin/plugin.json": pretty_json_bytes(codex_manifest),
        CODEX_ROOT / ".mcp.json": codex_mcp,
        CODEX_ROOT / "README.md": codex_readme,
        HERMES_ROOT / "distribution.yaml": hermes_distribution,
        HERMES_ROOT / "config.yaml": hermes_config,
        HERMES_ROOT / "mcp.json": hermes_mcp,
        HERMES_ROOT / "README.md": hermes_readme,
    }
    canonical_skill = root / "skills" / "aar-operations"
    for relative in SKILL_FILES:
        content = (canonical_skill / relative).read_bytes()
        documents[CODEX_ROOT / "skills/aar-operations" / relative] = content
        documents[HERMES_ROOT / "skills/aar-operations" / relative] = content
    optional_skill = root / "skills" / "aar-ipython-codegraph"
    for relative in OPTIONAL_SKILL_FILES:
        content = (optional_skill / relative).read_bytes()
        documents[CODEX_ROOT / "skills/aar-ipython-codegraph" / relative] = content
        documents[HERMES_ROOT / "skills/aar-ipython-codegraph" / relative] = content
    return documents


def generate_profiles(root: Path) -> list[Path]:
    documents = host_documents(root)
    written: list[Path] = []
    for relative, content in documents.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        written.append(relative)
    return sorted(written)


def verify_profiles(root: Path) -> list[str]:
    errors: list[str] = []
    expected = host_documents(root)
    for relative, expected_bytes in expected.items():
        try:
            actual = (root / relative).read_bytes()
        except FileNotFoundError:
            errors.append(f"host profile asset unavailable: {relative.as_posix()}")
            continue
        if actual != expected_bytes:
            errors.append(f"host profile asset is stale: {relative.as_posix()}")
    expected_paths = {relative.as_posix() for relative in expected}
    for generated_root in (CODEX_ROOT, HERMES_ROOT):
        absolute_root = root / generated_root
        if not absolute_root.exists():
            continue
        for path in absolute_root.rglob("*"):
            if path.is_file():
                relative = path.relative_to(root).as_posix()
                if relative not in expected_paths:
                    errors.append(f"unexpected host profile asset: {relative}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "generate":
        for path in generate_profiles(root):
            print(path.as_posix())
        return 0
    errors = verify_profiles(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("AR-0D host profile assets verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
