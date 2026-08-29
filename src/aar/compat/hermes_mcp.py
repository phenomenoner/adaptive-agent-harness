"""Standalone Hermes MCP adapter for a clean-installed provider-ready AAR runtime."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import anyio

from aar.canonical import canonical_sha256
from aar.compat.codex_mcp import DEFAULT_STARTUP_TIMEOUT_SEC, ensure_codex_supervisor
from aar.provider_ready_package_factory import PACKAGE_FACTORY_DECLARATIONS
from aar.runtime.hermes_host import load_hermes_route_catalog
from aar.runtime.process_identity import SupervisorDiscoveryRecord
from aar.runtime.provider_ready_activation import ProviderReadyActivationStore
from aar.runtime.python_child import exact_module_command
from aar.runtime.supervisor_client import SupervisorClient
from aar.runtime.supervisor_protocol import (
    SUPERVISOR_PROTOCOL_DIGEST,
    SUPERVISOR_PROTOCOL_VERSION,
)
from aar.versions import PACKAGE_VERSION


class HermesMcpLauncherError(RuntimeError):
    pass


def _default_runtime_home() -> Path:
    configured = os.environ.get("AAR_RUNTIME_HOME")
    return Path(configured) if configured else Path.home() / ".aar"


def _capabilities_probe(
    runtime_home: Path,
    discovery: SupervisorDiscoveryRecord,
) -> tuple[dict[str, Any], dict[str, Any]]:
    read_context = {
        "runtime_generation": discovery.runtime_generation,
        "capability_digest": discovery.capability_digest,
        "principal_id": "aar-hermes-ready-probe",
        "session_id": "aar-hermes-ready-probe",
        "deadline_unix_ms": time.time_ns() // 1_000_000 + 30_000,
    }
    messages = (
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "aar-hermes-ready-probe", "version": PACKAGE_VERSION},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "aar_capabilities", "arguments": {}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "aar_rlm_workbench_capabilities",
                "arguments": {"context": read_context},
            },
        },
    )
    completed = subprocess.run(
        exact_module_command(
            "aar.runtime.supervisor_client",
            ["--runtime-home", os.fspath(runtime_home)],
            isolated=False,
        ),
        input="".join(json.dumps(item, separators=(",", ":")) + "\n" for item in messages),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if completed.returncode != 0:
        raise HermesMcpLauncherError("Hermes provider-ready capability probe failed")
    try:
        rows = tuple(json.loads(line) for line in completed.stdout.splitlines())
        responses = {
            response_id: next(row for row in rows if row.get("id") == response_id)
            for response_id in (2, 3)
        }
        structured: dict[int, dict[str, Any]] = {}
        for response_id, response in responses.items():
            result = response["result"]
            if result.get("isError") is not False:
                raise ValueError("capability tool returned an error")
            value = result["structuredContent"]
            if not isinstance(value, dict):
                raise ValueError("structured capabilities are not an object")
            structured[response_id] = value
        return structured[2], structured[3]
    except (KeyError, StopIteration, TypeError, ValueError, json.JSONDecodeError) as error:
        raise HermesMcpLauncherError(
            "Hermes provider-ready capability probe returned invalid evidence"
        ) from error


def verify_hermes_ready(
    runtime_home: Path,
    discovery: SupervisorDiscoveryRecord,
    *,
    route_catalog_digest: str,
    default_route_profile: str,
) -> dict[str, Any]:
    """Read back both the live MCP owner and current persisted activation binding."""

    grant_set = ProviderReadyActivationStore(runtime_home / "authority").read(
        discovery.runtime_generation
    )
    if grant_set is None:
        raise HermesMcpLauncherError(
            "current supervisor generation has no provider-ready grant set"
        )
    capabilities, workbench = _capabilities_probe(runtime_home, discovery)
    ready = capabilities.get("ready")
    supervisor = capabilities.get("supervisor")
    model_broker = capabilities.get("model_broker")
    model_routes = capabilities.get("model_routes")
    methods = workbench.get("methods")
    expected_methods = tuple(item.method for item in PACKAGE_FACTORY_DECLARATIONS)
    method_rows_ready = (
        isinstance(methods, list)
        and tuple(row.get("method") for row in methods if isinstance(row, dict))
        == expected_methods
        and all(
            isinstance(row, dict)
            and row.get("configured") is True
            and row.get("reference_only") is False
            and row.get("backend_kind") in {"caller_driver", "native"}
            and row.get("adapter_generation") == discovery.runtime_generation
            for row in methods
        )
    )
    if (
        capabilities.get("package_version") != PACKAGE_VERSION
        or not isinstance(ready, dict)
        or ready.get("runtime_generation") != discovery.runtime_generation
        or ready.get("capabilities", {}).get("digest") != discovery.capability_digest
        or not isinstance(supervisor, dict)
        or supervisor.get("mode") != "attached-supervisor"
        or supervisor.get("frontend_ephemeral") is not True
        or supervisor.get("dispatcher_generation") != discovery.dispatcher_generation
        or supervisor.get("supervisor_version") != discovery.supervisor_version
        or supervisor.get("protocol_version") != SUPERVISOR_PROTOCOL_VERSION
        or supervisor.get("protocol_digest") != SUPERVISOR_PROTOCOL_DIGEST
        or supervisor.get("process_identity_digest")
        != canonical_sha256(discovery.process_identity)
        or not isinstance(model_broker, dict)
        or model_broker.get("configured") is not True
        or model_broker.get("default_profile_id") != default_route_profile
        or model_broker.get("catalog_digest") != route_catalog_digest
        or not isinstance(model_routes, dict)
        or model_routes.get("catalog_digest") != route_catalog_digest
        or grant_set.runtime_generation != discovery.runtime_generation
        or not method_rows_ready
        or grant_set.capability_digest != canonical_sha256(workbench)
        or grant_set.route_catalog_digest != route_catalog_digest
    ):
        raise HermesMcpLauncherError(
            "live supervisor does not match the exact Hermes provider-ready startup binding"
        )
    return capabilities


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path, default=_default_runtime_home())
    parser.add_argument("--route-catalog", type=Path, required=True)
    parser.add_argument("--default-route-profile", required=True)
    parser.add_argument(
        "--programmable-backend", choices=("plain", "ipython"), default="ipython"
    )
    parser.add_argument(
        "--startup-timeout-sec", type=float, default=DEFAULT_STARTUP_TIMEOUT_SEC
    )
    args = parser.parse_args(argv)
    try:
        runtime_home = args.runtime_home.expanduser().resolve(strict=False)
        route_catalog_input = args.route_catalog.expanduser()
        catalog = load_hermes_route_catalog(route_catalog_input)
        route_catalog_path = route_catalog_input.resolve(strict=True)
        discovery = ensure_codex_supervisor(
            runtime_home,
            programmable_backend=args.programmable_backend,
            startup_timeout_sec=args.startup_timeout_sec,
            supervisor_args=(
                "--provider-ready-route-catalog",
                os.fspath(route_catalog_path),
                "--default-route-profile",
                args.default_route_profile,
            ),
        )
        verify_hermes_ready(
            runtime_home,
            discovery,
            route_catalog_digest=catalog.catalog_digest,
            default_route_profile=args.default_route_profile,
        )
        anyio.run(SupervisorClient(runtime_home).bridge_stdio)
    except Exception as error:
        print(
            f"aar-hermes-mcp failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
