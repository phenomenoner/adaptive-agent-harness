from __future__ import annotations

import asyncio
import base64
import json
import socket
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.auth.provider import AccessToken
from starlette.testclient import TestClient

from aar.mcp.public_auth import PublicIdentity
from aar.mcp.public_server import (
    PublicServerSettings,
    build_public_server,
    create_public_asgi_app,
)
from aar.mcp.public_server import main as public_main

EXPECTED_PUBLIC_TOOLS = (
    "aar_public_capabilities",
    "aar_workspace_open",
    "aar_workspace_update",
    "aar_workspace_inspect",
    "aar_operation_status",
    "aar_rlm_start",
    "aar_rlm_claim_model_call",
    "aar_rlm_commit_model_call",
    "aar_rlm_status",
    "aar_rlm_cancel",
    "aar_artifact_resolve",
)


class _AcceptAllVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=["aar:rlm", "aar:workspace"],
            subject="test-subject",
        )


class _MissingScopeVerifier:
    async def verify_token(self, token: str) -> AccessToken | None:
        if token != "valid-token":
            return None
        return AccessToken(
            token=token,
            client_id="test-client",
            scopes=[],
            subject="test-subject",
        )


@dataclass
class _MutableIdentityProvider:
    identity: PublicIdentity

    def current_identity(self) -> PublicIdentity:
        return self.identity


def _identity(value: str) -> PublicIdentity:
    return PublicIdentity(
        tenant_key=value * 32,
        principal_id=f"principal-{value}",
        session_id=f"session-{value}",
        scopes=("aar:rlm", "aar:workspace"),
    )


def _settings(tmp_path: Path, *, port: int = 8765) -> PublicServerSettings:
    return PublicServerSettings(
        data_root=tmp_path / "public-data",
        issuer_url="http://issuer.example",
        resource_url=f"http://127.0.0.1:{port}/mcp",
        audience=f"http://127.0.0.1:{port}/mcp",
        jwks_url="http://issuer.example/jwks",
        documentation_url="http://docs.example/public-plugin",
        bind_host="127.0.0.1",
        bind_port=port,
        allow_insecure_dev=True,
    )


def _structured(result) -> dict[str, Any]:
    assert not result.is_error
    assert result.structured_content is not None
    assert json.loads(result.content[0].text) == result.structured_content
    return result.structured_content


def _artifact_resolve_args(reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "artifact_id": reference["artifact"]["value"],
        "digest": reference["digest"],
        "media_type": reference["media_type"],
        "size_bytes": reference["size_bytes"],
        "created_by_operation_id": reference["created_by"]["value"],
        "redacted": reference["redacted"],
        "max_bytes": reference["size_bytes"],
    }


def _unused_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def _with_streamable_http_client(application, settings, scenario) -> None:
    app = create_public_asgi_app(application, settings)
    uvicorn_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=settings.bind_host,
            port=settings.bind_port,
            log_level="critical",
            access_log=False,
        )
    )
    server_task = asyncio.create_task(uvicorn_server.serve())
    try:
        for _ in range(500):
            if uvicorn_server.started:
                break
            if server_task.done():
                await server_task
            await asyncio.sleep(0.01)
        else:
            raise RuntimeError("public MCP HTTP server did not start")

        async with (
            httpx2.AsyncClient(headers={"Authorization": "Bearer valid-token"}) as http_client,
            Client(
                streamable_http_client(
                    settings.resource_url,
                    http_client=http_client,
                    terminate_on_close=False,
                ),
                mode="auto",
            ) as client,
        ):
            await scenario(client)
    finally:
        uvicorn_server.should_exit = True
        await asyncio.wait_for(server_task, timeout=5)


def test_public_settings_require_https_and_exact_mcp_path(tmp_path: Path) -> None:
    try:
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="http://issuer.example",
            resource_url="https://mcp.example/mcp",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
        )
    except ValueError as error:
        assert "HTTPS" in str(error)
    else:  # pragma: no cover
        raise AssertionError("insecure production issuer must fail closed")

    try:
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="https://issuer.example",
            resource_url="https://mcp.example",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
        )
    except ValueError as error:
        assert "endpoint path" in str(error)
    else:  # pragma: no cover
        raise AssertionError("resource URL without MCP path must fail closed")

    with pytest.raises(ValueError, match="audience must exactly match"):
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="https://issuer.example",
            resource_url="https://mcp.example/mcp",
            audience="https://different.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
        )

    with pytest.raises(ValueError, match="absolute URL"):
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="https://operator:secret@issuer.example",
            resource_url="https://mcp.example/mcp",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
        )

    with pytest.raises(ValueError, match="aar:rlm and aar:workspace"):
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="https://issuer.example",
            resource_url="https://mcp.example/mcp",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
            required_scopes=("aar:workspace",),
        )

    with pytest.raises(ValueError, match="between 1 and 16384"):
        PublicServerSettings(
            data_root=tmp_path,
            issuer_url="https://issuer.example",
            resource_url="https://mcp.example/mcp",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            documentation_url="https://docs.example/plugin",
            max_rlm_query_bytes=16_385,
        )


def test_public_check_config_reports_challenge_without_disclosing_token(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    environment = {
        "AAR_PUBLIC_DATA_ROOT": str(tmp_path / "data"),
        "AAR_PUBLIC_ISSUER_URL": "https://issuer.example",
        "AAR_PUBLIC_RESOURCE_URL": "https://mcp.example/mcp",
        "AAR_PUBLIC_AUDIENCE": "https://mcp.example/mcp",
        "AAR_PUBLIC_JWKS_URL": "https://issuer.example/jwks",
        "AAR_PUBLIC_DOCUMENTATION_URL": "https://docs.example/plugin",
        "AAR_PUBLIC_OPENAI_CHALLENGE_TOKEN": "do-not-print-this-token",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert public_main(["--check-config"]) == 0
    output = capsys.readouterr().out
    assert "do-not-print-this-token" not in output
    assert json.loads(output)["openai_challenge_configured"] is True


def test_public_asgi_surface_requires_bearer_auth(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    identities = _MutableIdentityProvider(_identity("a"))
    application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )
    try:
        app = create_public_asgi_app(application, settings)
        with TestClient(app) as client:
            metadata = client.get("/.well-known/oauth-protected-resource/mcp")
            assert metadata.status_code == 200
            assert metadata.json() == {
                "resource": settings.resource_url,
                "authorization_servers": [f"{settings.issuer_url}/"],
                "scopes_supported": ["aar:rlm", "aar:workspace"],
                "bearer_methods_supported": ["header"],
                "resource_name": "Adaptive Agent Runtime",
                "resource_documentation": settings.documentation_url,
            }
            response = client.post(
                settings.mcp_path,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Host": "127.0.0.1:8765"},
            )
            assert response.status_code == 401
            challenge = response.headers["www-authenticate"]
            assert challenge.startswith("Bearer")
            assert (
                'resource_metadata="http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp"'
            ) in challenge
    finally:
        application.close()


def test_public_asgi_rejects_authenticated_token_without_required_scope(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    application = build_public_server(
        settings,
        token_verifier=_MissingScopeVerifier(),
        identity_provider=_MutableIdentityProvider(_identity("a")),
    )
    try:
        with TestClient(create_public_asgi_app(application, settings)) as client:
            response = client.post(
                settings.mcp_path,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={
                    "Authorization": "Bearer valid-token",
                    "Host": "127.0.0.1:8765",
                },
            )
            assert response.status_code == 403
            challenge = response.headers["www-authenticate"]
            assert 'error="insufficient_scope"' in challenge
            assert "aar:rlm" in challenge
    finally:
        application.close()


def test_public_asgi_domain_challenge_is_exact_and_opt_in(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    identities = _MutableIdentityProvider(_identity("a"))
    application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )
    try:
        with TestClient(create_public_asgi_app(application, settings)) as client:
            absent = client.get("/.well-known/openai-apps-challenge")
            assert absent.status_code == 404
            assert absent.text == "not configured"
            assert client.get("/healthz").text == "ok"
    finally:
        application.close()

    configured = replace(settings, openai_challenge_token="review-token-123")
    application = build_public_server(
        configured,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )
    try:
        with TestClient(create_public_asgi_app(application, configured)) as client:
            response = client.get("/.well-known/openai-apps-challenge")
            assert response.status_code == 200
            assert response.content == b"review-token-123"
    finally:
        application.close()


def test_authenticated_streamable_http_persists_across_server_restart(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path, port=_unused_loopback_port())
    identities = _MutableIdentityProvider(_identity("a"))
    bound_rlm: dict[str, Any] = {}

    first_application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )

    async def write_scenario(client: Client) -> None:
        capabilities = _structured(await client.call_tool("aar_public_capabilities"))
        assert tuple(capabilities["tool_names"]) == EXPECTED_PUBLIC_TOOLS
        opened = _structured(
            await client.call_tool("aar_workspace_open", {"workspace_id": "http-restart"})
        )
        updated = _structured(
            await client.call_tool(
                "aar_workspace_update",
                {
                    "workspace_id": "http-restart",
                    "expected_generation": opened["handle"]["generation"],
                    "expected_revision": opened["handle"]["revision"],
                    "action": "set",
                    "key": "transport",
                    "value": "streamable-http",
                    "idempotency_key": "http-restart-write-v1",
                },
            )
        )
        assert updated["state"] == "succeeded"
        started = _structured(
            await client.call_tool(
                "aar_rlm_start",
                {
                    "query": "Return exactly restart-safe.",
                    "idempotency_key": "http-rlm-start-v1",
                    "executor_kind": "codex-host",
                    "requested_model": "host-current",
                    "strategy": "single_call",
                    "max_model_calls": 1,
                    "max_output_tokens_per_call": 64,
                    "max_result_bytes_per_call": 1024,
                },
            )
        )
        pending = started["job"]["pending_call"]
        claimed = _structured(
            await client.call_tool(
                "aar_rlm_claim_model_call",
                {
                    "job_id": started["job"]["job_id"],
                    "call_id": pending["call_id"],
                    "expected_revision": started["job"]["revision"],
                    "call_spec_digest": pending["spec_digest"],
                    "idempotency_key": "http-rlm-claim-v1",
                },
            )
        )
        assert claimed["job"]["phase"] == "awaiting_caller_result"
        bound_rlm.update(
            job_id=claimed["job"]["job_id"],
            ticket=claimed["job"]["active_ticket"],
        )

    try:
        asyncio.run(_with_streamable_http_client(first_application, settings, write_scenario))
    finally:
        first_application.close()

    second_application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )

    async def read_scenario(client: Client) -> None:
        inspected = _structured(
            await client.call_tool("aar_workspace_inspect", {"workspace_id": "http-restart"})
        )
        assert inspected["values"] == {"transport": "streamable-http"}
        assert inspected["handle"]["revision"] == 1
        status = _structured(
            await client.call_tool("aar_rlm_status", {"job_id": bound_rlm["job_id"]})
        )
        assert status["job"]["phase"] == "awaiting_caller_result"
        assert status["job"]["active_ticket"] == bound_rlm["ticket"]
        committed = _structured(
            await client.call_tool(
                "aar_rlm_commit_model_call",
                {
                    "job_id": status["job"]["job_id"],
                    "call_id": status["job"]["active_ticket"]["call_id"],
                    "expected_revision": status["job"]["revision"],
                    "ticket_digest": status["job"]["active_ticket"]["ticket_digest"],
                    "outcome": "succeeded",
                    "output_text": "restart-safe",
                    "effective_model": "host-current",
                    "idempotency_key": "http-rlm-commit-v1",
                },
            )
        )
        assert committed["job"]["phase"] == "succeeded"
        assert committed["job"]["result"]["answer"] == "restart-safe"

    try:
        asyncio.run(_with_streamable_http_client(second_application, settings, read_scenario))
    finally:
        second_application.close()


def test_public_workspace_quotas_fail_before_mutation(tmp_path: Path) -> None:
    settings = replace(
        _settings(tmp_path),
        max_workspaces_per_tenant=1,
        max_keys_per_workspace=1,
        max_value_bytes=8,
        max_workspace_bytes=32,
    )
    identities = _MutableIdentityProvider(_identity("a"))
    application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )

    async def scenario() -> None:
        async with Client(application.server, mode="auto") as client:
            first = _structured(
                await client.call_tool("aar_workspace_open", {"workspace_id": "quota"})
            )
            assert first["failure"] is None
            second = _structured(
                await client.call_tool("aar_workspace_open", {"workspace_id": "overflow"})
            )
            assert second["failure"]["code"] == "PUBLIC_QUOTA_EXCEEDED"

            base = {
                "workspace_id": "quota",
                "expected_generation": 1,
                "expected_revision": 0,
                "action": "set",
                "key": "one",
                "value": "one",
                "idempotency_key": "quota-first-v1",
            }
            updated = _structured(await client.call_tool("aar_workspace_update", base))
            assert updated["state"] == "succeeded"

            key_overflow = _structured(
                await client.call_tool(
                    "aar_workspace_update",
                    {
                        **base,
                        "expected_revision": 1,
                        "key": "two",
                        "value": "two",
                        "idempotency_key": "quota-key-overflow-v1",
                    },
                )
            )
            assert key_overflow["failure"]["code"] == "PUBLIC_QUOTA_EXCEEDED"

            value_overflow = _structured(
                await client.call_tool(
                    "aar_workspace_update",
                    {
                        **base,
                        "expected_revision": 1,
                        "value": "123456789",
                        "idempotency_key": "quota-value-overflow-v1",
                    },
                )
            )
            assert value_overflow["failure"]["code"] == "PUBLIC_QUOTA_EXCEEDED"

            inspected = _structured(
                await client.call_tool("aar_workspace_inspect", {"workspace_id": "quota"})
            )
            assert inspected["handle"]["revision"] == 1
            assert inspected["values"] == {"one": "one"}

    try:
        asyncio.run(scenario())
    finally:
        application.close()


def test_public_workspace_flow_is_tenant_scoped_and_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    identities = _MutableIdentityProvider(_identity("a"))
    application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )

    async def scenario() -> None:
        async with Client(application.server, mode="auto") as client:
            tools = await client.list_tools()
            assert tuple(tool.name for tool in tools.tools) == EXPECTED_PUBLIC_TOOLS
            annotations = {
                tool.name: tool.annotations.model_dump(mode="json", by_alias=True)
                for tool in tools.tools
            }
            for name in EXPECTED_PUBLIC_TOOLS:
                assert annotations[name]["idempotentHint"] is True
                assert annotations[name]["openWorldHint"] is False
            assert annotations["aar_workspace_open"]["readOnlyHint"] is False
            assert annotations["aar_workspace_open"]["destructiveHint"] is False
            assert annotations["aar_rlm_start"]["readOnlyHint"] is False
            assert annotations["aar_rlm_start"]["destructiveHint"] is False
            assert annotations["aar_workspace_update"]["readOnlyHint"] is False
            assert annotations["aar_workspace_update"]["destructiveHint"] is True
            for name in (
                "aar_rlm_claim_model_call",
                "aar_rlm_commit_model_call",
                "aar_rlm_cancel",
            ):
                assert annotations[name]["readOnlyHint"] is False
                assert annotations[name]["destructiveHint"] is True
            for name in (
                "aar_public_capabilities",
                "aar_workspace_inspect",
                "aar_operation_status",
                "aar_rlm_status",
                "aar_artifact_resolve",
            ):
                assert annotations[name]["readOnlyHint"] is True
                assert annotations[name]["destructiveHint"] is False
            capabilities = _structured(await client.call_tool("aar_public_capabilities"))
            assert tuple(capabilities["tool_names"]) == EXPECTED_PUBLIC_TOOLS
            assert capabilities["tool_surface_version"] == "aar.public-tools.v2"
            assert capabilities["limits"] == {
                "active_tenants": 128,
                "artifact_bytes": 1_048_576,
                "keys_per_workspace": 256,
                "request_body_bytes": 1_048_576,
                "rlm_jobs_per_tenant": 256,
                "rlm_model_calls": 8,
                "rlm_output_tokens_per_call": 32_768,
                "rlm_query_bytes": 16_384,
                "rlm_result_bytes_per_call": 262_144,
                "value_bytes": 65_536,
                "workspace_bytes": 262_144,
                "workspaces_per_tenant": 64,
            }
            assert capabilities["unsupported_capabilities"] == [
                "effect.execute",
                "external.delivery",
                "provider.credentials",
                "rlm.service_managed_provider",
                "workspace.program.execute",
            ]

            opened = _structured(
                await client.call_tool("aar_workspace_open", {"workspace_id": "research"})
            )
            assert opened["handle"] == {
                "workspace_id": "research",
                "generation": 1,
                "revision": 0,
            }
            empty = _structured(
                await client.call_tool("aar_workspace_inspect", {"workspace_id": "research"})
            )
            assert empty["values"] == {}

            update_args = {
                "workspace_id": "research",
                "expected_generation": 1,
                "expected_revision": 0,
                "action": "set",
                "key": "finding",
                "value": "bounded",
                "idempotency_key": "update-finding-v1",
            }
            updated = _structured(await client.call_tool("aar_workspace_update", update_args))
            assert updated["state"] == "succeeded"
            assert updated["result"]["revision_after"] == 1
            replay = _structured(await client.call_tool("aar_workspace_update", update_args))
            assert replay["operation_id"] == updated["operation_id"]
            assert replay["result"] == updated["result"]

            inspected = _structured(
                await client.call_tool("aar_workspace_inspect", {"workspace_id": "research"})
            )
            assert inspected["handle"]["revision"] == 1
            assert inspected["values"] == {"finding": "bounded"}

            status = _structured(
                await client.call_tool(
                    "aar_operation_status", {"operation_id": updated["operation_id"]}
                )
            )
            assert status["state"] == "succeeded"
            reference = status["artifacts"][0]
            artifact = _structured(
                await client.call_tool(
                    "aar_artifact_resolve",
                    _artifact_resolve_args(reference),
                )
            )
            decoded = json.loads(base64.b64decode(artifact["content_base64"]))
            assert decoded["result"]["revision_after"] == 1

            tenant_a_rlm = _structured(
                await client.call_tool(
                    "aar_rlm_start",
                    {
                        "query": "tenant-private model work",
                        "idempotency_key": "tenant-a-rlm",
                        "executor_kind": "codex-host",
                        "requested_model": "host-current",
                    },
                )
            )
            tenant_a_job_id = tenant_a_rlm["job"]["job_id"]

            identities.identity = _identity("b")
            tenant_b = _structured(
                await client.call_tool("aar_workspace_open", {"workspace_id": "research"})
            )
            assert tenant_b["handle"]["revision"] == 0
            tenant_b_values = _structured(
                await client.call_tool("aar_workspace_inspect", {"workspace_id": "research"})
            )
            assert tenant_b_values["values"] == {}
            cross_tenant_rlm = _structured(
                await client.call_tool(
                    "aar_rlm_status", {"job_id": tenant_a_job_id}
                )
            )
            assert cross_tenant_rlm["job"] is None
            assert cross_tenant_rlm["failure"]["code"] == "RLM_JOB_NOT_FOUND"

    try:
        asyncio.run(scenario())
        assert application.tenant_pool.tenant_database_path("a" * 32).is_file()
        assert application.tenant_pool.tenant_database_path("b" * 32).is_file()
        assert application.tenant_pool.tenant_database_path(
            "a" * 32
        ) != application.tenant_pool.tenant_database_path("b" * 32)
    finally:
        application.close()


def test_public_artifact_resolve_requires_exact_receipt_reference(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    identities = _MutableIdentityProvider(_identity("a"))
    application = build_public_server(
        settings,
        token_verifier=_AcceptAllVerifier(),
        identity_provider=identities,
    )

    async def scenario() -> None:
        async with Client(application.server, mode="auto") as client:
            await client.call_tool("aar_workspace_open", {"workspace_id": "artifacts"})
            first = _structured(
                await client.call_tool(
                    "aar_workspace_update",
                    {
                        "workspace_id": "artifacts",
                        "expected_generation": 1,
                        "expected_revision": 0,
                        "action": "set",
                        "key": "a",
                        "value": "a",
                        "idempotency_key": "artifact-update-a",
                    },
                )
            )
            second = _structured(
                await client.call_tool(
                    "aar_workspace_update",
                    {
                        "workspace_id": "artifacts",
                        "expected_generation": 1,
                        "expected_revision": 1,
                        "action": "set",
                        "key": "b",
                        "value": "b",
                        "idempotency_key": "artifact-update-b",
                    },
                )
            )
            first_status = _structured(
                await client.call_tool(
                    "aar_operation_status", {"operation_id": first["operation_id"]}
                )
            )
            second_status = _structured(
                await client.call_tool(
                    "aar_operation_status", {"operation_id": second["operation_id"]}
                )
            )
            first_reference = first_status["artifacts"][0]
            second_reference = second_status["artifacts"][0]
            assert first_reference["size_bytes"] == second_reference["size_bytes"]

            exact = _structured(
                await client.call_tool(
                    "aar_artifact_resolve",
                    _artifact_resolve_args(first_reference),
                )
            )
            assert exact["artifact_id"] == first_reference["artifact"]["value"]
            assert len(base64.b64decode(exact["content_base64"])) == first_reference["size_bytes"]

            mismatches = {
                "artifact_id": f"artifact-{'0' * 32}",
                "digest": second_reference["digest"],
                "media_type": "application/json",
                "size_bytes": first_reference["size_bytes"] + 1,
                "created_by_operation_id": second_reference["created_by"]["value"],
                "redacted": True,
            }
            for field, value in mismatches.items():
                request = _artifact_resolve_args(first_reference)
                request[field] = value
                if field == "redacted":
                    request["allow_redacted"] = True
                denied = _structured(await client.call_tool("aar_artifact_resolve", request))
                assert denied["failure"]["code"] == "AUTHORITY_DENIED", field
                assert denied["content_base64"] is None, field

            bounded_request = _artifact_resolve_args(first_reference)
            bounded_request["max_bytes"] = first_reference["size_bytes"] - 1
            bounded = _structured(await client.call_tool("aar_artifact_resolve", bounded_request))
            assert bounded["failure"]["code"] == "INVALID_ARGUMENT"
            assert bounded["content_base64"] is None

            identities.identity = _identity("b")
            cross_tenant = _structured(
                await client.call_tool(
                    "aar_artifact_resolve",
                    _artifact_resolve_args(first_reference),
                )
            )
            assert cross_tenant["failure"] is not None
            assert cross_tenant["content_base64"] is None

    try:
        asyncio.run(scenario())
    finally:
        application.close()
