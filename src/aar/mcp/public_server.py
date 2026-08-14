"""Authenticated Streamable HTTP MCP surface for the public AAR workspace product."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import uvicorn
from mcp.server import MCPServer
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.routes import (
    build_resource_metadata_url,
    create_protected_resource_routes,
)
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field, JsonValue
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.models import IdentityValue
from aar.mcp.public_auth import (
    OAuthIdentityProvider,
    OidcJwtTokenVerifier,
    PublicAuthenticationError,
    PublicIdentity,
    PublicIdentityProvider,
)
from aar.mcp.public_models import (
    PublicArtifactResult,
    PublicCapabilitiesResult,
    PublicOperationResult,
    PublicWorkspaceHandle,
    PublicWorkspaceHandleResult,
    PublicWorkspaceInspectResult,
)
from aar.mcp.public_rlm import (
    PublicRlmCapacityExceeded,
    PublicRlmConflict,
    PublicRlmInvalidTransition,
    PublicRlmNotFound,
)
from aar.mcp.public_rlm_models import (
    PublicRlmClaimRequest,
    PublicRlmCommitRequest,
    PublicRlmJobSpec,
    PublicRlmToolResult,
)
from aar.mcp.public_runtime import TenantCapacityExceeded, TenantRuntime, TenantRuntimePool
from aar.mcp.server import _failure, _receipt_artifact
from aar.runtime.models import WorkspaceExecuteSpec, WorkspaceHandle
from aar.runtime.reference_host import WorkspaceBindingDenied
from aar.runtime.workspace import WorkspaceNotFound
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    Budget,
    Digest,
    FailureCategory,
    FailureEnvelope,
    Grant,
    HostRef,
    LaneRef,
    MediaType,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    RequestEnvelope,
    WorkspaceRef,
)
from aar.versions import PACKAGE_VERSION

PUBLIC_SERVER_NAME = "aar-public-runtime"
PUBLIC_TOOL_SURFACE_VERSION = "aar.public-tools.v2"
PUBLIC_REQUIRED_SCOPES = ("aar:rlm", "aar:workspace")
PUBLIC_AUTHORITY_STATEMENT = (
    "The OAuth-authenticated host owns identity, authorization, model selection, and model "
    "execution; AAR stores bounded tenant state and receipt-bound RLM continuation."
)
PUBLIC_UNSUPPORTED_CAPABILITIES = (
    "effect.execute",
    "external.delivery",
    "provider.credentials",
    "rlm.service_managed_provider",
    "workspace.program.execute",
)
PUBLIC_SERVER_INSTRUCTIONS = (
    "Use this plugin for durable structured workspace state or caller-delegated RLM coordination. "
    "For an RLM job, choose one model route and reasoning effort at start, claim each returned "
    "call specification before model spend, execute it through the host with tools disabled, and "
    "commit the bounded result before continuing. Every call in that job inherits the start "
    "route; start another job to use a different route. Never claim that AAR "
    "called or verified a provider. Reuse an idempotency key only for identical input. This public "
    "surface does not execute Python, accept provider credentials, perform external effects, or "
    "deliver results outside the MCP response."
)
READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
NON_DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
BoundedWallTime = Annotated[int, Field(ge=1, le=60_000)]
BoundedArtifactBytes = Annotated[int, Field(ge=1, le=1_048_576)]
BoundedRlmQuery = Annotated[str, Field(min_length=1, max_length=16_384, strict=True)]
BoundedRlmRoute = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
BoundedRlmEffort = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
BoundedRlmExecutor = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
BoundedRlmOutput = Annotated[
    str, Field(min_length=1, max_length=1_048_576, strict=True)
]
BoundedRlmFailureCode = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
BoundedRlmFailureMessage = Annotated[str, Field(min_length=1, max_length=512, strict=True)]
BoundedHostReceiptId = Annotated[str, Field(min_length=1, max_length=256, strict=True)]


class PublicQuotaExceeded(RuntimeError):
    """A tenant request would exceed the curated public storage boundary."""


@dataclass(frozen=True, slots=True)
class PublicServerSettings:
    data_root: Path
    issuer_url: str
    resource_url: str
    audience: str
    jwks_url: str
    documentation_url: str
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000
    required_scopes: tuple[str, ...] = PUBLIC_REQUIRED_SCOPES
    allowed_origins: tuple[str, ...] = ()
    openai_challenge_token: str | None = None
    max_active_tenants: int = 128
    max_request_body_size: int = 1_048_576
    max_artifact_bytes: int = 1_048_576
    max_workspaces_per_tenant: int = 64
    max_keys_per_workspace: int = 256
    max_value_bytes: int = 65_536
    max_workspace_bytes: int = 262_144
    max_rlm_jobs_per_tenant: int = 256
    max_rlm_query_bytes: int = 16_384
    max_rlm_model_calls: int = 8
    max_rlm_output_tokens_per_call: int = 32_768
    max_rlm_result_bytes_per_call: int = 262_144
    allow_insecure_dev: bool = False

    def __post_init__(self) -> None:
        if not self.audience:
            raise ValueError("OAuth audience is required")
        if not self.required_scopes:
            raise ValueError("at least one OAuth scope is required")
        missing_scopes = set(PUBLIC_REQUIRED_SCOPES).difference(self.required_scopes)
        if missing_scopes:
            raise ValueError(
                "public runtime requires the aar:rlm and aar:workspace OAuth scopes"
            )
        if self.bind_port < 1 or self.bind_port > 65_535:
            raise ValueError("bind port must be between 1 and 65535")
        if self.max_request_body_size < 1 or self.max_request_body_size > 4_194_304:
            raise ValueError("max request body size must be between 1 and 4194304 bytes")
        if self.max_artifact_bytes < 1 or self.max_artifact_bytes > 1_048_576:
            raise ValueError("max artifact bytes must be between 1 and 1048576")
        if self.max_workspaces_per_tenant < 1 or self.max_workspaces_per_tenant > 10_000:
            raise ValueError("max workspaces per tenant must be between 1 and 10000")
        if self.max_keys_per_workspace < 1 or self.max_keys_per_workspace > 10_000:
            raise ValueError("max keys per workspace must be between 1 and 10000")
        if self.max_value_bytes < 1 or self.max_value_bytes > 1_048_576:
            raise ValueError("max value bytes must be between 1 and 1048576")
        if self.max_workspace_bytes < self.max_value_bytes or (
            self.max_workspace_bytes > 4_194_304
        ):
            raise ValueError(
                "max workspace bytes must cover max value bytes and not exceed 4194304"
            )
        if self.max_rlm_jobs_per_tenant < 1 or self.max_rlm_jobs_per_tenant > 100_000:
            raise ValueError("max RLM jobs per tenant must be between 1 and 100000")
        if self.max_rlm_query_bytes < 1 or self.max_rlm_query_bytes > 16_384:
            raise ValueError("max RLM query bytes must be between 1 and 16384")
        if self.max_rlm_model_calls < 1 or self.max_rlm_model_calls > 8:
            raise ValueError("max RLM model calls must be between 1 and 8")
        if (
            self.max_rlm_output_tokens_per_call < 1
            or self.max_rlm_output_tokens_per_call > 32_768
        ):
            raise ValueError("max RLM output tokens per call must be between 1 and 32768")
        if (
            self.max_rlm_result_bytes_per_call < 1
            or self.max_rlm_result_bytes_per_call > 262_144
        ):
            raise ValueError("max RLM result bytes per call must be between 1 and 262144")
        if self.openai_challenge_token is not None and (
            not self.openai_challenge_token
            or len(self.openai_challenge_token) > 512
            or any(character.isspace() for character in self.openai_challenge_token)
        ):
            raise ValueError(
                "OpenAI domain challenge token must contain 1 to 512 non-whitespace characters"
            )
        for label, value in (
            ("issuer URL", self.issuer_url),
            ("resource URL", self.resource_url),
            ("JWKS URL", self.jwks_url),
            ("documentation URL", self.documentation_url),
        ):
            parsed = urlsplit(value)
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(
                    f"{label} must be an absolute URL without credentials, query, or fragment"
                )
            if not self.allow_insecure_dev and parsed.scheme != "https":
                raise ValueError(f"{label} must use HTTPS outside explicit development mode")
            if self.allow_insecure_dev and parsed.scheme not in {"http", "https"}:
                raise ValueError(f"{label} must use HTTP or HTTPS")
        resource = urlsplit(self.resource_url)
        if resource.path in {"", "/"}:
            raise ValueError("resource URL must include the public MCP endpoint path")
        if self.audience != self.resource_url:
            raise ValueError("OAuth audience must exactly match the public MCP resource URL")

    @property
    def mcp_path(self) -> str:
        return urlsplit(self.resource_url).path

    @property
    def allowed_hosts(self) -> tuple[str, ...]:
        resource = urlsplit(self.resource_url)
        assert resource.netloc
        local = (
            self.bind_host if self.bind_port in {80, 443} else f"{self.bind_host}:{self.bind_port}"
        )
        return tuple(dict.fromkeys((resource.netloc, local)))

    @classmethod
    def from_env(cls) -> PublicServerSettings:
        def required(name: str) -> str:
            value = os.environ.get(name)
            if value is None or not value.strip():
                raise ValueError(f"{name} is required")
            return value.strip()

        return cls(
            data_root=Path(required("AAR_PUBLIC_DATA_ROOT")),
            issuer_url=required("AAR_PUBLIC_ISSUER_URL"),
            resource_url=required("AAR_PUBLIC_RESOURCE_URL"),
            audience=required("AAR_PUBLIC_AUDIENCE"),
            jwks_url=required("AAR_PUBLIC_JWKS_URL"),
            documentation_url=required("AAR_PUBLIC_DOCUMENTATION_URL"),
            bind_host=os.environ.get("AAR_PUBLIC_BIND_HOST", "127.0.0.1"),
            bind_port=_env_int("AAR_PUBLIC_BIND_PORT", 8000),
            required_scopes=_env_csv("AAR_PUBLIC_REQUIRED_SCOPES", PUBLIC_REQUIRED_SCOPES),
            allowed_origins=_env_csv("AAR_PUBLIC_ALLOWED_ORIGINS", ()),
            openai_challenge_token=_env_optional("AAR_PUBLIC_OPENAI_CHALLENGE_TOKEN"),
            max_active_tenants=_env_int("AAR_PUBLIC_MAX_ACTIVE_TENANTS", 128),
            max_request_body_size=_env_int("AAR_PUBLIC_MAX_REQUEST_BODY_SIZE", 1_048_576),
            max_artifact_bytes=_env_int("AAR_PUBLIC_MAX_ARTIFACT_BYTES", 1_048_576),
            max_workspaces_per_tenant=_env_int("AAR_PUBLIC_MAX_WORKSPACES_PER_TENANT", 64),
            max_keys_per_workspace=_env_int("AAR_PUBLIC_MAX_KEYS_PER_WORKSPACE", 256),
            max_value_bytes=_env_int("AAR_PUBLIC_MAX_VALUE_BYTES", 65_536),
            max_workspace_bytes=_env_int("AAR_PUBLIC_MAX_WORKSPACE_BYTES", 262_144),
            max_rlm_jobs_per_tenant=_env_int("AAR_PUBLIC_MAX_RLM_JOBS_PER_TENANT", 256),
            max_rlm_query_bytes=_env_int("AAR_PUBLIC_MAX_RLM_QUERY_BYTES", 16_384),
            max_rlm_model_calls=_env_int("AAR_PUBLIC_MAX_RLM_MODEL_CALLS", 8),
            max_rlm_output_tokens_per_call=_env_int(
                "AAR_PUBLIC_MAX_RLM_OUTPUT_TOKENS_PER_CALL", 32_768
            ),
            max_rlm_result_bytes_per_call=_env_int(
                "AAR_PUBLIC_MAX_RLM_RESULT_BYTES_PER_CALL", 262_144
            ),
            allow_insecure_dev=_env_bool("AAR_PUBLIC_ALLOW_INSECURE_DEV", False),
        )

    def transport_security(self) -> TransportSecuritySettings:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=list(self.allowed_hosts),
            allowed_origins=list(self.allowed_origins),
        )


@dataclass(slots=True)
class PublicMcpApplication:
    server: MCPServer
    tenant_pool: TenantRuntimePool

    def close(self) -> None:
        self.tenant_pool.close()


def build_public_server(
    settings: PublicServerSettings,
    *,
    token_verifier: TokenVerifier | None = None,
    identity_provider: PublicIdentityProvider | None = None,
) -> PublicMcpApplication:
    verifier = token_verifier or OidcJwtTokenVerifier(
        issuer=settings.issuer_url,
        audience=settings.audience,
        jwks_url=settings.jwks_url,
        required_scopes=settings.required_scopes,
    )
    identities = identity_provider or OAuthIdentityProvider(
        issuer=settings.issuer_url,
        required_scopes=settings.required_scopes,
    )
    tenant_pool = TenantRuntimePool(
        settings.data_root,
        max_active_tenants=settings.max_active_tenants,
        max_rlm_jobs_per_tenant=settings.max_rlm_jobs_per_tenant,
    )
    server = MCPServer(
        PUBLIC_SERVER_NAME,
        title="Adaptive Agent Runtime",
        description=(
            "OAuth-authenticated durable structured workspaces and caller-delegated RLM "
            "coordination for ChatGPT and Codex, isolated by tenant."
        ),
        instructions=PUBLIC_SERVER_INSTRUCTIONS,
        website_url=settings.documentation_url,
        version=PACKAGE_VERSION,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer_url),
            service_documentation_url=AnyHttpUrl(settings.documentation_url),
            required_scopes=list(settings.required_scopes),
            resource_server_url=AnyHttpUrl(settings.resource_url),
        ),
    )

    @contextmanager
    def current() -> Iterator[tuple[PublicIdentity, TenantRuntime]]:
        identity = identities.current_identity()
        with tenant_pool.acquire(identity) as runtime:
            yield identity, runtime

    @server.tool(
        name="aar_public_capabilities",
        description=(
            "Read the exact curated public tool identity, tenant boundary, and unsupported "
            "capabilities before using a durable workspace."
        ),
        annotations=READ_ONLY,
    )
    async def aar_public_capabilities() -> PublicCapabilitiesResult:
        identities.current_identity()
        tools = await server.list_tools()
        core = {
            "server_name": PUBLIC_SERVER_NAME,
            "server_version": PACKAGE_VERSION,
            "tool_surface_version": PUBLIC_TOOL_SURFACE_VERSION,
            "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
        }
        return PublicCapabilitiesResult(
            server_name=PUBLIC_SERVER_NAME,
            package_version=PACKAGE_VERSION,
            tool_surface_version=PUBLIC_TOOL_SURFACE_VERSION,
            tool_surface_digest=canonical_sha256(core),
            tool_names=tuple(tool.name for tool in tools),
            authority_statement=PUBLIC_AUTHORITY_STATEMENT,
            tenant_isolation=(
                "OAuth-subject-derived per-tenant database and runtime ownership; the public "
                "surface exposes no programmable execution or provider credential path"
            ),
            limits={
                "active_tenants": settings.max_active_tenants,
                "artifact_bytes": settings.max_artifact_bytes,
                "keys_per_workspace": settings.max_keys_per_workspace,
                "request_body_bytes": settings.max_request_body_size,
                "rlm_jobs_per_tenant": settings.max_rlm_jobs_per_tenant,
                "rlm_model_calls": settings.max_rlm_model_calls,
                "rlm_output_tokens_per_call": settings.max_rlm_output_tokens_per_call,
                "rlm_query_bytes": settings.max_rlm_query_bytes,
                "rlm_result_bytes_per_call": settings.max_rlm_result_bytes_per_call,
                "value_bytes": settings.max_value_bytes,
                "workspace_bytes": settings.max_workspace_bytes,
                "workspaces_per_tenant": settings.max_workspaces_per_tenant,
            },
            unsupported_capabilities=PUBLIC_UNSUPPORTED_CAPABILITIES,
        )

    @server.tool(
        name="aar_workspace_open",
        description=(
            "Create or reopen one tenant-private named structured workspace and return its current "
            "generation and revision."
        ),
        annotations=NON_DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_workspace_open(workspace_id: IdentityValue) -> PublicWorkspaceHandleResult:
        try:
            with current() as (_identity, runtime):
                workspace = WorkspaceRef(value=workspace_id)
                try:
                    handle = runtime.host.workspace.current_handle(workspace)
                    runtime.host.workspace.assert_session(workspace, runtime.session)
                except WorkspaceNotFound:
                    if runtime.host.workspace.count() >= settings.max_workspaces_per_tenant:
                        raise PublicQuotaExceeded(
                            "tenant workspace count reached the configured limit"
                        ) from None
                    handle = runtime.host.create_workspace(workspace, runtime.session)
                return PublicWorkspaceHandleResult(handle=_public_handle(workspace_id, handle))
        except Exception as error:
            return PublicWorkspaceHandleResult(failure=_public_failure(error))

    @server.tool(
        name="aar_workspace_update",
        description=(
            "Apply one idempotent set, delete, or integer increment to an exact workspace revision "
            "and return its durable operation receipt."
        ),
        annotations=DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_workspace_update(
        workspace_id: IdentityValue,
        expected_generation: Annotated[int, Field(ge=1)],
        expected_revision: Annotated[int, Field(ge=0)],
        action: Literal["set", "delete", "increment"],
        key: IdentityValue,
        idempotency_key: IdentityValue,
        value: JsonValue | None = None,
        max_wall_time_ms: BoundedWallTime = 30_000,
    ) -> PublicOperationResult:
        operation: OperationRef | None = None
        try:
            with current() as (identity, runtime):
                spec = WorkspaceExecuteSpec(
                    workspace=WorkspaceRef(value=workspace_id),
                    expected_generation=expected_generation,
                    expected_revision=expected_revision,
                    action=action,
                    key=key,
                    value=value,
                )
                envelope = _public_envelope(
                    identity,
                    runtime,
                    capability="workspace.execute",
                    idempotency_key=idempotency_key,
                    input_digest=canonical_sha256(spec),
                    wall_time_ms=max_wall_time_ms,
                    workspace=WorkspaceHandle(
                        workspace=spec.workspace,
                        generation=spec.expected_generation,
                        revision=spec.expected_revision,
                    ),
                )
                record = runtime.host.submit_execute(envelope, spec)
                operation = record.operation
                if record.state is OperationState.ACCEPTED:
                    try:
                        _validate_public_update(runtime, spec, settings)
                    except Exception as error:
                        record = runtime.host.registry.fail(
                            operation,
                            _public_failure(error, operation=operation),
                            runtime.host.runtime_generation,
                        )
                        return _public_operation_result(runtime, record)
                record = runtime.host.run_execute(record.operation)
                return _public_operation_result(runtime, record)
        except Exception as error:
            return PublicOperationResult(
                operation_id=None if operation is None else operation.value,
                failure=_public_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_workspace_inspect",
        description=(
            "Read the current tenant-private values and exact revision of a named structured "
            "workspace without changing it."
        ),
        annotations=READ_ONLY,
    )
    async def aar_workspace_inspect(workspace_id: IdentityValue) -> PublicWorkspaceInspectResult:
        try:
            with current() as (_identity, runtime):
                workspace = WorkspaceRef(value=workspace_id)
                runtime.host.workspace.assert_session(workspace, runtime.session)
                handle = runtime.host.workspace.current_handle(workspace)
                snapshot = runtime.host.inspect(handle)
                return PublicWorkspaceInspectResult(
                    handle=_public_handle(workspace_id, handle),
                    values=dict(snapshot.values),
                )
        except Exception as error:
            return PublicWorkspaceInspectResult(failure=_public_failure(error))

    @server.tool(
        name="aar_operation_status",
        description=(
            "Read a durable operation receipt returned to the current authenticated tenant."
        ),
        annotations=READ_ONLY,
    )
    async def aar_operation_status(operation_id: IdentityValue) -> PublicOperationResult:
        operation = OperationRef(value=operation_id)
        try:
            with current() as (_identity, runtime):
                record = runtime.host.status(operation)
                _assert_operation_binding(runtime, record.request_json)
                return _public_operation_result(runtime, record)
        except Exception as error:
            return PublicOperationResult(
                operation_id=operation_id,
                failure=_public_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_rlm_start",
        description=(
            "Create or exactly replay a tenant-private caller-delegated RLM job and return its "
            "first immutable model-call specification. AAR does not execute the model."
        ),
        annotations=NON_DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_rlm_start(
        query: BoundedRlmQuery,
        idempotency_key: IdentityValue,
        executor_kind: BoundedRlmExecutor,
        requested_model: BoundedRlmRoute,
        strategy: Literal["single_call", "iterative_refinement"] = "single_call",
        max_model_calls: Annotated[int, Field(ge=1, le=8)] = 1,
        max_output_tokens_per_call: Annotated[int, Field(ge=1, le=32_768)] = 4_096,
        max_result_bytes_per_call: Annotated[int, Field(ge=1, le=262_144)] = 32_768,
        requested_reasoning_effort: BoundedRlmEffort | None = None,
    ) -> PublicRlmToolResult:
        try:
            with current() as (identity, runtime):
                _require_public_scope(identity, "aar:rlm")
                if len(query.encode("utf-8")) > settings.max_rlm_query_bytes:
                    raise PublicQuotaExceeded("RLM query exceeds the configured byte limit")
                if max_model_calls > settings.max_rlm_model_calls:
                    raise PublicQuotaExceeded(
                        "RLM model-call count exceeds the configured limit"
                    )
                if max_output_tokens_per_call > settings.max_rlm_output_tokens_per_call:
                    raise PublicQuotaExceeded(
                        "RLM output-token request exceeds the configured per-call limit"
                    )
                if max_result_bytes_per_call > settings.max_rlm_result_bytes_per_call:
                    raise PublicQuotaExceeded(
                        "RLM result-byte request exceeds the configured per-call limit"
                    )
                if (
                    strategy == "iterative_refinement"
                    and max_result_bytes_per_call > 32_768
                ):
                    raise PublicQuotaExceeded(
                        "iterative refinement result bytes cannot exceed 32768 so the next "
                        "bounded prompt remains representable"
                    )
                spec = PublicRlmJobSpec(
                    query=query,
                    strategy=strategy,
                    executor_kind=executor_kind,
                    requested_model=requested_model,
                    requested_reasoning_effort=requested_reasoning_effort,
                    max_model_calls=max_model_calls,
                    max_output_tokens_per_call=max_output_tokens_per_call,
                    max_result_bytes_per_call=max_result_bytes_per_call,
                )
                return runtime.rlm.start(spec, idempotency_key=idempotency_key)
        except Exception as error:
            return PublicRlmToolResult(failure=_public_rlm_failure(error))

    @server.tool(
        name="aar_rlm_claim_model_call",
        description=(
            "Atomically claim one pending RLM call and issue a ticket that inherits the job's "
            "fixed model route and bounds before the current host spends model capacity."
        ),
        annotations=DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_rlm_claim_model_call(
        job_id: IdentityValue,
        call_id: IdentityValue,
        expected_revision: Annotated[int, Field(ge=0)],
        call_spec_digest: Digest,
        idempotency_key: IdentityValue,
    ) -> PublicRlmToolResult:
        try:
            with current() as (identity, runtime):
                _require_public_scope(identity, "aar:rlm")
                request = PublicRlmClaimRequest(
                    job_id=job_id,
                    call_id=call_id,
                    expected_revision=expected_revision,
                    call_spec_digest=call_spec_digest,
                    idempotency_key=idempotency_key,
                )
                return runtime.rlm.claim(request)
        except Exception as error:
            return PublicRlmToolResult(failure=_public_rlm_failure(error))

    @server.tool(
        name="aar_rlm_commit_model_call",
        description=(
            "Commit one bounded host-executed model observation against its exact RLM ticket, "
            "then return the next call specification or terminal result."
        ),
        annotations=DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_rlm_commit_model_call(
        job_id: IdentityValue,
        call_id: IdentityValue,
        expected_revision: Annotated[int, Field(ge=0)],
        ticket_digest: Digest,
        outcome: Literal["succeeded", "failed_certain", "outcome_unknown"],
        idempotency_key: IdentityValue,
        output_text: BoundedRlmOutput | None = None,
        effective_model: BoundedRlmRoute | None = None,
        effective_reasoning_effort: BoundedRlmEffort | None = None,
        input_tokens: Annotated[int, Field(ge=0, le=100_000_000)] | None = None,
        output_tokens: Annotated[int, Field(ge=0, le=100_000_000)] | None = None,
        failure_code: BoundedRlmFailureCode | None = None,
        failure_message: BoundedRlmFailureMessage | None = None,
        host_receipt_id: BoundedHostReceiptId | None = None,
        host_receipt_digest: Digest | None = None,
    ) -> PublicRlmToolResult:
        try:
            with current() as (identity, runtime):
                _require_public_scope(identity, "aar:rlm")
                request = PublicRlmCommitRequest(
                    job_id=job_id,
                    call_id=call_id,
                    expected_revision=expected_revision,
                    ticket_digest=ticket_digest,
                    outcome=outcome,
                    output_text=output_text,
                    effective_model=effective_model,
                    effective_reasoning_effort=effective_reasoning_effort,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    failure_code=failure_code,
                    failure_message=failure_message,
                    host_receipt_id=host_receipt_id,
                    host_receipt_digest=host_receipt_digest,
                    idempotency_key=idempotency_key,
                )
                return runtime.rlm.commit(request)
        except Exception as error:
            return PublicRlmToolResult(failure=_public_rlm_failure(error))

    @server.tool(
        name="aar_rlm_status",
        description=(
            "Read the tenant-private caller-delegated RLM phase, revision, pending call or claimed "
            "ticket, route provenance, and terminal result without executing a model."
        ),
        annotations=READ_ONLY,
    )
    async def aar_rlm_status(job_id: IdentityValue) -> PublicRlmToolResult:
        try:
            with current() as (identity, runtime):
                _require_public_scope(identity, "aar:rlm")
                return runtime.rlm.status(job_id)
        except Exception as error:
            return PublicRlmToolResult(failure=_public_rlm_failure(error))

    @server.tool(
        name="aar_rlm_cancel",
        description=(
            "Cancel an unclaimed RLM call certainly, or record an indeterminate cancellation "
            "request after a host execution ticket has already been issued."
        ),
        annotations=DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_rlm_cancel(
        job_id: IdentityValue,
        expected_revision: Annotated[int, Field(ge=0)],
        idempotency_key: IdentityValue,
    ) -> PublicRlmToolResult:
        try:
            with current() as (identity, runtime):
                _require_public_scope(identity, "aar:rlm")
                return runtime.rlm.cancel(
                    job_id,
                    expected_revision=expected_revision,
                    idempotency_key=idempotency_key,
                )
        except Exception as error:
            return PublicRlmToolResult(failure=_public_rlm_failure(error))

    @server.tool(
        name="aar_artifact_resolve",
        description=(
            "Resolve one bounded content-addressed artifact returned by an operation belonging to "
            "the current authenticated tenant."
        ),
        annotations=READ_ONLY,
    )
    async def aar_artifact_resolve(
        artifact_id: IdentityValue,
        digest: Digest,
        media_type: MediaType,
        size_bytes: Annotated[int, Field(ge=0)],
        created_by_operation_id: IdentityValue,
        redacted: bool,
        max_bytes: BoundedArtifactBytes,
        allow_redacted: bool = False,
    ) -> PublicArtifactResult:
        operation = OperationRef(value=created_by_operation_id)
        try:
            with current() as (_identity, runtime):
                if max_bytes > settings.max_artifact_bytes:
                    raise ValueError("artifact disclosure exceeds the server limit")
                record = runtime.host.status(operation)
                _assert_operation_binding(runtime, record.request_json)
                requested_reference = ArtifactReference(
                    artifact=ArtifactIdRef(value=artifact_id),
                    digest=digest,
                    media_type=media_type,
                    size_bytes=size_bytes,
                    created_by=operation,
                    redacted=redacted,
                )
                reference = next(
                    (
                        candidate
                        for candidate in _receipt_artifact(runtime.host, record)
                        if candidate == requested_reference
                    ),
                    None,
                )
                if reference is None:
                    raise WorkspaceBindingDenied(
                        "artifact reference does not match the authenticated operation receipt"
                    )
                if reference.size_bytes > max_bytes:
                    raise ValueError("artifact exceeds the requested disclosure limit")
                if reference.redacted and not allow_redacted:
                    raise ValueError("redacted artifact requires explicit allow_redacted")
                content = runtime.host.artifacts.read(reference)
                return PublicArtifactResult(
                    artifact_id=reference.artifact.value,
                    digest=reference.digest,
                    media_type=reference.media_type,
                    size_bytes=reference.size_bytes,
                    content_base64=base64.b64encode(content).decode("ascii"),
                    redacted=reference.redacted,
                )
        except Exception as error:
            return PublicArtifactResult(failure=_public_failure(error, operation=operation))

    return PublicMcpApplication(server=server, tenant_pool=tenant_pool)


def create_public_asgi_app(
    application: PublicMcpApplication,
    settings: PublicServerSettings,
) -> Starlette:
    """Create the production ASGI surface around one owned public MCP runtime."""

    app = application.server.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        json_response=True,
        stateless_http=True,
        max_request_body_size=settings.max_request_body_size,
        transport_security=settings.transport_security(),
        host=settings.bind_host,
    )
    resource_url = AnyHttpUrl(settings.resource_url)
    metadata_path = urlsplit(str(build_resource_metadata_url(resource_url))).path
    metadata_routes = create_protected_resource_routes(
        resource_url=resource_url,
        authorization_servers=[AnyHttpUrl(settings.issuer_url)],
        scopes_supported=list(settings.required_scopes),
        resource_name="Adaptive Agent Runtime",
        resource_documentation=AnyHttpUrl(settings.documentation_url),
    )
    for index, route in enumerate(app.routes):
        if getattr(route, "path", None) == metadata_path:
            app.routes[index : index + 1] = metadata_routes
            break
    else:  # pragma: no cover - the authenticated SDK app must publish this route
        raise RuntimeError("MCP SDK omitted protected resource metadata route")

    async def health(_request: Request) -> PlainTextResponse:
        return PlainTextResponse("ok")

    async def openai_challenge(_request: Request) -> PlainTextResponse:
        if settings.openai_challenge_token is None:
            return PlainTextResponse("not configured", status_code=404)
        return PlainTextResponse(settings.openai_challenge_token)

    app.routes.insert(0, Route("/healthz", health, methods=["GET"]))
    app.routes.insert(
        0,
        Route(
            "/.well-known/openai-apps-challenge",
            openai_challenge,
            methods=["GET"],
        ),
    )
    return app


def _public_handle(workspace_id: str, handle: WorkspaceHandle) -> PublicWorkspaceHandle:
    return PublicWorkspaceHandle(
        workspace_id=workspace_id,
        generation=handle.generation,
        revision=handle.revision,
    )


def _validate_public_update(
    runtime: TenantRuntime,
    spec: WorkspaceExecuteSpec,
    settings: PublicServerSettings,
) -> None:
    handle = WorkspaceHandle(
        workspace=spec.workspace,
        generation=spec.expected_generation,
        revision=spec.expected_revision,
    )
    state = dict(runtime.host.inspect(handle).values)
    if spec.action == "set":
        candidate_value = spec.value
        state[spec.key] = spec.value
    elif spec.action == "delete":
        candidate_value = None
        state.pop(spec.key, None)
    else:
        current = state.get(spec.key, 0)
        if isinstance(current, bool) or not isinstance(current, int):
            raise ValueError("increment target is not an integer")
        assert isinstance(spec.value, int) and not isinstance(spec.value, bool)
        candidate_value = current + spec.value
        state[spec.key] = candidate_value
    if candidate_value is not None and (
        len(canonical_json_bytes(candidate_value)) > settings.max_value_bytes
    ):
        raise PublicQuotaExceeded("workspace value exceeds the configured byte limit")
    if len(state) > settings.max_keys_per_workspace:
        raise PublicQuotaExceeded("workspace key count exceeds the configured limit")
    if len(canonical_json_bytes(state)) > settings.max_workspace_bytes:
        raise PublicQuotaExceeded("workspace state exceeds the configured byte limit")


def _public_envelope(
    identity: PublicIdentity,
    runtime: TenantRuntime,
    *,
    capability: str,
    idempotency_key: str,
    input_digest: str,
    wall_time_ms: int,
    workspace: WorkspaceHandle,
) -> RequestEnvelope:
    now_ms = runtime.host.now_ms()
    deadline = now_ms + wall_time_ms
    authority_digest = hashlib.sha256(
        f"{identity.tenant_key}\0{idempotency_key}".encode()
    ).hexdigest()
    grant_id = f"public-{capability.replace('.', '-')}-{identity.tenant_key[:12]}"
    return RequestEnvelope(
        request_id=f"request-{authority_digest[:32]}",
        idempotency_key=f"public-{authority_digest}",
        host=HostRef(value=f"aar-public-{identity.tenant_key}"),
        principal=runtime.principal,
        lane=LaneRef(value="execute"),
        session=runtime.session,
        workspace=workspace.workspace,
        runtime_generation=runtime.host.runtime_generation,
        workspace_generation=workspace.generation,
        expected_workspace_revision=workspace.revision,
        capability_digest=runtime.host.capabilities.digest,
        deadline_unix_ms=deadline,
        grants=(
            Grant(
                grant_id=grant_id,
                capability=capability,
                issued_to=runtime.principal,
                expires_at_unix_ms=deadline,
            ),
        ),
        budget=Budget(wall_time_ms=wall_time_ms),
        trace_id=f"trace-{authority_digest[:32]}",
        input_digest=input_digest,
    )


def _assert_operation_binding(runtime: TenantRuntime, request_json: str) -> None:
    request = RequestEnvelope.model_validate_json(request_json, strict=True)
    if request.principal != runtime.principal or request.session != runtime.session:
        raise WorkspaceBindingDenied("operation belongs to a different authenticated tenant")


def _require_public_scope(identity: PublicIdentity, scope: str) -> None:
    if scope not in identity.scopes:
        raise PublicAuthenticationError(
            "authenticated token is missing the required AAR capability scope"
        )


def _public_operation_result(runtime: TenantRuntime, record: Any) -> PublicOperationResult:
    _assert_operation_binding(runtime, record.request_json)
    result = None if record.result_json is None else json.loads(record.result_json)
    return PublicOperationResult(
        operation_id=record.operation.value,
        state=record.state,
        certainty=record.certainty,
        record_revision=record.record_revision,
        reconciliation_required=record.reconciliation_required,
        result=result,
        artifacts=_receipt_artifact(runtime.host, record),
        failure=record.failure,
    )


def _public_failure(
    error: Exception,
    *,
    operation: OperationRef | None = None,
) -> FailureEnvelope:
    if isinstance(error, PublicAuthenticationError):
        return FailureEnvelope(
            category=FailureCategory.AUTHORITY,
            code="AUTHENTICATION_REQUIRED",
            message="a valid authenticated public identity is required",
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
    if isinstance(error, TenantCapacityExceeded):
        return FailureEnvelope(
            category=FailureCategory.BUDGET,
            code="TENANT_CAPACITY_EXCEEDED",
            message="the bounded tenant runtime pool is at capacity",
            retryable=True,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
    if isinstance(error, PublicQuotaExceeded):
        return FailureEnvelope(
            category=FailureCategory.BUDGET,
            code="PUBLIC_QUOTA_EXCEEDED",
            message=str(error),
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
            operation=operation,
        )
    return _failure(error, operation=operation)


def _public_rlm_failure(error: Exception) -> FailureEnvelope:
    if isinstance(error, PublicRlmNotFound):
        return FailureEnvelope(
            category=FailureCategory.AUTHORITY,
            code="RLM_JOB_NOT_FOUND",
            message="the RLM job is unavailable to the authenticated tenant",
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
        )
    if isinstance(error, PublicRlmCapacityExceeded):
        return FailureEnvelope(
            category=FailureCategory.BUDGET,
            code="RLM_CAPACITY_EXCEEDED",
            message=str(error),
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
        )
    if isinstance(error, PublicRlmConflict):
        return FailureEnvelope(
            category=FailureCategory.CONFLICT,
            code="RLM_BINDING_CONFLICT",
            message=str(error),
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
        )
    if isinstance(error, PublicRlmInvalidTransition):
        return FailureEnvelope(
            category=FailureCategory.CONFLICT,
            code="RLM_INVALID_TRANSITION",
            message=str(error),
            retryable=False,
            certainty=OutcomeCertainty.CERTAIN,
        )
    return _public_failure(error)


def _env_csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    values = tuple(sorted({item.strip() for item in raw.split(",") if item.strip()}))
    if not values and default:
        raise ValueError(f"{name} cannot be empty")
    return values


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return default if raw is None else int(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _env_optional(name: str) -> str | None:
    raw = os.environ.get(name)
    return None if raw is None else raw.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    args = parser.parse_args(argv)
    settings = PublicServerSettings.from_env()
    if args.host is not None:
        settings = replace(settings, bind_host=args.host)
    if args.port is not None:
        settings = replace(settings, bind_port=args.port)
    if args.check_config:
        print(
            json.dumps(
                {
                    "server_name": PUBLIC_SERVER_NAME,
                    "package_version": PACKAGE_VERSION,
                    "resource_url": settings.resource_url,
                    "issuer_url": settings.issuer_url,
                    "documentation_url": settings.documentation_url,
                    "mcp_path": settings.mcp_path,
                    "openai_challenge_configured": (settings.openai_challenge_token is not None),
                    "required_scopes": settings.required_scopes,
                    "allowed_hosts": settings.allowed_hosts,
                    "max_active_tenants": settings.max_active_tenants,
                    "max_request_body_size": settings.max_request_body_size,
                    "max_artifact_bytes": settings.max_artifact_bytes,
                    "max_workspaces_per_tenant": settings.max_workspaces_per_tenant,
                    "max_keys_per_workspace": settings.max_keys_per_workspace,
                    "max_value_bytes": settings.max_value_bytes,
                    "max_workspace_bytes": settings.max_workspace_bytes,
                    "max_rlm_jobs_per_tenant": settings.max_rlm_jobs_per_tenant,
                    "max_rlm_query_bytes": settings.max_rlm_query_bytes,
                    "max_rlm_model_calls": settings.max_rlm_model_calls,
                    "max_rlm_output_tokens_per_call": (
                        settings.max_rlm_output_tokens_per_call
                    ),
                    "max_rlm_result_bytes_per_call": (
                        settings.max_rlm_result_bytes_per_call
                    ),
                },
                sort_keys=True,
            )
        )
        return 0
    application = build_public_server(settings)
    try:
        uvicorn.run(
            create_public_asgi_app(application, settings),
            host=settings.bind_host,
            port=settings.bind_port,
        )
    finally:
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
