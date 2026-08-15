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

from aar.compat.codex_mcp import (
    RUNTIME_HOME_ENV,
    default_runtime_home,
    ensure_codex_supervisor,
    stop_codex_supervisor,
)

SETUP_SCHEMA_VERSION = "aar.codex-setup.v3"
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


def _resolve_entrypoint(name: str, command: str | None = None) -> Path:
    if command is not None:
        candidate = Path(command).expanduser()
    else:
        executable_name = f"{name}.exe" if os.name == "nt" else name
        # Keep the active environment path until after selecting its sibling
        # entrypoint.  uv virtualenv Python launchers are commonly symlinks to
        # a shared interpreter; resolving first would search beside that shared
        # interpreter instead of beside this tool environment's console scripts.
        local = Path(sys.executable).with_name(executable_name)
        discovered = shutil.which(name)
        candidate = local if local.is_file() else Path(discovered or "")
    if not candidate.is_file():
        raise RuntimeError(
            f"{name} was not found in the active tool environment; install the exact AAR wheel "
            "first"
        )
    return candidate.resolve(strict=True)


def resolve_aar_mcp(command: str | None = None) -> Path:
    """Resolve the generic attached frontend used by matching legacy config checks."""
    return _resolve_entrypoint("aar-mcp", command)


def resolve_codex_mcp(command: str | None = None) -> Path:
    """Resolve the Codex-owned supervisor launcher from the active tool environment."""
    return _resolve_entrypoint("aar-codex-mcp", command)


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


def _plugin_mcp_spec(marketplace: Path) -> tuple[str, list[str]]:
    path = marketplace / "plugins" / "adaptive-agent-runtime" / ".mcp.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        server = document["mcpServers"]["aar"]
        command = server["command"]
        args = server.get("args", [])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError(f"the bundled AAR Codex MCP declaration is invalid: {path}") from error
    if not isinstance(command, str) or not command:
        raise RuntimeError("the bundled AAR Codex MCP command is invalid")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise RuntimeError("the bundled AAR Codex MCP args are invalid")
    return command, args


def _same_path(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return os.path.normcase(os.fspath(left.resolve(strict=False))) == os.path.normcase(
            os.fspath(right.resolve(strict=False))
        )


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
    plugin_mcp_launcher: Path | None = None,
    invoke: InvokeCodex | None = None,
    codex_config: Path | None = None,
) -> dict[str, Any]:
    """Install/update the plugin and keep it as the sole AAR MCP authority."""
    call = invoke or (lambda arguments: _invoke_codex(codex, arguments))
    config = (codex_config or _default_codex_config()).expanduser().resolve(strict=False)
    marketplace = marketplace.expanduser().resolve(strict=True)
    plugin_mcp_command, plugin_mcp_args = _plugin_mcp_spec(marketplace)
    preflight_launcher = plugin_mcp_launcher or aar_mcp
    legacy_mcp = _global_aar_entry(config)
    legacy_global_mcp_removed = legacy_mcp is not None
    if legacy_mcp is not None:
        command = legacy_mcp.get("command")
        args = legacy_mcp.get("args", [])
        unexpected_fields = set(legacy_mcp) - {"args", "command"}
        allowed_commands = {
            os.fspath(aar_mcp).casefold(),
            os.fspath(preflight_launcher).casefold(),
        }
        if (
            not isinstance(command, str)
            or command.casefold() not in allowed_commands
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
    marketplace_replaced = False
    previous_marketplace: Path | None = None
    if marketplace_added:
        call(("plugin", "marketplace", "add", os.fspath(marketplace), "--json"))
        active_marketplace = marketplace
    else:
        current_root = current.get("root")
        if not current_root:
            raise RuntimeError("the existing aar-local marketplace has no readable root")
        active_marketplace = Path(str(current_root)).expanduser().resolve(strict=False)
        if not _same_path(active_marketplace, marketplace):
            previous_marketplace = active_marketplace
            call(("plugin", "marketplace", "remove", MARKETPLACE_NAME))
            try:
                call(("plugin", "marketplace", "add", os.fspath(marketplace), "--json"))
            except Exception as install_error:
                try:
                    call(
                        (
                            "plugin",
                            "marketplace",
                            "add",
                            os.fspath(previous_marketplace),
                            "--json",
                        )
                    )
                except Exception as rollback_error:
                    raise RuntimeError(
                        "failed to install the bundled aar-local marketplace and failed to "
                        "restore its prior root"
                    ) from rollback_error
                raise RuntimeError(
                    "failed to install the bundled aar-local marketplace; its prior root was "
                    "restored"
                ) from install_error
            active_marketplace = marketplace
            marketplace_replaced = True

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
    plugin_changed = marketplace_added or marketplace_replaced or not (
        current_plugin is not None
        and current_plugin.get("version") == expected_version
        and current_plugin.get("enabled") is True
    )
    if plugin_changed:
        try:
            installed = json.loads(call(("plugin", "add", PLUGIN_SELECTOR, "--json")))
        except Exception as install_error:
            if marketplace_replaced:
                assert previous_marketplace is not None
                try:
                    call(("plugin", "marketplace", "remove", MARKETPLACE_NAME))
                    call(
                        (
                            "plugin",
                            "marketplace",
                            "add",
                            os.fspath(previous_marketplace),
                            "--json",
                        )
                    )
                    call(("plugin", "add", PLUGIN_SELECTOR, "--json"))
                except Exception as rollback_error:
                    raise RuntimeError(
                        "failed to install the bundled AAR plugin and failed to restore its prior "
                        "marketplace binding"
                    ) from rollback_error
                raise RuntimeError(
                    "failed to install the bundled AAR plugin; its prior marketplace binding was "
                    "restored"
                ) from install_error
            raise
    else:
        installed = current_plugin
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
            marketplace_added
            or marketplace_replaced
            or plugin_changed
            or legacy_global_mcp_removed
        ),
        "legacy_global_mcp_removed": legacy_global_mcp_removed,
        "codex_config": os.fspath(config),
        "marketplace_added": marketplace_added,
        "marketplace_replaced": marketplace_replaced,
        "marketplace_path": os.fspath(active_marketplace),
        "mcp_authority": "plugin",
        "plugin_mcp_args": plugin_mcp_args,
        "plugin_mcp_command": plugin_mcp_command,
        "plugin_id": installed.get("pluginId", PLUGIN_SELECTOR),
        "plugin_changed": plugin_changed,
        "plugin_version": installed["version"],
        "preflight_launcher": os.fspath(preflight_launcher),
    }


async def _preflight(
    codex_mcp: Path,
    runtime_home: Path,
    scenario_id: str,
    declared_args: Sequence[str],
) -> dict[str, Any]:
    environment = dict(os.environ)
    environment[RUNTIME_HOME_ENV] = os.fspath(runtime_home)
    parameters = StdioServerParameters(
        command=os.fspath(codex_mcp),
        args=list(declared_args),
        env=environment,
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
            "declared_args": list(declared_args),
            "declared_command": os.fspath(codex_mcp),
            "server_name": capabilities["server_name"],
            "package_version": capabilities["package_version"],
            "tool_surface_version": capabilities["tool_surface_version"],
            "tool_count": len(tool_names),
            "supervisor": {
                key: capabilities["supervisor"][key]
                for key in (
                    "frontend_ephemeral",
                    "mode",
                    "process_identity_digest",
                    "protocol_digest",
                    "protocol_version",
                )
            },
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
    codex_mcp: Path,
    *,
    runtime_home: Path | None = None,
    declared_args: Sequence[str] = (),
) -> dict[str, Any]:
    """Run the declared Codex launcher in an isolated attached-supervisor lifecycle."""
    scenario_id = f"codex-setup-{uuid.uuid4().hex}"
    if runtime_home is None:
        with tempfile.TemporaryDirectory(prefix="aar-codex-setup-") as directory:
            return await run_setup_preflight(
                codex_mcp,
                runtime_home=Path(directory) / "runtime",
                declared_args=declared_args,
            )
    runtime_home = runtime_home.expanduser().resolve(strict=False)
    if (runtime_home / "reference.sqlite3").exists() or (
        runtime_home / "supervisor" / "discovery.json"
    ).exists():
        raise RuntimeError("Codex setup preflight requires an unused isolated runtime home")
    report: dict[str, Any] | None = None
    try:
        report = await _preflight(codex_mcp, runtime_home, scenario_id, declared_args)
    finally:
        stopped = stop_codex_supervisor(runtime_home)
    if report is None:
        raise RuntimeError("Codex setup preflight produced no report")
    if not stopped:
        raise RuntimeError("Codex setup preflight did not own a supervisor to stop")
    report["supervisor_stopped"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Install the bundled AAR Codex plugin as the sole MCP authority and run a minimal "
            "dependency preflight."
        )
    )
    parser.add_argument("--aar-mcp", help="Exact generic aar-mcp used for legacy config matching")
    parser.add_argument(
        "--aar-codex-mcp",
        help="Exact aar-codex-mcp launcher; defaults to this tool environment",
    )
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
    parser.add_argument(
        "--stop-runtime",
        action="store_true",
        help="Gracefully stop the exact discovered Codex-owned supervisor before an upgrade",
    )
    parser.add_argument(
        "--codex-runtime-home",
        type=Path,
        help=(
            "Codex runtime home for --stop-runtime; defaults to "
            f"{RUNTIME_HOME_ENV} or ~/.aar/codex"
        ),
    )
    args = parser.parse_args(argv)
    if args.preflight_only and args.skip_preflight:
        parser.error("--preflight-only and --skip-preflight cannot be combined")
    if args.stop_runtime and (args.preflight_only or args.skip_preflight):
        parser.error("--stop-runtime cannot be combined with setup or preflight modes")
    if args.codex_runtime_home is not None and not args.stop_runtime:
        parser.error("--codex-runtime-home requires --stop-runtime")

    try:
        if args.stop_runtime:
            runtime_home = (
                args.codex_runtime_home.expanduser().resolve(strict=False)
                if args.codex_runtime_home is not None
                else default_runtime_home()
            )
            stopped = stop_codex_supervisor(runtime_home)
            print(
                json.dumps(
                    {
                        "action": "stop-runtime",
                        "runtime_home": os.fspath(runtime_home),
                        "schema_version": SETUP_SCHEMA_VERSION,
                        "status": "passed",
                        "stopped": stopped,
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        aar_mcp = resolve_aar_mcp(args.aar_mcp)
        codex_mcp = resolve_codex_mcp(args.aar_codex_mcp)
        marketplace = bundled_marketplace()
        plugin_mcp_command, plugin_mcp_args = _plugin_mcp_spec(marketplace)
        if plugin_mcp_command != "aar-codex-mcp":
            raise RuntimeError(
                "the bundled Codex plugin must use the aar-codex-mcp host launcher"
            )
        preflight = (
            None
            if args.skip_preflight
            else asyncio.run(
                run_setup_preflight(codex_mcp, declared_args=plugin_mcp_args)
            )
        )
        configured = None
        codex_runtime = None
        if not args.preflight_only:
            configured = configure_codex(
                resolve_codex(args.codex),
                aar_mcp,
                marketplace,
                plugin_mcp_launcher=codex_mcp,
                codex_config=(
                    Path(args.codex_config).expanduser() if args.codex_config else None
                ),
            )
            runtime_home = default_runtime_home()
            discovery = ensure_codex_supervisor(runtime_home)
            codex_runtime = {
                "discovery_digest": discovery.discovery_digest,
                "dispatcher_generation": discovery.dispatcher_generation,
                "runtime_generation": discovery.runtime_generation,
                "runtime_home": os.fspath(runtime_home),
                "status": "ready",
                "supervisor_version": discovery.supervisor_version,
            }
        report = {
            "schema_version": SETUP_SCHEMA_VERSION,
            "status": "passed",
            "aar_mcp": os.fspath(aar_mcp),
            "codex_mcp": os.fspath(codex_mcp),
            "preflight": preflight,
            "codex": configured,
            "codex_runtime": codex_runtime,
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
