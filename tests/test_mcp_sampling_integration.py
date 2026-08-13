from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import CreateMessageResult, SamplingCapability, TextContent

from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.mcp.server import build_server
from aar.providers.gateway import GatewayDriverManifest, OwnerGatewayModelBroker
from aar.providers.mcp_sampling import McpSamplingGatewayTransport
from aar.runtime.model_broker import StaticModelBrokerRegistry

NOW_MS = 1_700_000_000_000
ROOT = Path(__file__).resolve().parents[1]


def _structured(result) -> dict[str, Any]:
    assert not result.is_error
    assert result.structured_content is not None
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


async def _capabilities(client: Client) -> dict[str, Any]:
    return _structured(await client.call_tool("aar_capabilities"))


def _rlm_context(
    capabilities: dict[str, Any],
    suffix: str,
    *,
    now_ms: int = NOW_MS,
    output_tokens: int = 64,
) -> dict[str, Any]:
    grants = {
        item["capability"]: item["grant_id"]
        for item in capabilities["reference_grants"]
    }
    return {
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "principal_id": "principal-mcp-sampling-test",
        "session_id": "session-mcp-sampling-test",
        "deadline_unix_ms": now_ms + 10_000,
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_ids": sorted((grants["model.request"], grants["rlm.execute"])),
        "budget_wall_time_ms": 10_000,
        "budget_model_requests": 1,
        "budget_input_tokens": 1_024,
        "budget_output_tokens": output_tokens,
        "budget_child_operations": 0,
        "budget_artifact_bytes": 0,
    }


class _RecordingSamplingTransport(McpSamplingGatewayTransport):
    observed_error: tuple[str, str] | None = None

    def send(self, *args, **kwargs):
        try:
            return super().send(*args, **kwargs)
        except Exception as error:
            cause = error.__cause__
            observed = error if cause is None else cause
            self.observed_error = (type(observed).__name__, str(observed))
            raise


def _application(database: Path):
    profile = ModelRouteProfile(
        profile_id="gateway-luna-max-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    transport = _RecordingSamplingTransport(now_ms=lambda: NOW_MS)
    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="hermes-mcp-sampling-v1",
            driver_version="1.0.0",
            lookup_supported=False,
            cancellation_supported=True,
        ),
        transport=transport,
        credential_resolver=lambda _binding: b"mcp-client-owned-authority",
    )
    registry = StaticModelBrokerRegistry(
        ModelRouteCatalog.issue((profile,)),
        brokers={profile.profile_id: broker},
    )
    application = build_server(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
        dispatcher_concurrency=1,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
        mcp_sampling_transport=transport,
    )
    return application, transport


def test_binding_failure_returns_terminal_operation_identity(
    tmp_path: Path, monkeypatch
) -> None:
    async def scenario() -> None:
        application, transport = _application(tmp_path / "binding-failure.sqlite3")

        def reject_binding(*_args, **_kwargs):
            raise RuntimeError("sensitive session binding detail")

        monkeypatch.setattr(transport, "bind", reject_binding)

        async def unused_sample(_context, _params):
            raise AssertionError("binding failure must prevent sampling dispatch")

        try:
            async with Client(
                application.server,
                sampling_callback=unused_sample,
                sampling_capabilities=SamplingCapability(),
            ) as client:
                capabilities = await _capabilities(client)
                arguments = {
                    "context": _rlm_context(capabilities, "binding-failure"),
                    "query": "must remain durably observable",
                    "strategy": "baseline",
                    "max_steps": 1,
                }
                first = _structured(
                    await client.call_tool("aar_rlm_execute", arguments)
                )
                replayed = _structured(
                    await client.call_tool("aar_rlm_execute", arguments)
                )

                assert first["operation"] is not None
                assert first["state"] == "failed"
                assert first["failure"]["code"] == "DISPATCH_PREREQUISITE_FAILED"
                assert first["failure"]["operation"] == first["operation"]
                assert "sensitive" not in json.dumps(first)
                assert replayed == first
        finally:
            application.close()

    asyncio.run(scenario())


def test_in_process_transport_without_back_channel_fails_closed(tmp_path: Path) -> None:
    async def scenario() -> None:
        database = tmp_path / "mcp-sampling.sqlite3"
        application, transport = _application(database)

        async def sample(_context, params):
            request_id = params.metadata["aar.model-request.v1"]["provider_request_id"]
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text="QUALIFIED"),
                model="gpt-5.6-luna",
                stopReason="endTurn",
                _meta={
                    "aar.model-receipt.v1": {
                        "schema_version": "aar.model-receipt.v1",
                        "provider_request_id": request_id,
                        "provider": "openai-codex",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "max",
                        "finish_reason": "stop",
                        "input_tokens": 9,
                        "output_tokens": 1,
                        "cache_read_tokens": None,
                        "cache_write_tokens": None,
                        "reasoning_tokens": None,
                        "total_tokens": 10,
                        "retry_count": 0,
                        "fallback_chain": [],
                    }
                },
            )

        try:
            async with Client(
                application.server,
                sampling_callback=sample,
                sampling_capabilities=SamplingCapability(),
            ) as client:
                capabilities = await _capabilities(client)
                result = _structured(
                    await asyncio.wait_for(
                        client.call_tool(
                            "aar_rlm_execute",
                            {
                                "context": _rlm_context(capabilities, "sampling-route"),
                                "query": "Return exactly QUALIFIED.",
                                "strategy": "baseline",
                                "max_steps": 1,
                            },
                        ),
                        timeout=2,
                    )
                )
                assert result["operation"] is not None
                assert result["state"] == "indeterminate"
                assert result["failure"] is None
                assert transport.observed_error is not None
                assert transport.observed_error[0] == "NoBackChannelError"
                operation_id = result["operation"]["value"]
                deadline = asyncio.get_running_loop().time() + 1.0
                model_state = None
                while True:
                    with sqlite3.connect(database) as connection:
                        operation_state = connection.execute(
                            "SELECT state FROM operations WHERE operation_id = ?",
                            (operation_id,),
                        ).fetchone()
                        model_state = connection.execute(
                            "SELECT state FROM model_executions WHERE operation_id = ?",
                            (operation_id,),
                        ).fetchone()
                    if model_state == ("receipt_lookup_unavailable_quarantined",):
                        break
                    assert model_state == ("provider_call_indeterminate",)
                    if asyncio.get_running_loop().time() >= deadline:
                        raise AssertionError("model uncertainty did not reach quarantine")
                    await asyncio.sleep(0.01)
                assert operation_state == ("indeterminate",)
        finally:
            application.close()

    asyncio.run(scenario())


def test_stdio_mcp_sampling_route_has_a_bidirectional_back_channel(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def sample(_context, params):
            request_id = params.metadata["aar.model-request.v1"]["provider_request_id"]
            return CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text="QUALIFIED"),
                model="gpt-5.6-luna",
                stopReason="endTurn",
                _meta={
                    "aar.model-receipt.v1": {
                        "schema_version": "aar.model-receipt.v1",
                        "provider_request_id": request_id,
                        "provider": "openai-codex",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "max",
                        "finish_reason": "stop",
                        "input_tokens": 9,
                        "output_tokens": 1,
                        "cache_read_tokens": None,
                        "cache_write_tokens": None,
                        "reasoning_tokens": None,
                        "total_tokens": 10,
                        "retry_count": 0,
                        "fallback_chain": [],
                    }
                },
            )

        parameters = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "aar.mcp.server",
                "--database",
                str(tmp_path / "mcp-sampling-stdio.sqlite3"),
                "--programmable-backend",
                "plain",
                "--hermes-mcp-sampling-luna-max",
            ],
            cwd=ROOT,
        )
        async with Client(
            stdio_client(parameters),
            mode="legacy",
            sampling_callback=sample,
            sampling_capabilities=SamplingCapability(),
        ) as client:
            capabilities = await _capabilities(client)
            result = _structured(
                await asyncio.wait_for(
                    client.call_tool(
                        "aar_rlm_execute",
                        {
                            "context": _rlm_context(
                                capabilities,
                                "sampling-stdio",
                                now_ms=capabilities["server_now_unix_ms"],
                                output_tokens=8_192,
                            ),
                            "query": "Return exactly QUALIFIED.",
                            "strategy": "baseline",
                            "max_steps": 1,
                        },
                    ),
                    timeout=5,
                )
            )
            assert result["failure"] is None
            assert result["state"] == "succeeded"

    asyncio.run(scenario())


def test_mcp_sampling_route_rejects_client_without_sampling_capability(tmp_path: Path) -> None:
    async def scenario() -> None:
        application, _transport = _application(tmp_path / "no-sampling-capability.sqlite3")
        try:
            async with Client(application.server) as client:
                capabilities = await _capabilities(client)
                result = _structured(
                    await client.call_tool(
                        "aar_rlm_execute",
                        {
                            "context": _rlm_context(capabilities, "no-sampling-capability"),
                            "query": "must not send",
                            "strategy": "baseline",
                            "max_steps": 1,
                        },
                    )
                )
                assert result["operation"] is None
                assert result["failure"]["message"] == (
                    "MCP client did not authorize sampling"
                )
        finally:
            application.close()

    asyncio.run(scenario())


def test_operation_scoped_sampling_route_rejects_start_only(tmp_path: Path) -> None:
    async def scenario() -> None:
        application, _transport = _application(tmp_path / "sampling-start-only.sqlite3")

        async def unused_sample(_context, _params):
            raise AssertionError("start_only must fail before sampling")

        try:
            async with Client(
                application.server,
                sampling_callback=unused_sample,
                sampling_capabilities=SamplingCapability(),
            ) as client:
                capabilities = await _capabilities(client)
                result = _structured(
                    await client.call_tool(
                        "aar_rlm_execute",
                        {
                            "context": _rlm_context(capabilities, "sampling-start-only"),
                            "query": "must not queue without its session",
                            "strategy": "baseline",
                            "max_steps": 1,
                            "start_only": True,
                        },
                    )
                )
                assert result["operation"] is None
                assert result["failure"]["message"] == (
                    "start_only is unavailable for an operation-scoped MCP sampling route"
                )
        finally:
            application.close()

    asyncio.run(scenario())
