"""Run the reusable AAR black-box compatibility scenario over MCP stdio."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
import uuid
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

from aar.mcp.server import SERVER_NAME

SMOKE_SCHEMA_VERSION = "aar.compat-smoke.v3"


def _resolve_scenario_id(value: str | None) -> str:
    """Keep explicit replay IDs while making ordinary smoke runs rerunnable."""

    return value or f"black-box-{uuid.uuid4().hex}"


def _structured(result: Any) -> dict[str, Any]:
    if result.is_error or result.structured_content is None:
        raise RuntimeError("MCP tool call did not return structured AAR content")
    return result.structured_content


def _contexts(
    capabilities: dict[str, Any], scenario_id: str
) -> tuple[dict[str, Any], dict[str, str]]:
    deadline = int(time.time() * 1000) + 60_000
    base = {
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "principal_id": f"principal-{scenario_id}",
        "session_id": f"session-{scenario_id}",
        "deadline_unix_ms": deadline,
    }
    grants = {
        descriptor["capability"]: descriptor["grant_id"]
        for descriptor in capabilities["reference_grants"]
    }
    return base, grants


def _mutation(
    read_context: dict[str, Any],
    grants: dict[str, str],
    capability: str,
    request_id: str,
) -> dict[str, Any]:
    return {
        **read_context,
        "request_id": request_id,
        "idempotency_key": f"idempotency-{request_id}",
        "grant_id": grants[capability],
        "budget_wall_time_ms": 60_000,
    }


async def run_scenario(client: Client, scenario_id: str) -> dict[str, Any]:
    capabilities = _structured(await client.call_tool("aar_capabilities"))
    tools = await client.list_tools(cache_mode="reload")
    observed_tools = [tool.name for tool in tools.tools]
    if capabilities["server_name"] != SERVER_NAME:
        raise RuntimeError("unexpected AAR server identity")
    if observed_tools != capabilities["tool_names"]:
        raise RuntimeError("discovered tool order does not match aar_capabilities")
    client_protocol_version = str(client.protocol_version)
    server_protocol_version = capabilities["negotiated_protocol_version"]
    if server_protocol_version != client_protocol_version:
        raise RuntimeError("client and server disagree on the negotiated MCP protocol version")

    read_context, grants = _contexts(capabilities, scenario_id)
    workspace_id = f"workspace-{scenario_id}"
    created = _structured(
        await client.call_tool(
            "aar_workspace_create",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.create",
                    f"create-{scenario_id}",
                ),
                "workspace_id": workspace_id,
            },
        )
    )
    if created["failure"] is not None or created["handle"]["revision"] != 0:
        raise RuntimeError("workspace create did not return the expected fresh handle")

    executed = _structured(
        await client.call_tool(
            "aar_workspace_execute",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.execute",
                    f"execute-{scenario_id}",
                ),
                "workspace_id": workspace_id,
                "expected_generation": created["handle"]["generation"],
                "expected_revision": 0,
                "action": "set",
                "key": "answer",
                "value": 42,
            },
        )
    )
    if executed["state"] != "succeeded" or len(executed["artifacts"]) != 1:
        raise RuntimeError("workspace execution did not produce one receipt artifact")
    operation_id = executed["operation"]["value"]
    reference = executed["artifacts"][0]

    resolved = _structured(
        await client.call_tool(
            "aar_artifact_resolve",
            {
                "context": read_context,
                "artifact_id": reference["artifact"]["value"],
                "digest": reference["digest"],
                "media_type": reference["media_type"],
                "size_bytes": reference["size_bytes"],
                "created_by_operation_id": operation_id,
                "redacted": reference["redacted"],
                "max_bytes": reference["size_bytes"],
            },
        )
    )
    receipt = json.loads(base64.b64decode(resolved["content_base64"], validate=True))
    if receipt["operation"]["value"] != operation_id or receipt["state"] != "succeeded":
        raise RuntimeError("resolved receipt does not bind the successful operation")

    inspected = _structured(
        await client.call_tool(
            "aar_workspace_inspect",
            {
                "context": read_context,
                "workspace_id": workspace_id,
                "expected_generation": created["handle"]["generation"],
                "expected_revision": 1,
            },
        )
    )
    if inspected["snapshot"]["values"] != [["answer", 42]]:
        raise RuntimeError("workspace inspection did not read back the mutation")

    status = _structured(
        await client.call_tool(
            "aar_operation_status",
            {"context": read_context, "operation_id": operation_id},
        )
    )
    if status["state"] != "succeeded":
        raise RuntimeError("operation status did not preserve the terminal state")

    accepted = _structured(
        await client.call_tool(
            "aar_workspace_execute",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.execute",
                    f"start-only-{scenario_id}",
                ),
                "workspace_id": workspace_id,
                "expected_generation": created["handle"]["generation"],
                "expected_revision": 1,
                "action": "set",
                "key": "cancelled",
                "value": True,
                "start_only": True,
            },
        )
    )
    cancel_operation_id = accepted["operation"]["value"]
    cancelled = _structured(
        await client.call_tool(
            "aar_operation_cancel",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "operation.cancel",
                    f"cancel-{scenario_id}",
                ),
                "operation_id": cancel_operation_id,
            },
        )
    )
    if accepted["state"] != "accepted" or cancelled["state"] != "cancelled":
        raise RuntimeError("start-only cancellation path was not distinguishable")

    stale = _structured(
        await client.call_tool(
            "aar_workspace_execute",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.execute",
                    f"stale-{scenario_id}",
                ),
                "workspace_id": workspace_id,
                "expected_generation": created["handle"]["generation"],
                "expected_revision": 0,
                "action": "set",
                "key": "stale",
                "value": True,
            },
        )
    )
    if stale["failure"] is None or stale["failure"]["category"] != "stale":
        raise RuntimeError("stale workspace handle did not fail closed")

    checkpoint = _structured(
        await client.call_tool("aar_checkpoint_describe", {"context": read_context})
    )
    if checkpoint["supported"] is not True or checkpoint["failure"] is not None:
        raise RuntimeError("portable checkpoint capability was not explicit")

    program_workspace_id = f"workspace-program-{scenario_id}"
    program_created = _structured(
        await client.call_tool(
            "aar_program_workspace_create",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.program.create",
                    f"program-create-{scenario_id}",
                ),
                "workspace_id": program_workspace_id,
            },
        )
    )
    program_handle = program_created["handle"]
    if (
        program_created["failure"] is not None
        or program_handle["workspace"]
        != {"type": "workspace", "value": program_workspace_id}
        or program_handle["backend"]["kind"] != "ipython"
        or program_handle["generation"] != 1
        or program_handle["revision"] != 0
    ):
        raise RuntimeError("programmable workspace create did not return a fresh handle")
    program_attached = _structured(
        await client.call_tool(
            "aar_program_workspace_attach",
            {
                "context": read_context,
                "handle": program_handle,
            },
        )
    )
    if program_attached["handle"] != program_created["handle"]:
        raise RuntimeError("programmable workspace attach changed the handle")

    program_executed = _structured(
        await client.call_tool(
            "aar_program_workspace_execute",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.program.execute",
                    f"program-execute-{scenario_id}",
                ),
                "handle": program_handle,
                "code": (
                    "import IPython\n"
                    "import numpy as np\n"
                    "import pandas as pd\n"
                    "frame = pd.DataFrame({'qty': [3, 7, 8]})\n"
                    "answer = int(np.asarray(frame['qty']).sum())\n"
                    "aar_display({'answer': answer, 'rows': int(len(frame))})\n"
                    "{'answer': answer, 'rows': int(len(frame)), "
                    "'ipython': IPython.__version__, 'numpy': np.__version__, "
                    "'pandas': pd.__version__}"
                ),
                "wall_time_ms": 30_000,
            },
        )
    )
    program_result = program_executed["result"]
    if (
        program_executed["failure"] is not None
        or program_executed["operation"]["state"] != "succeeded"
        or program_result["result"]["answer"] != 18
        or program_result["result"]["rows"] != 3
        or not program_result["result"]["ipython"]
        or not program_result["result"]["numpy"]
        or not program_result["result"]["pandas"]
        or program_result["revision_after"] != 1
    ):
        raise RuntimeError(
            "programmable workspace did not execute with its declared analysis dependencies: "
            f"{program_executed!r}"
        )
    program_operation_id = program_executed["operation"]["operation"]["value"]
    executed_handle = {
        "workspace": program_result["workspace"],
        "backend": program_result["backend"],
        "generation": program_result["generation"],
        "revision": program_result["revision_after"],
    }

    program_inspected = _structured(
        await client.call_tool(
            "aar_program_workspace_inspect",
            {
                "context": read_context,
                "handle": executed_handle,
            },
        )
    )
    variable_names = {
        item["name"] for item in program_inspected["snapshot"]["variables"]
    }
    if not {"IPython", "answer", "frame", "np", "pd"}.issubset(variable_names):
        raise RuntimeError("programmable inspection omitted user namespace values")

    program_reconciled = _structured(
        await client.call_tool(
            "aar_program_workspace_reconcile",
            {
                "context": read_context,
                "handle": executed_handle,
                "operation_id": program_operation_id,
            },
        )
    )
    if program_reconciled["result"]["result"] != program_result:
        raise RuntimeError("programmable reconciliation did not return the backend receipt")

    program_checkpointed = _structured(
        await client.call_tool(
            "aar_program_workspace_checkpoint",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.program.checkpoint",
                    f"program-checkpoint-{scenario_id}",
                ),
                "handle": executed_handle,
            },
        )
    )
    program_checkpoint = program_checkpointed["manifest"]
    if program_checkpointed["operation"]["state"] != "succeeded":
        raise RuntimeError("portable checkpoint operation did not succeed")
    if [item["name"] for item in program_checkpoint["values"]] != ["answer"]:
        raise RuntimeError("portable checkpoint did not preserve the JSON-subset value")
    if [item["name"] for item in program_checkpoint["exclusions"]] != [
        "IPython",
        "frame",
        "np",
        "pd",
    ]:
        raise RuntimeError("portable checkpoint did not report the live-value exclusion")

    program_restored = _structured(
        await client.call_tool(
            "aar_program_workspace_restore",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.program.restore",
                    f"program-restore-{scenario_id}",
                ),
                "workspace_id": program_workspace_id,
                "manifest": program_checkpoint,
                "expected_handle": executed_handle,
            },
        )
    )
    if program_restored["handle"]["generation"] != 2:
        raise RuntimeError("programmable restore did not fence a new generation")
    restored_handle = program_restored["handle"]
    program_health = _structured(
        await client.call_tool(
            "aar_program_workspace_health",
            {
                "context": read_context,
                "handle": restored_handle,
            },
        )
    )
    if program_health["health"]["status"] != "ready":
        raise RuntimeError("restored programmable worker was not ready")
    program_closed = _structured(
        await client.call_tool(
            "aar_program_workspace_close",
            {
                "context": _mutation(
                    read_context,
                    grants,
                    "workspace.program.close",
                    f"program-close-{scenario_id}",
                ),
                "handle": restored_handle,
                "reason": "compatibility scenario complete",
            },
        )
    )
    if not program_closed["result"]["closed"]:
        raise RuntimeError("programmable workspace close was not acknowledged")

    return {
        "checks": {
            "analysis_dependencies": "passed",
            "artifact_readback": "passed",
            "cancel": "passed",
            "capabilities": "passed",
            "checkpoint_portability": "passed",
            "create_execute_inspect": "passed",
            "program_create_execute_checkpoint_restore": "passed",
            "stale_handle": "passed",
            "status": "passed",
            "tool_discovery": "passed",
        },
        "observed": {
            "operation_skill_digest": capabilities["operation_skill_digest"],
            "operation_skill_version": capabilities["operation_skill_version"],
            "package_version": capabilities["package_version"],
            "client_protocol_version": client_protocol_version,
            "server_protocol_version": server_protocol_version,
            "schema_bundle_digest": capabilities["schema_bundle_digest"],
            "server_name": capabilities["server_name"],
            "tool_names": observed_tools,
            "tool_surface_digest": capabilities["tool_surface_digest"],
            "tool_surface_version": capabilities["tool_surface_version"],
        },
        "scenario_id": scenario_id,
        "schema_version": SMOKE_SCHEMA_VERSION,
        "status": "passed",
    }


async def _run(parameters: StdioServerParameters, scenario_id: str) -> dict[str, Any]:
    async with Client(stdio_client(parameters), mode="auto") as client:
        return await run_scenario(client, scenario_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", default="aar-mcp")
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--cwd", type=Path)
    parser.add_argument(
        "--scenario-id",
        help="Stable replay ID; defaults to a unique ID so persistent-state reruns stay fresh",
    )
    args = parser.parse_args(argv)
    scenario_id = _resolve_scenario_id(args.scenario_id)
    parameters = StdioServerParameters(
        command=args.command,
        args=args.arg,
        cwd=None if args.cwd is None else args.cwd.resolve(),
    )
    report = asyncio.run(_run(parameters, scenario_id))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
