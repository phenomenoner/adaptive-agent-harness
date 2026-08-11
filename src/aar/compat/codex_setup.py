"""Install the bundled Codex plugin as the sole AAR MCP transport authority."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from mcp import Client, StdioServerParameters
from mcp.client.stdio import stdio_client

SETUP_SCHEMA_VERSION = "aar.codex-setup.v2"
MARKETPLACE_NAME = "aar-local"
PLUGIN_SELECTOR = "adaptive-agent-runtime@aar-local"
InvokeCodex = Callable[[Sequence[str]], str]


def _structured(result: Any) -> dict[str, Any]:
    if result.is_error or result.structured_content is None:
        raise RuntimeError("AAR MCP preflight did not return structured content")
    return result.structured_content


def _mutation(
    base: dict[str, Any], grants: dict[str, str], capability: str, request_id: str
) -> dict[str, Any]:
    return {
        **base,
        "request_id": request_id,
        "idempotency_key": f"idempotency-{request_id}",
        "grant_id": grants[capability],
        "budget_wall_time_ms": 60_000,
    }


def resolve_aar_mcp(command: str | None = None) -> Path:
    """Resolve the exact entrypoint from the active tool environment."""
    if command is not None:
        candidate = Path(command).expanduser()
    else:
        name = "aar-mcp.exe" if os.name == "nt" else "aar-mcp"
        # Keep the active environment path until after selecting its sibling
        # entrypoint.  uv virtualenv Python launchers are commonly symlinks to
        # a shared interpreter; resolving first would search beside that shared
        # interpreter instead of beside this tool environment's console scripts.
        local = Path(sys.executable).with_name(name)
        discovered = shutil.which("aar-mcp")
        candidate = local if local.is_file() else Path(discovered or "")
    if not candidate.is_file():
        raise RuntimeError(
            "aar-mcp was not found in the active tool environment; install the exact AAR wheel "
            "first"
        )
    return candidate.resolve(strict=True)


def resolve_codex(command: str | None = None) -> Path:
    """Resolve a concrete Codex CLI command before changing user configuration."""
    discovered = command or shutil.which("codex")
    if not discovered:
        raise RuntimeError("Codex CLI was not found; install or update Codex before running setup")
    candidate = Path(discovered).expanduser()
    if not candidate.is_file():
        raise RuntimeError(f"Codex CLI does not exist: {candidate}")
    return candidate.resolve(strict=True)


def bundled_marketplace() -> Path:
    """Locate the generated marketplace in a wheel or a source checkout."""
    package_root = Path(__file__).resolve().parents[1]
    installed = package_root / "bundled" / "profiles" / "codex"
    source = Path(__file__).resolve().parents[3] / "profiles" / "codex"
    for candidate in (installed, source):
        if (candidate / ".agents" / "plugins" / "marketplace.json").is_file():
            return candidate.resolve(strict=True)
    raise RuntimeError("the AAR Codex marketplace is missing from this installation")


def _plugin_version(marketplace: Path) -> str:
    manifest = (
        marketplace
        / "plugins"
        / "adaptive-agent-runtime"
        / ".codex-plugin"
        / "plugin.json"
    )
    return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])


def _subprocess_argv(codex: Path, arguments: Sequence[str]) -> list[str]:
    if os.name == "nt" and codex.suffix.lower() in {".bat", ".cmd"}:
        comspec = Path(os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe"))
        return [os.fspath(comspec), "/d", "/c", os.fspath(codex), *arguments]
    return [os.fspath(codex), *arguments]


def _invoke_codex(codex: Path, arguments: Sequence[str]) -> str:
    completed = subprocess.run(
        _subprocess_argv(codex, arguments),
        check=False,
        capture_output=True,
        encoding="utf-8",
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        raise RuntimeError(
            f"Codex command failed ({completed.returncode}): {' '.join(arguments)}: "
            f"{detail[-2000:]}"
        )
    return completed.stdout


def _default_codex_config() -> Path:
    configured = os.environ.get("CODEX_HOME")
    root = Path(configured).expanduser() if configured else Path.home() / ".codex"
    return root / "config.toml"


def _global_aar_entry(config: Path) -> dict[str, Any] | None:
    if not config.is_file():
        return None
    try:
        with config.open("rb") as stream:
            document = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"Codex config could not be read safely: {config}: {exc}") from exc
    servers = document.get("mcp_servers", {})
    if not isinstance(servers, dict):
        raise RuntimeError(f"Codex config has an invalid mcp_servers table: {config}")
    entry = servers.get("aar")
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise RuntimeError(f"Codex config has an invalid global AAR MCP entry: {config}")
    return entry


def configure_codex(
    codex: Path,
    aar_mcp: Path,
    marketplace: Path,
    *,
    invoke: InvokeCodex | None = None,
    codex_config: Path | None = None,
) -> dict[str, Any]:
    """Install/update the plugin and keep it as the sole AAR MCP authority."""
    call = invoke or (lambda arguments: _invoke_codex(codex, arguments))
    config = (codex_config or _default_codex_config()).expanduser().resolve(strict=False)
    legacy_mcp = _global_aar_entry(config)
    legacy_global_mcp_removed = legacy_mcp is not None
    if legacy_mcp is not None:
        command = legacy_mcp.get("command")
        args = legacy_mcp.get("args", [])
        unexpected_fields = set(legacy_mcp) - {"args", "command"}
        if (
            not isinstance(command, str)
            or command.casefold() != os.fspath(aar_mcp).casefold()
            or args not in (None, [])
            or unexpected_fields
        ):
            raise RuntimeError(
                "a conflicting global MCP server named 'aar' is configured; remove or rename it "
                "explicitly before installing the AAR plugin"
            )
    listed = json.loads(call(("plugin", "marketplace", "list", "--json")))
    marketplaces = listed.get("marketplaces", [])
    current = next(
        (item for item in marketplaces if item.get("name") == MARKETPLACE_NAME), None
    )
    marketplace_added = current is None
    if marketplace_added:
        call(("plugin", "marketplace", "add", os.fspath(marketplace), "--json"))
        active_marketplace = marketplace
    else:
        current_root = current.get("root")
        if not current_root:
            raise RuntimeError("the existing aar-local marketplace has no readable root")
        active_marketplace = Path(str(current_root))

    expected_version = _plugin_version(marketplace)
    plugin_list = json.loads(call(("plugin", "list", "--json")))
    current_plugin = next(
        (
            item
            for item in plugin_list.get("installed", [])
            if item.get("pluginId") == PLUGIN_SELECTOR
        ),
        None,
    )
    plugin_changed = not (
        current_plugin is not None
        and current_plugin.get("version") == expected_version
        and current_plugin.get("enabled") is True
    )
    installed = (
        json.loads(call(("plugin", "add", PLUGIN_SELECTOR, "--json")))
        if plugin_changed
        else current_plugin
    )
    if installed is None or installed.get("version") != expected_version:
        raise RuntimeError(
            "the configured aar-local marketplace did not install the bundled plugin version; "
            "inspect or replace the stale marketplace before retrying"
        )

    if legacy_global_mcp_removed:
        call(("mcp", "remove", "aar"))
        if _global_aar_entry(config) is not None:
            raise RuntimeError("the legacy global AAR MCP server remained after removal")
    return {
        "configuration_changed": (
            marketplace_added or plugin_changed or legacy_global_mcp_removed
        ),
        "legacy_global_mcp_removed": legacy_global_mcp_removed,
        "codex_config": os.fspath(config),
        "marketplace_added": marketplace_added,
        "marketplace_path": os.fspath(active_marketplace),
        "mcp_authority": "plugin",
        "plugin_mcp_command": "aar-mcp",
        "plugin_id": installed.get("pluginId", PLUGIN_SELECTOR),
        "plugin_changed": plugin_changed,
        "plugin_version": installed["version"],
        "preflight_launcher": os.fspath(aar_mcp),
    }


async def _preflight(aar_mcp: Path, database: Path, scenario_id: str) -> dict[str, Any]:
    parameters = StdioServerParameters(
        command=os.fspath(aar_mcp),
        args=["--database", os.fspath(database)],
    )
    async with Client(stdio_client(parameters), mode="auto") as client:
        capabilities = _structured(await client.call_tool("aar_capabilities"))
        tools = await client.list_tools(cache_mode="reload")
        tool_names = [tool.name for tool in tools.tools]
        if tool_names != capabilities["tool_names"]:
            raise RuntimeError("AAR tool discovery does not match aar_capabilities")

        base = {
            "runtime_generation": capabilities["ready"]["runtime_generation"],
            "capability_digest": capabilities["ready"]["capabilities"]["digest"],
            "principal_id": f"principal-{scenario_id}",
            "session_id": f"session-{scenario_id}",
            "deadline_unix_ms": int(time.time() * 1000) + 60_000,
        }
        grants = {
            item["capability"]: item["grant_id"]
            for item in capabilities["reference_grants"]
        }
        workspace_id = f"workspace-{scenario_id}"
        created = _structured(
            await client.call_tool(
                "aar_program_workspace_create",
                {
                    "context": _mutation(
                        base,
                        grants,
                        "workspace.program.create",
                        f"create-{scenario_id}",
                    ),
                    "workspace_id": workspace_id,
                },
            )
        )
        if created["failure"] is not None:
            raise RuntimeError(
                "AAR setup preflight could not create a programmable workspace: "
                f"{created['failure']!r}"
            )

        executed = _structured(
            await client.call_tool(
                "aar_program_workspace_execute",
                {
                    "context": _mutation(
                        base,
                        grants,
                        "workspace.program.execute",
                        f"execute-{scenario_id}",
                    ),
                    "handle": created["handle"],
                    "code": (
                        "import IPython\n"
                        "import numpy as np\n"
                        "import pandas as pd\n"
                        "frame = pd.DataFrame({'qty': [3, 7, 8]})\n"
                        "answer = int(np.asarray(frame['qty']).sum())\n"
                        "{'answer': answer, 'rows': int(len(frame)), "
                        "'ipython': IPython.__version__, 'numpy': np.__version__, "
                        "'pandas': pd.__version__}"
                    ),
                    "wall_time_ms": 30_000,
                },
            )
        )
        if executed["failure"] is not None:
            raise RuntimeError(
                "AAR setup preflight could not import its analysis dependencies: "
                f"{executed['failure']!r}"
            )
        dependency_result = executed["result"]["result"]
        if dependency_result["answer"] != 18 or dependency_result["rows"] != 3:
            raise RuntimeError("AAR setup dependency calculation returned an unexpected result")

        executed_handle = {
            "workspace": executed["result"]["workspace"],
            "backend": executed["result"]["backend"],
            "generation": executed["result"]["generation"],
            "revision": executed["result"]["revision_after"],
        }
        inspected = _structured(
            await client.call_tool(
                "aar_program_workspace_inspect",
                {"context": base, "handle": executed_handle},
            )
        )
        variable_names = [item["name"] for item in inspected["snapshot"]["variables"]]
        if not {"IPython", "answer", "frame", "np", "pd"}.issubset(variable_names):
            raise RuntimeError("AAR setup inspection omitted dependency-backed workspace values")

        closed = _structured(
            await client.call_tool(
                "aar_program_workspace_close",
                {
                    "context": _mutation(
                        base,
                        grants,
                        "workspace.program.close",
                        f"close-{scenario_id}",
                    ),
                    "handle": executed_handle,
                    "reason": "Codex setup preflight complete",
                },
            )
        )
        if not closed["result"]["closed"]:
            raise RuntimeError("AAR setup preflight workspace did not close")
        return {
            "server_name": capabilities["server_name"],
            "package_version": capabilities["package_version"],
            "tool_surface_version": capabilities["tool_surface_version"],
            "tool_count": len(tool_names),
            "dependencies": {
                key: dependency_result[key] for key in ("ipython", "numpy", "pandas")
            },
            "calculation": {
                "answer": dependency_result["answer"],
                "rows": dependency_result["rows"],
            },
            "workspace_closed": True,
        }


async def run_setup_preflight(
    aar_mcp: Path, *, database: Path | None = None
) -> dict[str, Any]:
    """Run the minimal real-MCP dependency lifecycle used by the installer."""
    scenario_id = f"codex-setup-{uuid.uuid4().hex}"
    if database is not None:
        return await _preflight(aar_mcp, database, scenario_id)
    with tempfile.TemporaryDirectory(prefix="aar-codex-setup-") as directory:
        return await _preflight(aar_mcp, Path(directory) / "runtime.sqlite3", scenario_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install the bundled AAR Codex plugin as the sole MCP authority and run a minimal "
            "dependency preflight."
        )
    )
    parser.add_argument("--aar-mcp", help="Exact aar-mcp executable; defaults to this tool env")
    parser.add_argument("--codex", help="Exact Codex CLI command; defaults to PATH discovery")
    parser.add_argument(
        "--codex-config",
        help="Codex config.toml to inspect for a legacy global AAR server",
    )
    parser.add_argument(
        "--preflight-only", action="store_true", help="Verify AAR without changing Codex config"
    )
    parser.add_argument(
        "--skip-preflight", action="store_true", help="Configure Codex without the runtime check"
    )
    args = parser.parse_args(argv)
    if args.preflight_only and args.skip_preflight:
        parser.error("--preflight-only and --skip-preflight cannot be combined")

    try:
        aar_mcp = resolve_aar_mcp(args.aar_mcp)
        preflight = (
            None if args.skip_preflight else asyncio.run(run_setup_preflight(aar_mcp))
        )
        configured = None
        if not args.preflight_only:
            configured = configure_codex(
                resolve_codex(args.codex),
                aar_mcp,
                bundled_marketplace(),
                codex_config=(
                    Path(args.codex_config).expanduser() if args.codex_config else None
                ),
            )
        report = {
            "schema_version": SETUP_SCHEMA_VERSION,
            "status": "passed",
            "aar_mcp": os.fspath(aar_mcp),
            "preflight": preflight,
            "codex": configured,
            "restart_required": bool(
                configured is not None and configured["configuration_changed"]
            ),
        }
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # pragma: no cover - exercised through CLI process tests
        print(
            json.dumps(
                {
                    "schema_version": SETUP_SCHEMA_VERSION,
                    "status": "failed",
                    "error": str(exc),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
