from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client

import aar.mcp.server as server_module
from aar.asset_models import (
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    AgentFingerprint,
    Episode,
)
from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.models import McpMutationContext
from aar.mcp.server import _envelope, _envelope_handle, build_server
from aar.rlm_models import RlmJobSpec
from aar.runtime.model_broker import ReferenceModelBroker, StaticModelBrokerRegistry
from aar.runtime.rlm import SimulatedRlmProcessLoss
from aar.runtime.workspace_models import ProgrammableWorkspaceHandle, WorkspaceProgramSpec
from aar.schemas import (
    Budget,
    Grant,
    OperationRef,
    OperationState,
    PrincipalRef,
    SessionRef,
)


def test_fixed_hermes_sampling_cli_warns_and_names_caller_delegated_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Server:
        def run(self, *, transport: str) -> None:
            assert transport == "stdio"

    class _Application:
        server = _Server()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        server_module,
        "hermes_mcp_sampling_luna_max_registry",
        lambda: (object(), object(), "deprecated-profile"),
    )
    monkeypatch.setattr(server_module, "build_server", lambda *_args, **_kwargs: _Application())

    with pytest.warns(FutureWarning, match="caller-delegated RLM"):
        assert (
            server_module.main(
                [
                    "--database",
                    str(tmp_path / "deprecated-sampling.sqlite3"),
                    "--programmable-backend",
                    "plain",
                    "--hermes-mcp-sampling-luna-max",
                ]
            )
            == 0
        )


ROOT = Path(__file__).resolve().parents[1]
NOW_MS = 1_700_000_000_000
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
    "aar_rlm_workbench_execute",
    "aar_rlm_workbench_capabilities",
    "aar_rlm_workbench_status",
    "aar_broker_work_claim",
    "aar_broker_work_mark_send_started",
    "aar_broker_work_cancel_before_send",
    "aar_broker_work_commit",
    "aar_broker_work_reconcile",
)
FROZEN_V7_TOOLS = EXPECTED_TOOLS[:30]


def _structured(result) -> dict[str, Any]:
    assert not result.is_error
    assert result.structured_content is not None
    assert len(result.content) == 1
    text_value = json.loads(result.content[0].text)
    assert text_value == result.structured_content
    return result.structured_content


def _program_handle(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace": result["workspace"],
        "backend": result["backend"],
        "generation": result["generation"],
        "revision": result["revision_after"],
    }


async def _capabilities(client: Client) -> dict[str, Any]:
    return _structured(await client.call_tool("aar_capabilities"))


def _read_context(capabilities: dict[str, Any], *, deadline: int = NOW_MS + 10_000):
    return {
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "principal_id": "principal-mcp-test",
        "session_id": "session-mcp-test",
        "deadline_unix_ms": deadline,
    }


def _mutation_context(
    capabilities: dict[str, Any],
    suffix: str,
    *,
    capability: str,
    deadline: int = NOW_MS + 10_000,
):
    grants = {
        descriptor["capability"]: descriptor["grant_id"]
        for descriptor in capabilities["reference_grants"]
    }
    return {
        **_read_context(capabilities, deadline=deadline),
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_id": grants[capability],
        "budget_wall_time_ms": max(1, deadline - NOW_MS),
    }


def _rlm_context(
    capabilities: dict[str, Any],
    suffix: str,
    *,
    include_evidence: bool = False,
    deadline: int = NOW_MS + 10_000,
):
    grants = {
        descriptor["capability"]: descriptor["grant_id"]
        for descriptor in capabilities["reference_grants"]
    }
    required = [grants["model.request"], grants["rlm.execute"]]
    if include_evidence:
        required.append(grants["evidence.query"])
    return {
        **_read_context(capabilities, deadline=deadline),
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_ids": sorted(required),
        "budget_wall_time_ms": max(1, deadline - NOW_MS),
        "budget_model_requests": 2,
        "budget_input_tokens": 1_024,
        "budget_output_tokens": 64,
        "budget_child_operations": 0,
        "budget_artifact_bytes": 0,
    }


async def _create_workspace(
    client: Client,
    capabilities: dict[str, Any],
    *,
    workspace_id: str = "workspace-mcp-test",
):
    return _structured(
        await client.call_tool(
            "aar_workspace_create",
            {
                "context": _mutation_context(
                    capabilities,
                    f"create-{workspace_id}",
                    capability="workspace.create",
                ),
                "workspace_id": workspace_id,
            },
        )
    )


async def _create_program_workspace(
    client: Client,
    capabilities: dict[str, Any],
    *,
    workspace_id: str = "workspace-program-mcp-test",
):
    return _structured(
        await client.call_tool(
            "aar_program_workspace_create",
            {
                "context": _mutation_context(
                    capabilities,
                    f"program-create-{workspace_id}",
                    capability="workspace.program.create",
                ),
                "workspace_id": workspace_id,
            },
        )
    )


def test_in_process_discovery_and_capability_fallback_are_deterministic(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        application = build_server(tmp_path / "in-process.sqlite3", now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server, mode="auto") as client:
                assert str(client.protocol_version) == "2026-07-28"
                first = await client.list_tools()
                second = await client.list_tools(cache_mode="reload")
                assert tuple(tool.name for tool in first.tools) == EXPECTED_TOOLS
                assert tuple(tool.name for tool in second.tools) == EXPECTED_TOOLS

                capabilities = await _capabilities(client)
                assert capabilities["server_name"] == "aar-mcp"
                assert capabilities["sdk_version"] == "2.0.0"
                assert capabilities["negotiated_protocol_version"] == "2026-07-28"
                assert tuple(capabilities["tool_names"]) == FROZEN_V7_TOOLS
                assert capabilities["operation_skill_digest"] != f"sha256:{'0' * 64}"
                assert capabilities["authority_statement"] == (
                    "AAR computes and proposes. The host authorizes and delivers."
                )
        finally:
            application.close()

    asyncio.run(scenario())


def test_build_server_injects_and_projects_non_secret_model_routes(tmp_path: Path) -> None:
    class ClosingBroker(ReferenceModelBroker):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            super().close()

    async def scenario() -> None:
        profile = ModelRouteProfile(
            profile_id="owner-gateway-v1",
            provider_driver="owner-gateway-driver-v1",
            provider="provider",
            model="model",
            reasoning_effort="high",
            max_output_tokens=128,
        )
        catalog = ModelRouteCatalog.issue((profile,))
        broker = ClosingBroker()
        registry = StaticModelBrokerRegistry(
            catalog,
            brokers={profile.profile_id: broker},
        )
        application = build_server(
            tmp_path / "model-routes.sqlite3",
            now_ms=lambda: NOW_MS,
            model_broker_registry=registry,
            default_model_route_profile=profile.profile_id,
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                assert capabilities["model_routes"] == catalog.model_dump(mode="json")
                assert capabilities["model_broker"] == {
                    "configured": True,
                    "provider_connectivity": "not_probed",
                    "default_profile_id": profile.profile_id,
                    "catalog_digest": catalog.catalog_digest,
                    "route_profile_count": 1,
                    "journal_schema_versions": [1],
                }
                encoded = canonical_json_bytes(
                    {
                        "model_routes": capabilities["model_routes"],
                        "model_broker": capabilities["model_broker"],
                    }
                )
                assert b"credential-canary" not in encoded
                assert b"authorization" not in encoded.lower()
        finally:
            application.close()
        assert broker.close_calls == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("mode", "expected_protocol"),
    (("auto", "2026-07-28"), ("legacy", "2025-11-25")),
)
def test_real_stdio_client_lists_and_calls_both_protocol_eras(
    tmp_path: Path,
    mode: str,
    expected_protocol: str,
) -> None:
    async def scenario() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "aar.mcp.server",
                "--database",
                str(tmp_path / f"stdio-{mode}.sqlite3"),
            ],
            cwd=ROOT,
        )
        async with Client(stdio_client(parameters), mode=mode) as client:
            assert str(client.protocol_version) == expected_protocol
            tools = await client.list_tools()
            assert tuple(tool.name for tool in tools.tools) == EXPECTED_TOOLS
            capabilities = await _capabilities(client)
            assert capabilities["server_now_unix_ms"] > 0
            assert capabilities["protocol_versions"] == [
                "2026-07-28",
                "2025-11-25",
                "2025-06-18",
                "2025-03-26",
                "2024-11-05",
            ]
            assert capabilities["negotiated_protocol_version"] == expected_protocol
            assert expected_protocol in capabilities["protocol_versions"]

    asyncio.run(scenario())


def test_reference_context_prepares_a_copy_ready_flat_mutation(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(tmp_path / "reference-context.sqlite3", now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                prepared = _structured(
                    await client.call_tool(
                        "aar_reference_context",
                        {
                            "capability": "workspace.create",
                            "context_key": "create-copy-ready-workspace",
                            "budget_wall_time_ms": 10_000,
                        },
                    )
                )
                assert prepared["failure"] is None
                assert prepared["read_context"] == {
                    "runtime_generation": capabilities["ready"]["runtime_generation"],
                    "capability_digest": capabilities["ready"]["capabilities"]["digest"],
                    "principal_id": "reference-principal",
                    "session_id": "reference-session-1",
                    "deadline_unix_ms": NOW_MS + 10_000,
                }
                assert prepared["context"] == {
                    **prepared["read_context"],
                    "request_id": "request-create-copy-ready-workspace",
                    "idempotency_key": "idempotency-create-copy-ready-workspace",
                    "grant_id": "reference-grant-workspace-create",
                    "budget_wall_time_ms": 10_000,
                }

                created = _structured(
                    await client.call_tool(
                        "aar_workspace_create",
                        {
                            "context": prepared["context"],
                            "workspace_id": "copy-ready-workspace",
                        },
                    )
                )
                assert created["failure"] is None
                assert created["handle"]["workspace"]["value"] == "copy-ready-workspace"
        finally:
            application.close()

    asyncio.run(scenario())


def test_skill_guided_create_execute_inspect_and_status(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(tmp_path / "happy.sqlite3", now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                assert capabilities["server_now_unix_ms"] == NOW_MS
                created = await _create_workspace(client, capabilities)
                assert created["failure"] is None
                assert created["handle"] == {
                    "workspace": {"type": "workspace", "value": "workspace-mcp-test"},
                    "generation": 1,
                    "revision": 0,
                }

                executed = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "execute-happy",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "answer",
                            "value": 42,
                        },
                    )
                )
                assert executed["state"] == "succeeded"
                assert executed["certainty"] == "certain"
                assert len(executed["artifacts"]) == 1
                operation_id = executed["operation"]["value"]

                inspected = _structured(
                    await client.call_tool(
                        "aar_workspace_inspect",
                        {
                            "context": _read_context(capabilities),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 1,
                        },
                    )
                )
                assert inspected["snapshot"]["values"] == [["answer", 42]]

                status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert status["state"] == "succeeded"
                assert status["result"]["revision_after"] == 1
        finally:
            application.close()

    asyncio.run(scenario())


def test_asset_bundle_round_trip_and_unknown_outcome_over_mcp(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "assets.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                fingerprint = AdaptiveAssetDocument.issue(
                    AgentFingerprint(
                        runtime_digest=canonical_sha256({"runtime": "mcp"}),
                        capability_digest=capabilities["ready"]["capabilities"]["digest"],
                        policy_digest=canonical_sha256({"policy": "bounded"}),
                    )
                )
                episode = AdaptiveAssetDocument.issue(
                    Episode(
                        operation=OperationRef(value="operation-mcp-asset"),
                        agent=fingerprint.manifest.asset,
                        request_digest=canonical_sha256({"request": "asset-round-trip"}),
                    )
                )
                bundle = AdaptiveAssetBundle.issue(documents=(fingerprint, episode))
                imported = _structured(
                    await client.call_tool(
                        "aar_asset_import",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "asset-import",
                                capability="asset.import",
                            ),
                            "bundle": bundle.model_dump(mode="json"),
                        },
                    )
                )
                assert imported["state"] == "succeeded"
                assert imported["result"] == {
                    "active_serving_mutated": False,
                    "asset_count": 2,
                    "bundle_digest": bundle.bundle_digest,
                    "event_count": 0,
                }

                fetched = _structured(
                    await client.call_tool(
                        "aar_asset_get",
                        {
                            "context": _read_context(capabilities),
                            "kind": fingerprint.manifest.asset.kind,
                            "digest": fingerprint.manifest.asset.digest,
                        },
                    )
                )
                assert fetched["document"] == fingerprint.model_dump(mode="json")

                exported = _structured(
                    await client.call_tool(
                        "aar_asset_export",
                        {
                            "context": _read_context(capabilities),
                            "roots": [episode.manifest.asset.model_dump(mode="json")],
                        },
                    )
                )
                assert exported["bundle"] == bundle.model_dump(mode="json")

                observation = _structured(
                    await client.call_tool(
                        "aar_asset_outcome",
                        {
                            "context": _read_context(capabilities),
                            "episode": episode.manifest.asset.model_dump(mode="json"),
                        },
                    )
                )
                assert observation["observation"] == {
                    "episode": episode.manifest.asset.model_dump(mode="json"),
                    "outcome": None,
                    "status": "unknown",
                }
        finally:
            application.close()

    asyncio.run(scenario())


def test_brokered_rlm_mcp_slice_is_progressive_bounded_and_cancellable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "rlm-mcp.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                executed = _structured(
                    await client.call_tool(
                        "aar_rlm_execute",
                        {
                            "context": _rlm_context(
                                capabilities,
                                "rlm-evidence",
                                include_evidence=True,
                            ),
                            "query": "portable runtime",
                            "strategy": "evidence_synthesis",
                            "max_steps": 2,
                        },
                    )
                )
                assert executed["state"] == "succeeded"
                assert executed["result"]["schema_version"] == "aar.rlm.v1"
                assert [step["action"] for step in executed["result"]["steps"]] == [
                    "evidence.query",
                    "model.request",
                ]
                operation_id = executed["operation"]["value"]

                status = _structured(
                    await client.call_tool(
                        "aar_rlm_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert status["snapshot"]["state"] == "succeeded"
                assert status["snapshot"]["usage"]["model_requests"] == 1

                catalog = _structured(
                    await client.call_tool(
                        "aar_broker_catalog",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert [method["name"] for method in catalog["catalog"]["methods"]] == [
                    "evidence.query",
                    "model.request",
                ]
                assert all(
                    "request_schema_json" not in method for method in catalog["catalog"]["methods"]
                )

                described = _structured(
                    await client.call_tool(
                        "aar_broker_describe",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                            "methods": ["model.request"],
                        },
                    )
                )
                contract = described["contracts"]["contracts"][0]
                assert contract["request_model"] == "ModelRequest"
                assert '"prompt"' in contract["request_schema_json"]
                assert contract["contract_digest"].startswith("sha256:")

                denied = _structured(
                    await client.call_tool(
                        "aar_broker_describe",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                            "methods": ["artifact.put"],
                        },
                    )
                )
                assert denied["failure"]["code"] == "BROKER_GRANT_DENIED"

                accepted = _structured(
                    await client.call_tool(
                        "aar_rlm_execute",
                        {
                            "context": _rlm_context(capabilities, "rlm-cancel"),
                            "query": "cancel me",
                            "strategy": "baseline",
                            "max_steps": 1,
                            "start_only": True,
                        },
                    )
                )
                cancelled_id = accepted["operation"]["value"]
                cancelled = _structured(
                    await client.call_tool(
                        "aar_operation_cancel",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "rlm-cancel-operation",
                                capability="operation.cancel",
                            ),
                            "operation_id": cancelled_id,
                        },
                    )
                )
                assert cancelled["state"] in {"running", "cancelled"}
                cancelled_status = None
                for _ in range(100):
                    cancelled_status = _structured(
                        await client.call_tool(
                            "aar_rlm_status",
                            {
                                "context": _read_context(capabilities),
                                "operation_id": cancelled_id,
                            },
                        )
                    )
                    if cancelled_status["snapshot"]["state"] == "cancelled":
                        break
                    await asyncio.sleep(0.01)
                assert cancelled_status is not None
                assert cancelled_status["snapshot"]["state"] == "cancelled"
        finally:
            application.close()

    asyncio.run(scenario())


def test_program_workspace_mcp_plain_lifecycle_is_complete(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "program-plain.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                created = await _create_program_workspace(client, capabilities)
                assert created["failure"] is None
                assert created["handle"]["generation"] == 1
                assert created["handle"]["revision"] == 0
                created_handle = created["handle"]

                attached = _structured(
                    await client.call_tool(
                        "aar_program_workspace_attach",
                        {
                            "context": _read_context(capabilities),
                            "handle": created_handle,
                        },
                    )
                )
                assert attached["handle"] == created["handle"]

                executed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-execute-plain",
                                capability="workspace.program.execute",
                            ),
                            "handle": created_handle,
                            "code": ("answer = 6 * 7\naar_display({'answer': answer})\nanswer"),
                            "wall_time_ms": 1_000,
                        },
                    )
                )
                assert executed["failure"] is None
                assert executed["result"]["status"] == "succeeded"
                assert executed["result"]["result"] == 42
                assert executed["result"]["revision_after"] == 1
                operation_id = executed["operation"]["operation"]["value"]
                executed_handle = _program_handle(executed["result"])

                inspected = _structured(
                    await client.call_tool(
                        "aar_program_workspace_inspect",
                        {
                            "context": _read_context(capabilities),
                            "handle": executed_handle,
                        },
                    )
                )
                variables = {item["name"]: item for item in inspected["snapshot"]["variables"]}
                assert variables["answer"]["preview"] == "42"

                reconciled = _structured(
                    await client.call_tool(
                        "aar_program_workspace_reconcile",
                        {
                            "context": _read_context(capabilities),
                            "handle": executed_handle,
                            "operation_id": operation_id,
                        },
                    )
                )
                assert reconciled["result"]["state"] == "completed"
                assert reconciled["result"]["result"] == executed["result"]

                interrupted = _structured(
                    await client.call_tool(
                        "aar_program_workspace_interrupt",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-interrupt-complete",
                                capability="workspace.program.interrupt",
                            ),
                            "handle": executed_handle,
                            "operation_id": operation_id,
                        },
                    )
                )
                assert interrupted["result"]["accepted"] is False

                checkpointed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_checkpoint",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-checkpoint-plain",
                                capability="workspace.program.checkpoint",
                            ),
                            "handle": executed_handle,
                        },
                    )
                )
                manifest = checkpointed["manifest"]
                assert [item["name"] for item in manifest["values"]] == ["answer"]
                assert manifest["content_digest"].startswith("sha256:")
                assert manifest["source_handle"] == executed_handle
                assert checkpointed["operation"]["state"] == "succeeded"

                stale_handle = {**executed_handle, "revision": 0}

                stale_restore = _structured(
                    await client.call_tool(
                        "aar_program_workspace_restore",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-restore-stale",
                                capability="workspace.program.restore",
                            ),
                            "workspace_id": "workspace-program-mcp-test",
                            "manifest": manifest,
                            "expected_handle": stale_handle,
                        },
                    )
                )
                assert stale_restore["failure"]["code"] == "STALE_RUNTIME_STATE"
                assert stale_restore["operation"]["state"] == "failed"

                restored = _structured(
                    await client.call_tool(
                        "aar_program_workspace_restore",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-restore-plain",
                                capability="workspace.program.restore",
                            ),
                            "workspace_id": "workspace-program-mcp-test",
                            "manifest": manifest,
                            "expected_handle": executed_handle,
                        },
                    )
                )
                assert restored["handle"]["generation"] == 2
                assert restored["handle"]["revision"] == 0
                restored_handle = restored["handle"]

                health = _structured(
                    await client.call_tool(
                        "aar_program_workspace_health",
                        {
                            "context": _read_context(capabilities),
                            "handle": restored_handle,
                        },
                    )
                )
                assert health["health"]["status"] == "ready"

                closed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_close",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-close-plain",
                                capability="workspace.program.close",
                            ),
                            "handle": restored_handle,
                            "reason": "test complete",
                        },
                    )
                )
                assert closed["result"]["closed"] is True
                assert closed["operation"]["state"] == "succeeded"
        finally:
            application.close()

    asyncio.run(scenario())


def test_program_workspace_mcp_persists_failure_and_timeout_receipts(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "program-receipts.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                created = await _create_program_workspace(
                    client,
                    capabilities,
                    workspace_id="workspace-program-receipts",
                )
                created_handle = created["handle"]
                failed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-failed-receipt",
                                capability="workspace.program.execute",
                            ),
                            "handle": created_handle,
                            "code": "before_failure = 7\nraise ValueError('broken')",
                        },
                    )
                )
                assert failed["result"]["status"] == "failed"
                assert failed["operation"]["state"] == "failed"
                assert failed["failure"]["code"] == "WORKSPACE_EXECUTION_FAILED"
                operation_id = failed["operation"]["operation"]["value"]
                status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert status["state"] == "failed"
                assert status["result"]["status"] == "failed"
                failed_handle = _program_handle(failed["result"])

                timed_out = _structured(
                    await client.call_tool(
                        "aar_program_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-timeout-receipt",
                                capability="workspace.program.execute",
                            ),
                            "handle": failed_handle,
                            "code": "while True:\n    pass",
                            "wall_time_ms": 10,
                        },
                    )
                )
                assert timed_out["result"]["status"] == "timed_out"
                assert timed_out["operation"]["state"] == "timed_out"
                assert timed_out["operation"]["result"]["status"] == "timed_out"
        finally:
            application.close()

    asyncio.run(scenario())


def test_program_workspace_mcp_interrupts_worker_before_cancelling_receipt(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "program-interrupt.sqlite3",
            now_ms=lambda: NOW_MS,
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                created = await _create_program_workspace(
                    client,
                    capabilities,
                    workspace_id="workspace-program-interrupt-mcp",
                )
                handle = ProgrammableWorkspaceHandle.model_validate_json(
                    json.dumps(created["handle"]),
                    strict=True,
                )
                executing = asyncio.create_task(
                    client.call_tool(
                        "aar_program_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-interrupt-live",
                                capability="workspace.program.execute",
                                deadline=NOW_MS + 60_000,
                            ),
                            "handle": created["handle"],
                            "code": "import time\ntime.sleep(60)",
                            "wall_time_ms": 60_000,
                        },
                    )
                )
                deadline = asyncio.get_running_loop().time() + 5
                while True:
                    health = await asyncio.to_thread(
                        application.host.program_workspace.health,
                        handle,
                    )
                    if health.status == "busy":
                        break
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AssertionError("programmable worker did not become busy")
                    await asyncio.sleep(0.01)
                assert health.running_operation is not None
                operation_id = health.running_operation.value

                generic_cancel = _structured(
                    await client.call_tool(
                        "aar_operation_cancel",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "generic-cancel-program",
                                capability="operation.cancel",
                            ),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert generic_cancel["failure"]["code"] == "OPERATION_CONFLICT"

                interrupted = _structured(
                    await client.call_tool(
                        "aar_program_workspace_interrupt",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-interrupt-worker",
                                capability="workspace.program.interrupt",
                            ),
                            "handle": created["handle"],
                            "operation_id": operation_id,
                        },
                    )
                )
                assert interrupted["result"]["accepted"] is True
                executed = _structured(await executing)
                assert executed["result"]["status"] == "interrupted"
                assert executed["result"]["workspace_lost"] is True
                assert executed["operation"]["state"] == "cancelled"
                assert executed["operation"]["result"]["status"] == "interrupted"
        finally:
            application.close()

    asyncio.run(scenario())


def test_program_workspace_mcp_reaches_real_ipython_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "program-ipython.sqlite3",
            now_ms=lambda: NOW_MS,
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                created = await _create_program_workspace(
                    client,
                    capabilities,
                    workspace_id="workspace-program-ipython-mcp-test",
                )
                assert created["failure"] is None
                created_handle = created["handle"]
                executed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-execute-ipython",
                                capability="workspace.program.execute",
                            ),
                            "handle": created_handle,
                            "code": (
                                "import IPython\n"
                                "answer = 6 * 7\n"
                                "{'answer': answer, 'ipython': IPython.__version__}"
                            ),
                        },
                    )
                )
                assert executed["failure"] is None
                assert executed["result"]["result"] == {
                    "answer": 42,
                    "ipython": "9.16.1",
                }
                executed_handle = _program_handle(executed["result"])

                checkpointed = _structured(
                    await client.call_tool(
                        "aar_program_workspace_checkpoint",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-checkpoint-ipython",
                                capability="workspace.program.checkpoint",
                            ),
                            "handle": executed_handle,
                        },
                    )
                )
                assert [item["name"] for item in checkpointed["manifest"]["values"]] == ["answer"]
                assert [item["name"] for item in checkpointed["manifest"]["exclusions"]] == [
                    "IPython"
                ]
        finally:
            application.close()

    asyncio.run(scenario())


def test_start_only_cancel_timeout_and_stale_revision_are_distinct(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(tmp_path / "failures.sqlite3", now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                await _create_workspace(client, capabilities)

                denied_context = _mutation_context(
                    capabilities,
                    "unissued-grant",
                    capability="workspace.execute",
                )
                denied_context["grant_id"] = "unissued-grant"
                denied = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": denied_context,
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "denied",
                            "value": True,
                        },
                    )
                )
                assert denied["failure"]["code"] == "AUTHORITY_DENIED"

                accepted = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "start-only",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "cancelled",
                            "value": True,
                            "start_only": True,
                        },
                    )
                )
                assert accepted["state"] == "accepted"
                cancelled = _structured(
                    await client.call_tool(
                        "aar_operation_cancel",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "cancel",
                                capability="operation.cancel",
                            ),
                            "operation_id": accepted["operation"]["value"],
                        },
                    )
                )
                assert cancelled["state"] == "cancelled"

                succeeded = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "execute-first",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "current",
                            "value": 1,
                        },
                    )
                )
                assert succeeded["state"] == "succeeded"

                stale = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "execute-stale",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "stale",
                            "value": 2,
                        },
                    )
                )
                assert stale["state"] == "failed"
                assert stale["failure"]["code"] == "WORKSPACE_REVISION_CONFLICT"

                expired = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "execute-expired",
                                capability="workspace.execute",
                                deadline=NOW_MS,
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 1,
                            "action": "set",
                            "key": "expired",
                            "value": 3,
                        },
                    )
                )
                assert expired["state"] is None
                assert expired["failure"]["code"] == "DEADLINE_EXPIRED"
                assert expired["failure"]["category"] == "deadline"
        finally:
            application.close()

    asyncio.run(scenario())


def test_restart_exposes_indeterminate_and_reconcile_without_receipt(tmp_path: Path) -> None:
    database = tmp_path / "restart.sqlite3"

    async def prepare() -> tuple[dict[str, Any], str]:
        application = build_server(database, now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                await _create_workspace(client, capabilities)
                accepted = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "restart-running",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "lost",
                            "value": True,
                            "start_only": True,
                        },
                    )
                )
                operation_id = accepted["operation"]["value"]
                application.host.registry.begin(
                    OperationRef(value=operation_id),
                    application.host.runtime_generation,
                )
                return capabilities, operation_id
        finally:
            application.close()

    prior_capabilities, operation_id = asyncio.run(prepare())

    async def recover() -> None:
        application = build_server(database, now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                assert capabilities["ready"]["runtime_generation"] == 2

                stale_status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(prior_capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert stale_status["failure"]["code"] == "STALE_RUNTIME_STATE"

                status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert status["state"] == "indeterminate"
                assert status["certainty"] == "indeterminate"
                assert status["reconciliation_required"] is True

                reconciled = _structured(
                    await client.call_tool(
                        "aar_operation_reconcile",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "reconcile-lost",
                                capability="operation.reconcile",
                            ),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert reconciled["state"] == "failed"
                assert reconciled["certainty"] == "certain"
                assert reconciled["failure"]["code"] == "NO_WORKSPACE_RECEIPT"
        finally:
            application.close()

    asyncio.run(recover())


def test_program_restart_keeps_missing_worker_receipt_indeterminate(tmp_path: Path) -> None:
    database = tmp_path / "program-restart.sqlite3"

    async def prepare() -> tuple[str, dict[str, Any]]:
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                created = await _create_program_workspace(
                    client,
                    capabilities,
                    workspace_id="workspace-program-restart",
                )
                handle = ProgrammableWorkspaceHandle.model_validate(created["handle"], strict=True)
                context = McpMutationContext.model_validate(
                    _mutation_context(
                        capabilities,
                        "program-restart-running",
                        capability="workspace.program.execute",
                    ),
                    strict=True,
                )
                spec = WorkspaceProgramSpec(code="answer = 42")
                payload = {
                    "handle": handle,
                    "kind": "workspace.program.execute",
                    "spec": spec,
                }
                envelope = _envelope(
                    application.host,
                    context,
                    "workspace.program.execute",
                    canonical_sha256(payload),
                    workspace=_envelope_handle(handle),
                )
                record, _created = application.host.registry.accept(
                    envelope, canonical_json_bytes(payload).decode()
                )
                application.host.registry.begin(
                    record.operation,
                    application.host.runtime_generation,
                )
                return record.operation.value, created["handle"]
        finally:
            application.close()

    operation_id, handle = asyncio.run(prepare())

    async def recover() -> None:
        application = build_server(
            database,
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert status["state"] == "indeterminate"
                assert status["certainty"] == "indeterminate"

                generic = _structured(
                    await client.call_tool(
                        "aar_operation_reconcile",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "program-restart-generic-reconcile",
                                capability="operation.reconcile",
                            ),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert generic["state"] == "indeterminate"
                assert generic["certainty"] == "indeterminate"
                assert generic["reconciliation_required"] is True
                assert generic["failure"]["code"] == "PROGRAM_RECEIPT_UNAVAILABLE_AFTER_RESTART"

                wrong_handle = {**handle, "revision": 1}
                denied = _structured(
                    await client.call_tool(
                        "aar_program_workspace_reconcile",
                        {
                            "context": _read_context(capabilities),
                            "handle": wrong_handle,
                            "operation_id": operation_id,
                        },
                    )
                )
                assert denied["failure"]["code"] == "AUTHORITY_DENIED"

                program = _structured(
                    await client.call_tool(
                        "aar_program_workspace_reconcile",
                        {
                            "context": _read_context(capabilities),
                            "handle": handle,
                            "operation_id": operation_id,
                        },
                    )
                )
                assert program["result"]["state"] == "lost"
                assert program["failure"]["code"] == "PROGRAM_RECEIPT_UNAVAILABLE_AFTER_RESTART"
                assert program["failure"]["certainty"] == "indeterminate"

                final_status = _structured(
                    await client.call_tool(
                        "aar_operation_status",
                        {
                            "context": _read_context(capabilities),
                            "operation_id": operation_id,
                        },
                    )
                )
                assert final_status["state"] == "indeterminate"
        finally:
            application.close()

    asyncio.run(recover())


def test_checkpoint_contract_and_artifact_resolution_are_bounded(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        application = build_server(tmp_path / "artifact.sqlite3", now_ms=lambda: NOW_MS)
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                await _create_workspace(client, capabilities)
                executed = _structured(
                    await client.call_tool(
                        "aar_workspace_execute",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "artifact-parent",
                                capability="workspace.execute",
                            ),
                            "workspace_id": "workspace-mcp-test",
                            "expected_generation": 1,
                            "expected_revision": 0,
                            "action": "set",
                            "key": "artifact",
                            "value": True,
                        },
                    )
                )
                operation = OperationRef(value=executed["operation"]["value"])
                reference = executed["artifacts"][0]

                checkpoint = _structured(
                    await client.call_tool(
                        "aar_checkpoint_describe",
                        {"context": _read_context(capabilities)},
                    )
                )
                assert checkpoint["supported"] is True
                assert checkpoint["portable_media_types"] == [
                    "application/vnd.aar.workspace-checkpoint.v1+json"
                ]
                assert checkpoint["failure"] is None

                resolved = _structured(
                    await client.call_tool(
                        "aar_artifact_resolve",
                        {
                            "context": _read_context(capabilities),
                            "artifact_id": reference["artifact"]["value"],
                            "digest": reference["digest"],
                            "media_type": reference["media_type"],
                            "size_bytes": reference["size_bytes"],
                            "created_by_operation_id": operation.value,
                            "redacted": reference["redacted"],
                            "max_bytes": reference["size_bytes"],
                        },
                    )
                )
                assert resolved["content_base64"] is not None

                denied = _structured(
                    await client.call_tool(
                        "aar_artifact_resolve",
                        {
                            "context": _read_context(capabilities),
                            "artifact_id": reference["artifact"]["value"],
                            "digest": reference["digest"],
                            "media_type": reference["media_type"],
                            "size_bytes": reference["size_bytes"],
                            "created_by_operation_id": operation.value,
                            "redacted": reference["redacted"],
                            "max_bytes": reference["size_bytes"] - 1,
                        },
                    )
                )
                assert denied["failure"]["code"] == "INVALID_ARGUMENT"
        finally:
            application.close()

    asyncio.run(scenario())


def test_operation_reconcile_exposes_proposal_only_compensation(tmp_path: Path) -> None:
    async def scenario() -> None:
        application = build_server(
            tmp_path / "mcp-compensation.sqlite3",
            now_ms=lambda: NOW_MS,
            programmable_backend="plain",
        )
        try:
            spec = RlmJobSpec(
                query="unknown model outcome",
                strategy="baseline",
                max_steps=1,
            )
            envelope = application.host.request_rlm_envelope(
                request_id="request-mcp-compensation",
                idempotency_key="idempotency-mcp-compensation",
                principal=PrincipalRef(value="principal-mcp-test"),
                session=SessionRef(value="session-mcp-test"),
                spec=spec,
                deadline_unix_ms=NOW_MS + 10_000,
                budget=Budget(
                    wall_time_ms=10_000,
                    model_requests=1,
                    input_tokens=1_024,
                    output_tokens=64,
                ),
            )
            compensation_grant = Grant(
                grant_id="grant-effect-propose",
                capability="effect.propose",
                issued_to=envelope.principal,
                expires_at_unix_ms=envelope.deadline_unix_ms,
            )
            envelope = envelope.model_copy(
                update={
                    "grants": tuple(
                        sorted(
                            (*envelope.grants, compensation_grant),
                            key=lambda grant: grant.grant_id,
                        )
                    )
                }
            )
            accepted = application.host.submit_rlm(envelope, spec)
            application.host.registry.begin(
                accepted.operation,
                application.host.runtime_generation,
            )

            def lose_model(_prompt: str, _context: Any) -> Any:
                raise SimulatedRlmProcessLoss()

            application.host.models.request = lose_model  # type: ignore[method-assign]
            with pytest.raises(SimulatedRlmProcessLoss):
                application.host.rlm.run(accepted.operation, envelope)
            application.host.registry.mark_indeterminate(
                accepted.operation,
                application.host.runtime_generation,
                "model_receipt_unknown",
            )

            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                denied = _structured(
                    await client.call_tool(
                        "aar_operation_reconcile",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "mcp-compensation-no-effect-grant",
                                capability="operation.reconcile",
                            ),
                            "operation_id": accepted.operation.value,
                            "propose_compensation": True,
                        },
                    )
                )
                assert denied["failure"]["code"] == "AUTHORITY_DENIED"
                reconciled = _structured(
                    await client.call_tool(
                        "aar_operation_reconcile",
                        {
                            "context": _mutation_context(
                                capabilities,
                                "mcp-compensation",
                                capability="operation.reconcile",
                            ),
                            "operation_id": accepted.operation.value,
                            "propose_compensation": True,
                            "compensation_grant_id": next(
                                descriptor["grant_id"]
                                for descriptor in capabilities["reference_grants"]
                                if descriptor["capability"] == "effect.propose"
                            ),
                        },
                    )
                )
                assert reconciled["state"] == OperationState.INDETERMINATE.value
                bound = application.host.brokers.bind(envelope, accepted.operation)
                trace = bound.traces()[0]
                assert trace.state == "failed"
                assert trace.reconciliation_action == "compensation_proposed"
                assert trace.compensation_digest is not None
                assert not hasattr(bound, "effect_execute")
        finally:
            application.close()

    asyncio.run(scenario())


def test_compensation_admission_rechecks_durable_control_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application = build_server(
        tmp_path / "compensation-control-race.sqlite3",
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
    )
    try:
        spec = RlmJobSpec(
            query="stale cancellation snapshot",
            strategy="baseline",
            max_steps=1,
        )
        envelope = application.host.request_rlm_envelope(
            request_id="request-compensation-control-race",
            idempotency_key="idempotency-compensation-control-race",
            principal=PrincipalRef(value="principal-mcp-test"),
            session=SessionRef(value="session-mcp-test"),
            spec=spec,
            deadline_unix_ms=NOW_MS + 10_000,
            budget=Budget(
                wall_time_ms=10_000,
                model_requests=1,
                input_tokens=1_024,
                output_tokens=64,
            ),
        )
        historical_effect_grant = Grant(
            grant_id="historical-grant-effect-propose",
            capability="effect.propose",
            issued_to=envelope.principal,
            expires_at_unix_ms=envelope.deadline_unix_ms,
        )
        envelope = envelope.model_copy(
            update={
                "grants": tuple(
                    sorted(
                        (*envelope.grants, historical_effect_grant),
                        key=lambda grant: grant.grant_id,
                    )
                )
            }
        )
        accepted = application.host.submit_rlm(envelope, spec)
        application.host.registry.begin(
            accepted.operation,
            application.host.runtime_generation,
        )

        def lose_model(_prompt: str, _context: Any) -> Any:
            raise SimulatedRlmProcessLoss()

        application.host.models.request = lose_model  # type: ignore[method-assign]
        with pytest.raises(SimulatedRlmProcessLoss):
            application.host.rlm.run(accepted.operation, envelope)
        application.host.registry.mark_indeterminate(
            accepted.operation,
            application.host.runtime_generation,
            "model_receipt_unknown",
        )

        journal = application.host.brokers._journal
        original_control_state = journal.operation_control_state

        def cancel_then_return_stale(_operation: OperationRef) -> tuple[int, bool]:
            application.host.registry.request_cancel(
                accepted.operation,
                f"sha256:{'c' * 64}",
                "race_cancel",
            )
            monkeypatch.setattr(
                journal,
                "operation_control_state",
                original_control_state,
            )
            return 0, False

        monkeypatch.setattr(
            journal,
            "operation_control_state",
            cancel_then_return_stale,
        )
        provider_calls = 0
        original_propose = application.host.effects.propose

        def count_proposal(*args, **kwargs):
            nonlocal provider_calls
            provider_calls += 1
            return original_propose(*args, **kwargs)

        application.host.effects.propose = count_proposal  # type: ignore[method-assign]
        report = application.host.reconcile_broker_calls(
            accepted.operation,
            current_capability_digest=application.host.capabilities.digest,
            current_compensation_grant=Grant(
                grant_id="current-grant-effect-propose",
                capability="effect.propose",
                issued_to=envelope.principal,
                expires_at_unix_ms=envelope.deadline_unix_ms,
            ),
            propose_compensation=True,
        )

        assert report.unresolved
        assert report.calls[0].action == "pending"
        assert provider_calls == 0
        traces = application.host.brokers.bind(envelope, accepted.operation).traces()
        assert [trace.method for trace in traces] == ["model.request"]
        assert traces[0].compensation_digest is None
        control = application.host.registry.continuity_snapshot(accepted.operation).control
        assert control.cancellation_requested is True
        assert control.control_revision == 1
    finally:
        application.close()
