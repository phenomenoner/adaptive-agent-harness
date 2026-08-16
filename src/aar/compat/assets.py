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
CODEX_PROFILE_VERSION = "0.4.0+codex.20260816122000"
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
                "command": "aar-codex-mcp",
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
                "launcher": "aar-codex-mcp",
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
                "Use $aar-operations for materially useful tool use or analysis; otherwise keep "
                "the direct host-native path."
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
    codex_readme = f"""# Codex host profile

Release package: `{PACKAGE_VERSION}`; bundled operation skill: `{OPERATION_SKILL_VERSION}`. The
source repository's immutable release snapshot is `profiles/release-status-v1.json`; exact
post-freeze evidence is bound by
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`. This profile does not establish official
Plugin Directory publication, which requires separate external authority.
Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so
`aar-codex-mcp` and its declared IPython, NumPy, and pandas dependencies are available, then run
`aar-codex-setup`. The setup command uses the marketplace bundled in the installed wheel and
preflights the declared plugin command and attached-supervisor mode.

The subprocess Codex route reports `NO_ATOMIC_AUTHORITY`: it reads current state and returns an
ordered manual/provider-authority-required plan before the first forward mutation. The host/operator
must apply that plan through the authoritative Codex configuration owner and rerun setup. There is
no automatic install, rollback, or best-effort compensation. A provider adapter may enable those
operations only after it supplies an opaque revision and expected-revision CAS contract.

Supervisor endpoint, credential, and shutdown-request paths are generation-unique. `discovery.json`
is a stable pointer that carries the publication ID and is advanced atomically. Normal lifecycle
cleanup is deliberately non-destructive and retains generation-specific control artifacts; do not
delete a successor by pathname or visible bytes. If native process identity is temporarily
unavailable, do not delete control files or start a second owner.

When setup reports `restart_required: true`, or a preserved manual receipt reports
`restart_required_after_manual_apply: true`, restart Codex Desktop, start a fresh task, load the
deferred capability tool when needed, and make one native `aar_capabilities` call. A later no-op
inspection with `restart_required: false` does not erase the preserved handoff. The old task's
catalog or a same-task transport error cannot prove that the release was picked up. Invoke
`$aar-operations` for public MCP workflows. Use `$aar-ipython-codegraph` only for explicitly
selected, digest-verified source artifacts and an already available external CodeGraph.
""".encode()
    hermes_readme = f"""# Hermes host profile

Release package: `{PACKAGE_VERSION}`; bundled operation skill: `{OPERATION_SKILL_VERSION}`. The
source repository's immutable release snapshot is `profiles/release-status-v1.json`; exact
post-freeze evidence is bound by
`adaptive-agent-runtime-v0.4.0a6-release-receipt.json`. This profile does not establish official
Plugin Directory publication, which requires separate external authority.
Install the exact `adaptive-agent-runtime` wheel with `uv tool install --force <wheel>` so `aar-mcp`
and its declared IPython, NumPy, and pandas dependencies are on the Hermes host PATH, then install
this directory with `hermes profile install <directory> --name <profile>`. The runtime reads
`config.yaml.mcp_servers`; `mcp.json` is the equivalent reviewable server map.

The profile provides ordinary MCP operations and host-owned RLM model calls. It contains no provider
credentials and does not authorize billable inference. A Hermes client may own the physical call and
return an `aar.model-receipt.v1` receipt.

Supervisor endpoint, credential, and shutdown-request paths are generation-unique; the stable
discovery pointer is advanced atomically. Normal lifecycle cleanup is deliberately non-destructive,
so generation-specific control artifacts remain forensic state. The subprocess Codex setup route is
not Hermes authority and reports `NO_ATOMIC_AUTHORITY`
when no provider revision/CAS contract exists; follow the ordered manual plan through the host's
configuration owner. After an upgrade, restart Hermes if required and create a fresh task/session
before relying on capability discovery. See `$aar-operations` for the full operation workflow.
""".encode()

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
