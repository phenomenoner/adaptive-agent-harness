"""Deterministic local-stdio MCP adapter over the reference-host operation semantics."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib.metadata
import importlib.resources
import json
import os
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from mcp_types.version import HANDSHAKE_PROTOCOL_VERSIONS, MODERN_PROTOCOL_VERSIONS
from pydantic import JsonValue

from aar.asset_models import AdaptiveAssetBundle, AdaptiveAssetRef, AssetKind
from aar.assets import AssetConflict, AssetReferenceMissing
from aar.broker_models import BrokerMethodName, ModelRouteCatalog, ModelRouteProfile
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.models import (
    ArtifactResolveToolResult,
    AssetBundleToolResult,
    AssetDocumentToolResult,
    AssetOutcomeToolResult,
    BrokerCatalogToolResult,
    BrokerContractsToolResult,
    CapabilitiesToolResult,
    CheckpointDescribeToolResult,
    IdentityValue,
    McpRlmWorkbenchMutationContext,
    McpRlmWorkbenchReadContext,
    ModelBrokerCapabilityProjection,
    OperationEventsToolResult,
    OperationToolResult,
    ProgramWorkspaceCheckpointToolResult,
    ProgramWorkspaceCloseToolResult,
    ProgramWorkspaceExecuteToolResult,
    ProgramWorkspaceHandleToolResult,
    ProgramWorkspaceHealthToolResult,
    ProgramWorkspaceInspectToolResult,
    ProgramWorkspaceInterruptToolResult,
    ProgramWorkspaceReconcileToolResult,
    ReferenceContextKey,
    ReferenceContextToolResult,
    ReferenceGrantDescriptor,
    RlmToolResult,
    SupervisorCapabilityProjection,
    WorkspaceHandleToolResult,
    WorkspaceInspectToolResult,
)
from aar.mcp.models import McpMutationContext as McpMutationContextModel
from aar.mcp.models import McpMutationContextArgument as McpMutationContext
from aar.mcp.models import McpReadContext as McpReadContextModel
from aar.mcp.models import McpReadContextArgument as McpReadContext
from aar.mcp.models import McpRlmMutationContextArgument as McpRlmMutationContext
from aar.mcp.workbench_surface import (
    SUCCESSOR_SURFACE_VERSION,
    bind_successor_tool_contract,
    frozen_v7_surface,
)
from aar.providers.gateway import GatewayDriverManifest, OwnerGatewayModelBroker
from aar.providers.mcp_sampling import McpSamplingGatewayTransport
from aar.rlm_models import RlmJobSpec
from aar.rlm_workbench_models import (
    CallerWorkCancelBeforeSendInput,
    CallerWorkClaimInput,
    CallerWorkCommitInput,
    CallerWorkMarkSendStartedInput,
    CallerWorkReconcileInput,
    RlmWorkbenchCapability,
    RlmWorkbenchExecuteInput,
    RlmWorkbenchFailure,
    require_fully_configured_workbench_capability,
)
from aar.runtime.brokers import (
    BrokerBudgetExceeded,
    BrokerCallConflict,
    BrokerCallIndeterminate,
    BrokerGrantDenied,
)
from aar.runtime.caller_work import CallerWorkError
from aar.runtime.ipython_backend import WorkspaceWorkerLost, WorkspaceWorkerProtocolError
from aar.runtime.model_broker import ModelBrokerRegistry, StaticModelBrokerRegistry
from aar.runtime.models import WorkspaceExecuteSpec, WorkspaceHandle
from aar.runtime.ownership import RuntimeOwnershipLock
from aar.runtime.reference_host import (
    BudgetDenied,
    CapabilityMismatch,
    DeadlineExpired,
    GrantDenied,
    InputDigestMismatch,
    ReferenceHost,
    ReferenceHostError,
    RlmStateMissing,
    WorkspaceBindingDenied,
)
from aar.runtime.registry import (
    IdempotencyConflict,
    InvalidTransition,
    StaleRuntimeGeneration,
)
from aar.runtime.rlm_workbench import RlmWorkbenchConflict, RlmWorkbenchDeadlineExceeded
from aar.runtime.workspace import (
    StaleWorkspaceGeneration,
    WorkspaceError,
    WorkspaceRevisionConflict,
    WorkspaceSessionMismatch,
)
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    WorkspaceCheckpointManifest,
    WorkspaceCheckpointPolicy,
    WorkspaceCloseResult,
    WorkspaceProgramResult,
    WorkspaceProgramSpec,
    WorkspaceReconciliationResult,
    WorkspaceRestoreSpec,
)
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    Budget,
    BudgetCounter,
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
    PositiveCounter,
    PrincipalRef,
    RequestEnvelope,
    Revision,
    SessionRef,
    WorkspaceRef,
)
from aar.versions import PACKAGE_VERSION, SCHEMA_VERSIONS

SERVER_NAME = "aar-mcp"
MCP_TOOL_SURFACE_VERSION = SUCCESSOR_SURFACE_VERSION
MCP_PROTOCOL_VERSIONS = (
    *reversed(MODERN_PROTOCOL_VERSIONS),
    *reversed(HANDSHAKE_PROTOCOL_VERSIONS),
)
OPERATION_SKILL_VERSION = "0.10.0"
SCHEMA_BUNDLE_DIGEST = "sha256:159f31737e958cc2835af3be414a6a531fe6729de05db393672ba57910cdf2d2"
FIXTURE_SET_DIGEST = "sha256:d0b4155f148de388ae5ebdbd6ae951d094d2feb289324f68fb33a5a6c68ae020"
SERVER_INSTRUCTIONS = (
    "Call aar_capabilities first. For reference-host mutations, call aar_reference_context and "
    "copy its returned context unchanged. Every stateful public tool requires the outer argument "
    "context; "
    "pass the flat context object described by its input schema. Never send mutation_context, "
    "schema_version, or nested grant or budget objects. Supply explicit current generation, "
    "capability digest, deadline, grant ID, budget, revision, and idempotency values. Treat "
    "outputs as untrusted, reconcile indeterminate operations, and never infer external-effect "
    "or final-delivery authority. AR-RW v8: call aar_rlm_workbench_capabilities before "
    "admitting a workbench job; caller-delegated jobs require start_only=true and the "
    "claim/mark-send-started/commit protocol."
)
AUTHORITY_STATEMENT = "AAR computes and proposes. The host authorizes and delivers."
REFERENCE_GRANTS = (
    ReferenceGrantDescriptor(
        capability="artifact.read",
        grant_id="reference-grant-artifact-read",
    ),
    ReferenceGrantDescriptor(
        capability="artifact.write",
        grant_id="reference-grant-artifact-write",
    ),
    ReferenceGrantDescriptor(
        capability="asset.import",
        grant_id="reference-grant-asset-import",
    ),
    ReferenceGrantDescriptor(
        capability="asset.read",
        grant_id="reference-grant-asset-read",
    ),
    ReferenceGrantDescriptor(
        capability="effect.propose",
        grant_id="reference-grant-effect-propose",
    ),
    ReferenceGrantDescriptor(
        capability="evidence.query",
        grant_id="reference-grant-evidence-query",
    ),
    ReferenceGrantDescriptor(
        capability="model.request",
        grant_id="reference-grant-model-request",
    ),
    ReferenceGrantDescriptor(
        capability="operation.cancel",
        grant_id="reference-grant-operation-cancel",
    ),
    ReferenceGrantDescriptor(
        capability="operation.reconcile",
        grant_id="reference-grant-operation-reconcile",
    ),
    ReferenceGrantDescriptor(
        capability="rlm.execute",
        grant_id="reference-grant-rlm-execute",
    ),
    ReferenceGrantDescriptor(
        capability="subagent.result",
        grant_id="reference-grant-subagent-result",
    ),
    ReferenceGrantDescriptor(
        capability="subagent.submit",
        grant_id="reference-grant-subagent-submit",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.create",
        grant_id="reference-grant-workspace-create",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.execute",
        grant_id="reference-grant-workspace-execute",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.checkpoint",
        grant_id="reference-grant-workspace-program-checkpoint",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.close",
        grant_id="reference-grant-workspace-program-close",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.create",
        grant_id="reference-grant-workspace-program-create",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.execute",
        grant_id="reference-grant-workspace-program-execute",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.interrupt",
        grant_id="reference-grant-workspace-program-interrupt",
    ),
    ReferenceGrantDescriptor(
        capability="workspace.program.restore",
        grant_id="reference-grant-workspace-program-restore",
    ),
)
REFERENCE_GRANT_IDS = {item.capability: item.grant_id for item in REFERENCE_GRANTS}
REFERENCE_GRANT_CAPABILITIES = {item.grant_id: item.capability for item in REFERENCE_GRANTS}
# The successor-only v8 grant is intentionally absent from frozen aar_capabilities v7 bytes.
# It is described by the v8 workbench profile and accepted only by the successor tool family.
REFERENCE_GRANT_CAPABILITIES["reference-grant-rlm-workbench-execute"] = "rlm.workbench.execute"

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MUTATING_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
MUTATING_CANCEL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
MUTATING_DESTRUCTIVE_IDEMPOTENT = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)


@dataclass
class AarMcpApplication:
    server: MCPServer
    host: ReferenceHost
    runtime_ownership: RuntimeOwnershipLock

    def close(self) -> None:
        try:
            self.host.close()
        finally:
            self.runtime_ownership.close()


def _receipt_artifact(host: ReferenceHost, record) -> tuple[ArtifactReference, ...]:
    if record.state is not OperationState.SUCCEEDED or record.result_json is None:
        return ()
    payload = {
        "operation": record.operation.model_dump(mode="json"),
        "state": record.state.value,
        "certainty": record.certainty.value,
        "record_revision": record.record_revision,
        "result": json.loads(record.result_json),
    }
    return (
        host.artifacts.put(
            canonical_json_bytes(payload),
            "application/vnd.aar.operation-receipt+json",
            record.operation,
        ),
    )


def _operation_result(record, *, host: ReferenceHost | None = None) -> OperationToolResult:
    artifacts = () if host is None else _receipt_artifact(host, record)
    return OperationToolResult.from_record(record, artifacts=artifacts)


def _envelope_handle(handle: ProgrammableWorkspaceHandle) -> WorkspaceHandle:
    return WorkspaceHandle(
        workspace=handle.workspace,
        generation=handle.generation,
        revision=handle.revision,
    )


def _program_failure(result: WorkspaceProgramResult) -> FailureEnvelope:
    exception = next((event for event in result.events if event.kind == "exception"), None)
    message = "programmable workspace execution failed"
    if exception is not None and isinstance(exception.data, dict):
        message = str(exception.data.get("message", message))[:512]
    return FailureEnvelope(
        category=FailureCategory.WORKER,
        code=("WORKSPACE_WORKER_LOST" if result.workspace_lost else "WORKSPACE_EXECUTION_FAILED"),
        message=message,
        retryable=result.workspace_lost,
        certainty=OutcomeCertainty.CERTAIN,
        operation=result.operation,
    )


def _program_receipt_unavailable(operation: OperationRef) -> FailureEnvelope:
    return FailureEnvelope(
        category=FailureCategory.WORKER,
        code="PROGRAM_RECEIPT_UNAVAILABLE_AFTER_RESTART",
        message=(
            "the programmable worker receipt is unavailable after runtime restart; "
            "the operation outcome remains indeterminate"
        ),
        retryable=False,
        certainty=OutcomeCertainty.INDETERMINATE,
        operation=operation,
    )


def _finish_program_operation(
    host: ReferenceHost,
    result: WorkspaceProgramResult,
):
    result_json = canonical_json_bytes(result).decode()
    if result.status == "succeeded":
        return host.registry.succeed(
            result.operation,
            result_json,
            host.runtime_generation,
        )
    if result.status == "failed":
        return host.registry.fail(
            result.operation,
            _program_failure(result),
            host.runtime_generation,
            result_json=result_json,
        )
    if result.status == "interrupted":
        return host.registry.cancel(
            result.operation,
            host.runtime_generation,
            result_json=result_json,
        )
    return host.registry.time_out(
        result.operation,
        host.runtime_generation,
        result_json=result_json,
    )


def _failure(
    error: Exception,
    *,
    operation: OperationRef | None = None,
) -> FailureEnvelope:
    if isinstance(
        error,
        CapabilityMismatch
        | GrantDenied
        | BrokerGrantDenied
        | WorkspaceBindingDenied
        | WorkspaceSessionMismatch,
    ):
        category = FailureCategory.AUTHORITY
        code = "BROKER_GRANT_DENIED" if isinstance(error, BrokerGrantDenied) else "AUTHORITY_DENIED"
        retryable = False
    elif isinstance(error, BudgetDenied | BrokerBudgetExceeded):
        category = FailureCategory.BUDGET
        code = "BUDGET_DENIED"
        retryable = False
    elif isinstance(error, DeadlineExpired):
        category = FailureCategory.DEADLINE
        code = "DEADLINE_EXPIRED"
        retryable = True
    elif isinstance(error, RlmStateMissing):
        category = FailureCategory.CONFLICT
        code = "RLM_STATE_MISSING"
        retryable = True
    elif isinstance(
        error,
        IdempotencyConflict | InvalidTransition | BrokerCallConflict | AssetConflict,
    ):
        category = FailureCategory.CONFLICT
        code = "OPERATION_CONFLICT"
        retryable = False
    elif isinstance(error, BrokerCallIndeterminate):
        category = FailureCategory.TRANSPORT
        code = "BROKER_CALL_INDETERMINATE"
        retryable = True
    elif isinstance(
        error,
        StaleRuntimeGeneration | StaleWorkspaceGeneration | WorkspaceRevisionConflict,
    ):
        category = FailureCategory.STALE
        code = "STALE_RUNTIME_STATE"
        retryable = False
    elif isinstance(error, KeyError):
        category = FailureCategory.VALIDATION
        code = "REFERENCE_NOT_FOUND"
        retryable = False
    elif isinstance(error, WorkspaceWorkerLost | WorkspaceWorkerProtocolError):
        category = FailureCategory.WORKER
        code = "WORKSPACE_WORKER_UNAVAILABLE"
        retryable = True
    elif isinstance(
        error,
        InputDigestMismatch | WorkspaceError | AssetReferenceMissing | ValueError,
    ):
        category = FailureCategory.VALIDATION
        code = "INVALID_ARGUMENT"
        retryable = False
    elif isinstance(error, ReferenceHostError):
        category = FailureCategory.INTERNAL
        code = "REFERENCE_HOST_ERROR"
        retryable = False
    else:
        category = FailureCategory.INTERNAL
        code = "INTERNAL_ERROR"
        retryable = False
    message = str(error) if code != "INTERNAL_ERROR" else "internal MCP adapter error"
    return FailureEnvelope(
        category=category,
        code=code,
        message=message or code.lower(),
        retryable=retryable,
        certainty=OutcomeCertainty.CERTAIN,
        operation=operation,
    )


def _validate_read_context(
    host: ReferenceHost,
    context: McpReadContextModel | McpRlmWorkbenchReadContext,
) -> None:
    if context.runtime_generation != host.runtime_generation:
        raise StaleRuntimeGeneration(
            f"runtime generation is {host.runtime_generation}, not {context.runtime_generation}"
        )
    if context.capability_digest != host.capabilities.digest:
        raise CapabilityMismatch("capability digest does not match the reference host")
    if host.now_ms() >= context.deadline_unix_ms:
        raise DeadlineExpired("request deadline has expired")


def _validate_mutation_context(
    host: ReferenceHost,
    context: McpMutationContext,
    capability: str,
) -> None:
    _validate_read_context(host, context)
    expected_grant = REFERENCE_GRANT_IDS.get(capability)
    if expected_grant is None or context.grant_id != expected_grant:
        raise GrantDenied(f"reference host did not issue {capability} to this request")
    remaining_ms = context.deadline_unix_ms - host.now_ms()
    if remaining_ms > context.budget_wall_time_ms:
        raise BudgetDenied("wall-time budget does not cover the request deadline")


def _envelope(
    host: ReferenceHost,
    context: McpMutationContext,
    capability: str,
    input_digest: str,
    *,
    workspace: WorkspaceHandle | None = None,
) -> RequestEnvelope:
    _validate_mutation_context(host, context, capability)
    principal = PrincipalRef(value=context.principal_id)
    return RequestEnvelope(
        request_id=context.request_id,
        idempotency_key=context.idempotency_key,
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="execute"),
        session=SessionRef(value=context.session_id),
        workspace=None if workspace is None else workspace.workspace,
        runtime_generation=context.runtime_generation,
        workspace_generation=None if workspace is None else workspace.generation,
        expected_workspace_revision=None if workspace is None else workspace.revision,
        capability_digest=context.capability_digest,
        deadline_unix_ms=context.deadline_unix_ms,
        grants=(
            Grant(
                grant_id=context.grant_id,
                capability=capability,
                issued_to=principal,
                expires_at_unix_ms=context.deadline_unix_ms,
            ),
        ),
        budget=Budget(wall_time_ms=context.budget_wall_time_ms),
        trace_id=f"trace-{context.request_id}",
        input_digest=input_digest,
    )


def _rlm_envelope(
    host: ReferenceHost,
    context: McpRlmMutationContext | McpRlmWorkbenchMutationContext,
    input_digest: str,
) -> RequestEnvelope:
    _validate_read_context(host, context)
    remaining_ms = context.deadline_unix_ms - host.now_ms()
    if remaining_ms > context.budget_wall_time_ms:
        raise BudgetDenied("wall-time budget does not cover the request deadline")
    unknown = sorted(set(context.grant_ids) - set(REFERENCE_GRANT_CAPABILITIES))
    if unknown:
        raise GrantDenied("reference host did not issue grant ids: " + ", ".join(unknown))
    principal = PrincipalRef(value=context.principal_id)
    grants = tuple(
        Grant(
            grant_id=grant_id,
            capability=REFERENCE_GRANT_CAPABILITIES[grant_id],
            issued_to=principal,
            expires_at_unix_ms=context.deadline_unix_ms,
        )
        for grant_id in context.grant_ids
    )
    return RequestEnvelope(
        request_id=context.request_id,
        idempotency_key=context.idempotency_key,
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="rlm"),
        session=SessionRef(value=context.session_id),
        runtime_generation=context.runtime_generation,
        capability_digest=context.capability_digest,
        deadline_unix_ms=context.deadline_unix_ms,
        grants=grants,
        budget=Budget(
            wall_time_ms=context.budget_wall_time_ms,
            model_requests=context.budget_model_requests,
            input_tokens=context.budget_input_tokens,
            output_tokens=context.budget_output_tokens,
            child_operations=context.budget_child_operations,
            artifact_bytes=context.budget_artifact_bytes,
        ),
        trace_id=f"trace-{context.request_id}",
        input_digest=input_digest,
    )


def _assert_operation_binding(
    host: ReferenceHost,
    context: McpReadContextModel | McpRlmWorkbenchReadContext,
    operation: OperationRef,
) -> RequestEnvelope:
    _validate_read_context(host, context)
    record = host.registry.get(operation)
    request = RequestEnvelope.model_validate_json(record.request_json, strict=True)
    if (
        request.principal.value != context.principal_id
        or request.session.value != context.session_id
    ):
        raise WorkspaceBindingDenied("operation is bound to another principal or session")
    return request


def _current_reference_grant(
    host: ReferenceHost,
    context: McpReadContext,
    grant_id: str | None,
    capability: str,
) -> Grant:
    _validate_read_context(host, context)
    expected_grant = REFERENCE_GRANT_IDS.get(capability)
    if expected_grant is None or grant_id is None or grant_id != expected_grant:
        raise GrantDenied(f"reference host did not issue current {capability} authority")
    return Grant(
        grant_id=grant_id,
        capability=capability,
        issued_to=PrincipalRef(value=context.principal_id),
        expires_at_unix_ms=context.deadline_unix_ms,
    )


def _workbench_failure(
    error: Exception,
    *,
    operation: OperationRef | None = None,
) -> RlmWorkbenchFailure:
    """Map local exceptions into the frozen successor failure vocabulary."""

    category = "internal"
    code = "INTERNAL_ERROR"
    retryable = False
    if isinstance(error, RlmWorkbenchDeadlineExceeded | DeadlineExpired):
        category, code = "deadline", "DEADLINE_EXCEEDED"
    elif isinstance(error, GrantDenied | BrokerGrantDenied):
        category, code = "authority", "GRANT_DENIED"
    elif isinstance(error, BudgetDenied | BrokerBudgetExceeded):
        category, code = "budget", "BUDGET_EXCEEDED"
    elif isinstance(error, CallerWorkError):
        category, code = "conflict", "CALLER_WORK_CONFLICT"
    elif isinstance(error, IdempotencyConflict | RlmWorkbenchConflict | InvalidTransition):
        category, code = "conflict", "CONFLICT"
    elif isinstance(error, KeyError):
        category, code = "stale", "NOT_FOUND"
    elif isinstance(
        error,
        StaleRuntimeGeneration | CapabilityMismatch | WorkspaceBindingDenied | InputDigestMismatch,
    ):
        category, code = "authority", "CONTEXT_INVALID"
    elif isinstance(error, ReferenceHostError):
        category, code = "authority", "CAPABILITY_UNAVAILABLE"
    elif isinstance(error, ValueError):
        category, code = "validation", "INVALID_ARGUMENT"
    return RlmWorkbenchFailure.model_validate(
        {
            "schema_version": "aar.envelope.v1",
            "category": category,
            "code": code,
            "message": str(error)[:512] or type(error).__name__,
            "retryable": retryable,
            "certainty": "certain",
            "operation": None if operation is None else operation.model_dump(mode="json"),
            "details": [],
        },
        strict=True,
    )


def _workbench_result(value: Any) -> dict[str, Any]:
    if hasattr(value, "root"):
        return dict(value.root)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(value)


def _assert_workbench_operation_binding(
    host: ReferenceHost,
    context: McpRlmWorkbenchReadContext,
    operation: OperationRef,
    *,
    mutation: McpRlmWorkbenchMutationContext | None = None,
) -> RequestEnvelope:
    request = _assert_operation_binding(host, context, operation)
    granted = {grant.capability for grant in request.grants}
    if "rlm.workbench.execute" not in granted:
        raise WorkspaceBindingDenied("operation is not an RLM workbench execution")
    if context.deadline_unix_ms > request.deadline_unix_ms:
        raise DeadlineExpired("successor request cannot extend the cumulative deadline")
    if mutation is not None:
        admitted_grants = tuple(grant.grant_id for grant in request.grants)
        if tuple(mutation.grant_ids) != admitted_grants:
            raise GrantDenied("successor grant IDs differ from the admitted workbench context")
        admitted_budget = request.budget
        bounded = (
            mutation.budget_wall_time_ms <= admitted_budget.wall_time_ms
            and mutation.budget_model_requests <= admitted_budget.model_requests
            and mutation.budget_input_tokens <= admitted_budget.input_tokens
            and mutation.budget_output_tokens <= admitted_budget.output_tokens
            and mutation.budget_child_operations <= admitted_budget.child_operations
            and mutation.budget_artifact_bytes <= admitted_budget.artifact_bytes
        )
        if not bounded:
            raise BudgetDenied("successor request exceeds the admitted cumulative budget")
    return request


def _workbench_capability(host: ReferenceHost) -> RlmWorkbenchCapability:
    return host.workbench_capability()


def _run_caller_work_command(
    host: ReferenceHost,
    model_type: type[Any],
    method_name: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    command = model_type.model_validate(payload, strict=True)
    document = dict(command.root)
    context = McpRlmWorkbenchMutationContext.model_validate(document["context"], strict=True)
    operation = OperationRef.model_validate(document["operation"], strict=True)
    _assert_workbench_operation_binding(host, context, operation, mutation=context)
    if host.caller_work is None:
        raise ReferenceHostError("caller-work repository is unavailable")
    method = getattr(host.caller_work, method_name)
    return _workbench_result(method(document))


def _skill_digest() -> str:
    root = Path(__file__).resolve().parents[3]
    skill_path = root / "skills" / "aar-operations" / "SKILL.md"
    if skill_path.is_file():
        skill_bytes = skill_path.read_bytes()
    else:
        bundled = importlib.resources.files("aar").joinpath("bundled", "aar-operations", "SKILL.md")
        skill_bytes = bundled.read_bytes()
    return f"sha256:{hashlib.sha256(skill_bytes).hexdigest()}"


async def tool_surface_manifest(server: MCPServer) -> dict[str, Any]:
    tools = await server.list_tools()
    core = {
        "server_name": SERVER_NAME,
        "server_version": PACKAGE_VERSION,
        "sdk": {"name": "mcp", "version": importlib.metadata.version("mcp")},
        "protocol_versions": list(MCP_PROTOCOL_VERSIONS),
        "instructions": SERVER_INSTRUCTIONS,
        "tool_surface_version": SUCCESSOR_SURFACE_VERSION,
        "tools": [tool.model_dump(mode="json", by_alias=True) for tool in tools],
    }
    return {**core, "tool_surface_digest": canonical_sha256(core)}


def hermes_mcp_sampling_luna_max_registry(
    *,
    now_ms=None,
) -> tuple[StaticModelBrokerRegistry, McpSamplingGatewayTransport, str]:
    """Build the deprecated fixed Hermes Luna MCP Sampling compatibility route."""

    profile = ModelRouteProfile(
        profile_id="hermes-luna-max-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=8_192,
        fallback_policy="none",
        cache_policy="disabled",
    )
    clock = now_ms or (lambda: time.time_ns() // 1_000_000)
    transport = McpSamplingGatewayTransport(now_ms=clock)
    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id=profile.provider_driver,
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
    return registry, transport, profile.profile_id


def build_server(
    database_path: Path,
    *,
    now_ms=None,
    programmable_backend: Literal["plain", "ipython"] = "ipython",
    enable_durable_dispatch: bool = True,
    dispatcher_concurrency: int = 2,
    runtime_owner_mode: Literal["embedded-reference-host", "attached-supervisor"] = (
        "embedded-reference-host"
    ),
    supervisor_version: str | None = None,
    supervisor_protocol_version: str | None = None,
    supervisor_protocol_digest: str | None = None,
    supervisor_process_identity_digest: str | None = None,
    model_broker_registry: ModelBrokerRegistry | None = None,
    default_model_route_profile: str | None = None,
    mcp_sampling_transport: McpSamplingGatewayTransport | None = None,
    workbench_backend_availability: dict[str, dict[str, Any]] | None = None,
) -> AarMcpApplication:
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_ownership = RuntimeOwnershipLock(database_path)
    try:
        host = ReferenceHost(
            database_path,
            programmable_backend=programmable_backend,
            enable_durable_dispatch=enable_durable_dispatch,
            dispatcher_concurrency=dispatcher_concurrency,
            model_broker_registry=model_broker_registry,
            default_model_route_profile=default_model_route_profile,
            workbench_backend_availability=workbench_backend_availability,
            **({} if now_ms is None else {"now_ms": now_ms}),
        )
    except BaseException:
        runtime_ownership.close()
        raise
    server = MCPServer(
        SERVER_NAME,
        title="Adaptive Agent Runtime",
        description="Host-neutral bounded workspace operations over the AAR reference host",
        instructions=SERVER_INSTRUCTIONS,
        version=PACKAGE_VERSION,
    )
    supervisor_projection = SupervisorCapabilityProjection(
        mode=runtime_owner_mode,
        frontend_ephemeral=runtime_owner_mode == "attached-supervisor",
        dispatcher_generation=host.runtime_generation,
        supervisor_version=supervisor_version,
        protocol_version=supervisor_protocol_version,
        protocol_digest=supervisor_protocol_digest,
        process_identity_digest=supervisor_process_identity_digest,
    )

    @server.tool(
        name="aar_capabilities",
        description=(
            "Read exact AAR, schema, skill, negotiated protocol, tool, and authority capabilities."
        ),
        annotations=READ_ONLY,
    )
    async def aar_capabilities(request: Context) -> CapabilitiesToolResult:
        manifest = frozen_v7_surface()
        negotiated_protocol_version = request.protocol_version
        if negotiated_protocol_version is None:  # pragma: no cover
            raise RuntimeError("MCP request did not expose a negotiated protocol version")
        model_routes = (
            None if host.model_broker_registry is None else host.model_broker_registry.describe()
        )
        model_broker = (
            ModelBrokerCapabilityProjection(configured=False)
            if model_routes is None
            else ModelBrokerCapabilityProjection(
                configured=True,
                default_profile_id=host.default_model_route_profile,
                catalog_digest=model_routes.catalog_digest,
                route_profile_count=len(model_routes.profiles),
                journal_schema_versions=(
                    () if host.model_executions is None else host.model_executions.schema_versions()
                ),
            )
        )
        return CapabilitiesToolResult(
            server_name=SERVER_NAME,
            server_now_unix_ms=host.now_ms(),
            package_version=PACKAGE_VERSION,
            sdk_name="mcp",
            sdk_version=importlib.metadata.version("mcp"),
            protocol_versions=MCP_PROTOCOL_VERSIONS,
            negotiated_protocol_version=negotiated_protocol_version,
            tool_surface_version=manifest["tool_surface_version"],
            tool_surface_digest=manifest["tool_surface_digest"],
            operation_skill_version=OPERATION_SKILL_VERSION,
            operation_skill_digest=_skill_digest(),
            schema_versions=tuple(sorted(SCHEMA_VERSIONS.items())),
            schema_bundle_digest=SCHEMA_BUNDLE_DIGEST,
            fixture_set_digest=FIXTURE_SET_DIGEST,
            model_routes=model_routes,
            model_broker=model_broker,
            ready=host.ready(),
            supervisor=supervisor_projection,
            tool_names=tuple(tool["name"] for tool in manifest["tools"]),
            reference_grants=REFERENCE_GRANTS,
            unsupported_capabilities=(
                "effect.execute",
                "external.delivery",
                "provider.credentials",
                "workspace.pickle-checkpoint",
                "workspace.security-sandbox",
            ),
            authority_statement=AUTHORITY_STATEMENT,
        )

    @server.tool(
        name="aar_reference_context",
        description=(
            "Prepare current flat read and mutation contexts for one exact reference-host "
            "mutation. Copy the returned context unchanged into the outer context argument."
        ),
        annotations=READ_ONLY,
    )
    async def aar_reference_context(
        capability: IdentityValue,
        context_key: ReferenceContextKey,
        budget_wall_time_ms: PositiveCounter = 60_000,
    ) -> ReferenceContextToolResult:
        try:
            if budget_wall_time_ms > 60_000:
                raise BudgetDenied("reference-host wall-time budget cannot exceed 60000 ms")
            grant_id = REFERENCE_GRANT_IDS.get(capability)
            if grant_id is None:
                raise GrantDenied("capability has no published reference-host grant")
            ready = host.ready()
            now_ms = host.now_ms()
            read_context = McpReadContextModel(
                runtime_generation=ready.runtime_generation,
                capability_digest=ready.capabilities.digest,
                principal_id="reference-principal",
                session_id=f"reference-session-{ready.runtime_generation}",
                deadline_unix_ms=now_ms + budget_wall_time_ms,
            )
            context = McpMutationContextModel(
                **read_context.model_dump(mode="python"),
                request_id=f"request-{context_key}",
                idempotency_key=f"idempotency-{context_key}",
                grant_id=grant_id,
                budget_wall_time_ms=budget_wall_time_ms,
            )
            return ReferenceContextToolResult(
                read_context=read_context,
                context=context,
            )
        except Exception as error:
            return ReferenceContextToolResult(failure=_failure(error))

    @server.tool(
        name="aar_workspace_create",
        description="Create or idempotently recover a reference-host workspace handle.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_workspace_create(
        context: McpMutationContext,
        workspace_id: IdentityValue,
    ) -> WorkspaceHandleToolResult:
        operation: OperationRef | None = None
        try:
            workspace = WorkspaceRef(value=workspace_id)
            payload = {
                "kind": "workspace.create",
                "session": context.session_id,
                "workspace": workspace.model_dump(mode="json"),
            }
            envelope = _envelope(
                host,
                context,
                "workspace.create",
                canonical_sha256(payload),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED:
                host.registry.begin(record.operation, host.runtime_generation)
                handle = host.create_workspace(workspace, SessionRef(value=context.session_id))
                record = host.registry.succeed(
                    record.operation,
                    canonical_json_bytes(handle).decode(),
                    host.runtime_generation,
                )
            result = _operation_result(record)
            handle = None
            if record.result_json is not None:
                handle = WorkspaceHandle.model_validate_json(record.result_json, strict=True)
            return WorkspaceHandleToolResult(handle=handle, operation=result)
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return WorkspaceHandleToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_workspace_attach",
        description="Attach to an exact session, workspace generation, and revision.",
        annotations=READ_ONLY,
    )
    async def aar_workspace_attach(
        context: McpReadContext,
        workspace_id: IdentityValue,
        expected_generation: PositiveCounter,
        expected_revision: Revision,
    ) -> WorkspaceHandleToolResult:
        try:
            _validate_read_context(host, context)
            handle = host.workspace.attach(
                WorkspaceRef(value=workspace_id),
                SessionRef(value=context.session_id),
                expected_generation,
                expected_revision,
            )
            return WorkspaceHandleToolResult(handle=handle)
        except Exception as error:
            return WorkspaceHandleToolResult(failure=_failure(error))

    @server.tool(
        name="aar_workspace_execute",
        description=(
            "Submit an idempotent bounded workspace mutation and return its operation handle; "
            "start_only may leave it accepted for status or cancellation."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_workspace_execute(
        context: McpMutationContext,
        workspace_id: IdentityValue,
        expected_generation: PositiveCounter,
        expected_revision: Revision,
        action: Literal["set", "delete", "increment"],
        key: IdentityValue,
        value: JsonValue | None = None,
        start_only: bool = False,
    ) -> OperationToolResult:
        try:
            spec = WorkspaceExecuteSpec(
                workspace=WorkspaceRef(value=workspace_id),
                expected_generation=expected_generation,
                expected_revision=expected_revision,
                action=action,
                key=key,
                value=value,
            )
            handle = WorkspaceHandle(
                workspace=spec.workspace,
                generation=spec.expected_generation,
                revision=spec.expected_revision,
            )
            envelope = _envelope(
                host,
                context,
                "workspace.execute",
                canonical_sha256(spec),
                workspace=handle,
            )
            record = host.submit_execute(envelope, spec)
            if record.state is OperationState.ACCEPTED and not start_only:
                record = host.run_execute(record.operation)
            return _operation_result(record, host=host)
        except Exception as error:
            return OperationToolResult(failure=_failure(error))

    @server.tool(
        name="aar_workspace_inspect",
        description="Inspect the exact bounded JSON-scalar snapshot of a workspace revision.",
        annotations=READ_ONLY,
    )
    async def aar_workspace_inspect(
        context: McpReadContext,
        workspace_id: IdentityValue,
        expected_generation: PositiveCounter,
        expected_revision: Revision,
    ) -> WorkspaceInspectToolResult:
        try:
            _validate_read_context(host, context)
            handle = host.workspace.attach(
                WorkspaceRef(value=workspace_id),
                SessionRef(value=context.session_id),
                expected_generation,
                expected_revision,
            )
            return WorkspaceInspectToolResult(snapshot=host.inspect(handle))
        except Exception as error:
            return WorkspaceInspectToolResult(failure=_failure(error))

    @server.tool(
        name="aar_program_workspace_create",
        description="Create a programmable workspace generation using the configured backend.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_program_workspace_create(
        context: McpMutationContext,
        workspace_id: IdentityValue,
    ) -> ProgramWorkspaceHandleToolResult:
        operation: OperationRef | None = None
        try:
            workspace = WorkspaceRef(value=workspace_id)
            payload = {
                "kind": "workspace.program.create",
                "session": context.session_id,
                "workspace": workspace,
            }
            envelope = _envelope(
                host,
                context,
                "workspace.program.create",
                canonical_sha256(payload),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED:
                host.registry.begin(operation, host.runtime_generation)
                handle = await asyncio.to_thread(
                    host.program_workspace.create,
                    workspace,
                    SessionRef(value=context.session_id),
                )
                record = host.registry.succeed(
                    operation,
                    canonical_json_bytes(handle).decode(),
                    host.runtime_generation,
                )
            handle = (
                None
                if record.result_json is None
                else ProgrammableWorkspaceHandle.model_validate_json(
                    record.result_json, strict=True
                )
            )
            return ProgramWorkspaceHandleToolResult(
                handle=handle,
                operation=_operation_result(record),
                failure=record.failure,
            )
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return ProgramWorkspaceHandleToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_program_workspace_attach",
        description="Attach to an exact programmable workspace session, generation, and revision.",
        annotations=READ_ONLY,
    )
    async def aar_program_workspace_attach(
        context: McpReadContext,
        handle: ProgrammableWorkspaceHandle,
    ) -> ProgramWorkspaceHandleToolResult:
        try:
            _validate_read_context(host, context)
            handle = await asyncio.to_thread(
                host.program_workspace.attach,
                handle,
                SessionRef(value=context.session_id),
            )
            return ProgramWorkspaceHandleToolResult(handle=handle)
        except Exception as error:
            return ProgramWorkspaceHandleToolResult(failure=_failure(error))

    @server.tool(
        name="aar_program_workspace_execute",
        description=(
            "Execute bounded Python in the configured programmable workspace and "
            "return structured events plus an operation receipt."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_program_workspace_execute(
        context: McpMutationContext,
        handle: ProgrammableWorkspaceHandle,
        code: str,
        wall_time_ms: PositiveCounter = 10_000,
        max_output_chars: PositiveCounter = 16_384,
        max_events: PositiveCounter = 64,
    ) -> ProgramWorkspaceExecuteToolResult:
        operation: OperationRef | None = None
        try:
            if wall_time_ms > context.budget_wall_time_ms:
                raise BudgetDenied("workspace wall-time exceeds the request budget")
            spec = WorkspaceProgramSpec(
                code=code,
                wall_time_ms=wall_time_ms,
                max_output_chars=max_output_chars,
                max_events=max_events,
            )
            payload = {"handle": handle, "kind": "workspace.program.execute", "spec": spec}
            envelope = _envelope(
                host,
                context,
                "workspace.program.execute",
                canonical_sha256(payload),
                workspace=_envelope_handle(handle),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED:
                await asyncio.to_thread(
                    host.program_workspace.attach,
                    handle,
                    SessionRef(value=context.session_id),
                )
                host.registry.begin(operation, host.runtime_generation)
                program_result = await asyncio.to_thread(
                    host.program_workspace.execute,
                    operation,
                    handle,
                    spec,
                )
                record = _finish_program_operation(host, program_result)
            program_result = (
                None
                if record.result_json is None
                else WorkspaceProgramResult.model_validate_json(record.result_json, strict=True)
            )
            return ProgramWorkspaceExecuteToolResult(
                result=program_result,
                operation=_operation_result(record, host=host),
                failure=record.failure,
            )
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return ProgramWorkspaceExecuteToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_program_workspace_inspect",
        description="Inspect bounded variable summaries for an exact programmable revision.",
        annotations=READ_ONLY,
    )
    async def aar_program_workspace_inspect(
        context: McpReadContext,
        handle: ProgrammableWorkspaceHandle,
    ) -> ProgramWorkspaceInspectToolResult:
        try:
            _validate_read_context(host, context)
            handle = await asyncio.to_thread(
                host.program_workspace.attach,
                handle,
                SessionRef(value=context.session_id),
            )
            snapshot = await asyncio.to_thread(host.program_workspace.inspect, handle)
            return ProgramWorkspaceInspectToolResult(snapshot=snapshot)
        except Exception as error:
            return ProgramWorkspaceInspectToolResult(failure=_failure(error))

    @server.tool(
        name="aar_program_workspace_interrupt",
        description="Request host cancellation of a running programmable operation.",
        annotations=MUTATING_CANCEL,
    )
    async def aar_program_workspace_interrupt(
        context: McpMutationContext,
        handle: ProgrammableWorkspaceHandle,
        operation_id: IdentityValue,
    ) -> ProgramWorkspaceInterruptToolResult:
        operation = OperationRef(value=operation_id)
        try:
            _validate_mutation_context(host, context, "workspace.program.interrupt")
            _assert_operation_binding(host, context, operation)
            result = await asyncio.to_thread(
                host.program_workspace.interrupt,
                handle,
                operation,
            )
            return ProgramWorkspaceInterruptToolResult(result=result)
        except Exception as error:
            return ProgramWorkspaceInterruptToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_program_workspace_checkpoint",
        description=(
            "Create a digest-bound portable JSON-subset manifest with explicit exclusions."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_program_workspace_checkpoint(
        context: McpMutationContext,
        handle: ProgrammableWorkspaceHandle,
        max_values: PositiveCounter = 256,
        max_bytes: PositiveCounter = 1_048_576,
        max_depth: PositiveCounter = 12,
        max_collection_items: PositiveCounter = 4_096,
    ) -> ProgramWorkspaceCheckpointToolResult:
        operation: OperationRef | None = None
        try:
            policy = WorkspaceCheckpointPolicy(
                max_values=max_values,
                max_bytes=max_bytes,
                max_depth=max_depth,
                max_collection_items=max_collection_items,
            )
            payload = {
                "handle": handle,
                "kind": "workspace.program.checkpoint",
                "policy": policy,
            }
            envelope = _envelope(
                host,
                context,
                "workspace.program.checkpoint",
                canonical_sha256(payload),
                workspace=_envelope_handle(handle),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED:
                await asyncio.to_thread(
                    host.program_workspace.attach,
                    handle,
                    SessionRef(value=context.session_id),
                )
                host.registry.begin(operation, host.runtime_generation)
                manifest = await asyncio.to_thread(
                    host.program_workspace.checkpoint,
                    operation,
                    handle,
                    policy,
                    envelope.trace_id,
                )
                record = host.registry.succeed_workspace_checkpoint(
                    operation,
                    manifest,
                    host.runtime_generation,
                )
                return ProgramWorkspaceCheckpointToolResult(
                    manifest=manifest,
                    operation=_operation_result(record),
                )

            manifest = (
                None
                if record.result_json is None
                else WorkspaceCheckpointManifest.model_validate_json(
                    record.result_json, strict=True
                )
            )
            return ProgramWorkspaceCheckpointToolResult(
                manifest=manifest,
                operation=_operation_result(record),
                failure=record.failure,
            )
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return ProgramWorkspaceCheckpointToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_program_workspace_restore",
        description="Restore a verified portable checkpoint into a new workspace generation.",
        annotations=MUTATING_DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_program_workspace_restore(
        context: McpMutationContext,
        workspace_id: IdentityValue,
        manifest: WorkspaceCheckpointManifest,
        expected_handle: ProgrammableWorkspaceHandle | None = None,
    ) -> ProgramWorkspaceHandleToolResult:
        operation: OperationRef | None = None
        try:
            target = WorkspaceRef(value=workspace_id)
            spec = WorkspaceRestoreSpec(
                workspace=target,
                session=SessionRef(value=context.session_id),
                expected_handle=expected_handle,
            )
            payload = {"kind": "workspace.program.restore", "manifest": manifest, "spec": spec}
            envelope = _envelope(
                host,
                context,
                "workspace.program.restore",
                canonical_sha256(payload),
                workspace=(None if expected_handle is None else _envelope_handle(expected_handle)),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED:
                host.registry.begin(operation, host.runtime_generation)
                handle = await asyncio.to_thread(
                    host.program_workspace.restore,
                    manifest,
                    spec,
                )
                record = host.registry.succeed(
                    operation,
                    canonical_json_bytes(handle).decode(),
                    host.runtime_generation,
                )
            handle = (
                None
                if record.result_json is None
                else ProgrammableWorkspaceHandle.model_validate_json(
                    record.result_json, strict=True
                )
            )
            return ProgramWorkspaceHandleToolResult(
                handle=handle,
                operation=_operation_result(record),
                failure=record.failure,
            )
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return ProgramWorkspaceHandleToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_program_workspace_health",
        description="Read exact worker readiness, loss, and running-operation state.",
        annotations=READ_ONLY,
    )
    async def aar_program_workspace_health(
        context: McpReadContext,
        handle: ProgrammableWorkspaceHandle,
    ) -> ProgramWorkspaceHealthToolResult:
        try:
            _validate_read_context(host, context)
            health = await asyncio.to_thread(host.program_workspace.health, handle)
            return ProgramWorkspaceHealthToolResult(health=health)
        except Exception as error:
            return ProgramWorkspaceHealthToolResult(failure=_failure(error))

    @server.tool(
        name="aar_program_workspace_reconcile",
        description="Read the backend receipt or explicit running/lost classification.",
        annotations=READ_ONLY,
    )
    async def aar_program_workspace_reconcile(
        context: McpReadContext,
        handle: ProgrammableWorkspaceHandle,
        operation_id: IdentityValue,
    ) -> ProgramWorkspaceReconcileToolResult:
        operation = OperationRef(value=operation_id)
        try:
            request = _assert_operation_binding(host, context, operation)
            if not any(grant.capability == "workspace.program.execute" for grant in request.grants):
                raise WorkspaceBindingDenied("operation is not a programmable workspace execution")
            record = host.registry.get(operation)
            payload = json.loads(record.payload_json)
            stored_handle = ProgrammableWorkspaceHandle.model_validate(
                payload.get("handle"), strict=True
            )
            if record.result_json is not None:
                result = WorkspaceProgramResult.model_validate_json(record.result_json, strict=True)
                completed_handle = ProgrammableWorkspaceHandle(
                    workspace=result.workspace,
                    backend=result.backend,
                    generation=result.generation,
                    revision=result.revision_after,
                )
                if handle != completed_handle:
                    raise WorkspaceBindingDenied(
                        "programmable reconciliation handle does not match the result receipt"
                    )
                return ProgramWorkspaceReconcileToolResult(
                    result=WorkspaceReconciliationResult(
                        operation=operation,
                        backend=result.backend,
                        state="completed",
                        observed_revision=result.revision_after,
                        result=result,
                    )
                )
            if handle != stored_handle:
                raise WorkspaceBindingDenied(
                    "programmable reconciliation handle does not match the accepted intent"
                )
            if record.state is OperationState.INDETERMINATE:
                return ProgramWorkspaceReconcileToolResult(
                    result=WorkspaceReconciliationResult(
                        operation=operation,
                        backend=stored_handle.backend,
                        state="lost",
                        observed_revision=stored_handle.revision,
                    ),
                    failure=_program_receipt_unavailable(operation),
                )
            result = await asyncio.to_thread(
                host.program_workspace.reconcile,
                handle,
                operation,
            )
            return ProgramWorkspaceReconcileToolResult(result=result)
        except Exception as error:
            return ProgramWorkspaceReconcileToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_program_workspace_close",
        description="Close one exact programmable workspace generation without external effects.",
        annotations=MUTATING_DESTRUCTIVE_IDEMPOTENT,
    )
    async def aar_program_workspace_close(
        context: McpMutationContext,
        handle: ProgrammableWorkspaceHandle,
        reason: str,
    ) -> ProgramWorkspaceCloseToolResult:
        operation: OperationRef | None = None
        try:
            payload = {"handle": handle, "kind": "workspace.program.close", "reason": reason}
            envelope = _envelope(
                host,
                context,
                "workspace.program.close",
                canonical_sha256(payload),
                workspace=_envelope_handle(handle),
            )
            record, _created = host.registry.accept(
                envelope, canonical_json_bytes(payload).decode()
            )
            operation = record.operation
            close_result = None
            if record.state is OperationState.ACCEPTED:
                await asyncio.to_thread(
                    host.program_workspace.attach,
                    handle,
                    SessionRef(value=context.session_id),
                )
                host.registry.begin(operation, host.runtime_generation)
                close_result = await asyncio.to_thread(
                    host.program_workspace.close,
                    handle,
                    reason=reason,
                )
                record = host.registry.succeed(
                    operation,
                    canonical_json_bytes(close_result).decode(),
                    host.runtime_generation,
                )
            elif record.result_json is not None:
                close_result = WorkspaceCloseResult.model_validate_json(
                    record.result_json, strict=True
                )
            return ProgramWorkspaceCloseToolResult(
                result=close_result,
                operation=_operation_result(record),
                failure=record.failure,
            )
        except Exception as error:
            failure = _failure(error, operation=operation)
            operation_result = None
            if operation is not None:
                try:
                    record = host.registry.get(operation)
                    if record.state in {OperationState.ACCEPTED, OperationState.RUNNING}:
                        record = host.registry.fail(
                            operation,
                            failure,
                            host.runtime_generation,
                        )
                    operation_result = _operation_result(record)
                except Exception:
                    operation_result = None
            return ProgramWorkspaceCloseToolResult(
                operation=operation_result,
                failure=failure,
            )

    @server.tool(
        name="aar_asset_get",
        description="Read one immutable adaptive asset by its kind and manifest digest.",
        annotations=READ_ONLY,
    )
    async def aar_asset_get(
        context: McpReadContext,
        kind: AssetKind,
        digest: Digest,
    ) -> AssetDocumentToolResult:
        try:
            _validate_read_context(host, context)
            reference = AdaptiveAssetRef(kind=kind, digest=digest)
            document = host.adaptive_assets.get(reference)
            return AssetDocumentToolResult(document=document)
        except Exception as error:
            return AssetDocumentToolResult(failure=_failure(error))

    @server.tool(
        name="aar_asset_export",
        description=(
            "Export a deterministic dependency-closed asset bundle without reading or mutating "
            "host serving state."
        ),
        annotations=READ_ONLY,
    )
    async def aar_asset_export(
        context: McpReadContext,
        roots: list[AdaptiveAssetRef],
        include_events: bool = True,
    ) -> AssetBundleToolResult:
        try:
            _validate_read_context(host, context)
            bundle = host.adaptive_assets.export_bundle(tuple(roots), include_events=include_events)
            return AssetBundleToolResult(bundle=bundle)
        except Exception as error:
            return AssetBundleToolResult(failure=_failure(error))

    @server.tool(
        name="aar_asset_import",
        description=(
            "Validate and atomically import a content-addressed asset bundle. The operation is "
            "idempotent and cannot activate or mutate host serving state."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_asset_import(
        context: McpMutationContext,
        bundle: dict[str, JsonValue],
        start_only: bool = False,
    ) -> OperationToolResult:
        try:
            parsed_bundle = AdaptiveAssetBundle.model_validate_json(
                canonical_json_bytes(bundle), strict=True
            )
            envelope = _envelope(
                host,
                context,
                "asset.import",
                canonical_sha256(parsed_bundle),
            )
            record = host.submit_asset_import(envelope, parsed_bundle)
            if record.state is OperationState.ACCEPTED and not start_only:
                record = host.run_asset_import(record.operation)
            return _operation_result(record, host=host)
        except Exception as error:
            return OperationToolResult(failure=_failure(error))

    @server.tool(
        name="aar_asset_outcome",
        description=(
            "Read an episode outcome as observed or unknown; absence is never converted to a "
            "negative outcome."
        ),
        annotations=READ_ONLY,
    )
    async def aar_asset_outcome(
        context: McpReadContext,
        episode: AdaptiveAssetRef,
    ) -> AssetOutcomeToolResult:
        try:
            _validate_read_context(host, context)
            observation = host.adaptive_assets.outcome(episode)
            return AssetOutcomeToolResult(observation=observation)
        except Exception as error:
            return AssetOutcomeToolResult(failure=_failure(error))

    @server.tool(
        name="aar_rlm_execute",
        description=(
            "Submit a bounded brokered RLM job; start_only retains accepted intent for later "
            "status, cancellation, or execution."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_rlm_execute(
        request: Context,
        context: McpRlmMutationContext,
        query: str,
        strategy: Literal["baseline", "evidence_synthesis"],
        max_steps: int,
        start_only: bool = False,
    ) -> OperationToolResult:
        operation: OperationRef | None = None
        sampling_generation: object | None = None
        try:
            if mcp_sampling_transport is not None:
                capabilities = request.client_capabilities
                if capabilities is None or capabilities.sampling is None:
                    raise ReferenceHostError("MCP client did not authorize sampling")
                if start_only:
                    raise ReferenceHostError(
                        "start_only is unavailable for an operation-scoped MCP sampling route"
                    )
            spec = RlmJobSpec(query=query, strategy=strategy, max_steps=max_steps)
            envelope = _rlm_envelope(host, context, canonical_sha256(spec))
            loop = asyncio.get_running_loop()

            def bind_sampling(candidate: OperationRef) -> None:
                nonlocal sampling_generation
                assert mcp_sampling_transport is not None
                sampling_generation = mcp_sampling_transport.bind(
                    candidate,
                    session=request.session,
                    loop=loop,
                    related_request_id=request.request_id,
                )

            record = host.submit_rlm_durable(
                envelope,
                spec,
                **(
                    {"before_dispatch": bind_sampling} if mcp_sampling_transport is not None else {}
                ),
            )
            operation = record.operation
            if record.state is OperationState.ACCEPTED and not start_only:
                timeout_s = max(
                    0.0,
                    (envelope.deadline_unix_ms - host.now_ms()) / 1000,
                )
                record = await asyncio.to_thread(
                    host.wait_rlm,
                    record.operation,
                    timeout_s=timeout_s,
                )
            return _operation_result(record, host=host)
        except Exception as error:
            return OperationToolResult(failure=_failure(error, operation=operation))
        finally:
            if (
                mcp_sampling_transport is not None
                and operation is not None
                and sampling_generation is not None
            ):
                mcp_sampling_transport.unbind(operation, sampling_generation)

    @server.tool(
        name="aar_rlm_status",
        description="Read the bound RLM job, persisted steps, usage, and terminal trace.",
        annotations=READ_ONLY,
    )
    async def aar_rlm_status(
        context: McpReadContext,
        operation_id: IdentityValue,
    ) -> RlmToolResult:
        operation = OperationRef(value=operation_id)
        try:
            request = _assert_operation_binding(host, context, operation)
            if not any(grant.capability == "rlm.execute" for grant in request.grants):
                raise WorkspaceBindingDenied("operation is not an RLM execution")
            record = host.status(operation)
            return RlmToolResult(
                snapshot=host.rlm_status(operation),
                operation=_operation_result(record),
                failure=record.failure,
            )
        except Exception as error:
            return RlmToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_broker_catalog",
        description=(
            "List only the typed broker methods authorized by one bound operation; detailed "
            "request and response schemas remain deferred."
        ),
        annotations=READ_ONLY,
    )
    async def aar_broker_catalog(
        context: McpReadContext,
        operation_id: IdentityValue,
    ) -> BrokerCatalogToolResult:
        operation = OperationRef(value=operation_id)
        try:
            request = _assert_operation_binding(host, context, operation)
            return BrokerCatalogToolResult(catalog=host.brokers.bind(request, operation).catalog())
        except Exception as error:
            return BrokerCatalogToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_broker_describe",
        description=(
            "Resolve full digest-bound request and response schemas for selected authorized "
            "broker methods."
        ),
        annotations=READ_ONLY,
    )
    async def aar_broker_describe(
        context: McpReadContext,
        operation_id: IdentityValue,
        methods: list[BrokerMethodName],
    ) -> BrokerContractsToolResult:
        operation = OperationRef(value=operation_id)
        try:
            request = _assert_operation_binding(host, context, operation)
            return BrokerContractsToolResult(
                contracts=host.brokers.bind(request, operation).describe(methods)
            )
        except Exception as error:
            return BrokerContractsToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_operation_status",
        description="Read a principal- and session-bound operation state and exact result.",
        annotations=READ_ONLY,
    )
    async def aar_operation_status(
        context: McpReadContext,
        operation_id: IdentityValue,
    ) -> OperationToolResult:
        operation = OperationRef(value=operation_id)
        try:
            _assert_operation_binding(host, context, operation)
            return _operation_result(host.status(operation))
        except Exception as error:
            return OperationToolResult(
                operation=operation,
                failure=_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_operation_events",
        description=(
            "Read an authorized bounded continuity event page after one global sequence cursor; "
            "the read never dispatches or mutates the operation."
        ),
        annotations=READ_ONLY,
    )
    async def aar_operation_events(
        context: McpReadContext,
        operation_id: IdentityValue,
        after_sequence: Revision = 0,
        limit: PositiveCounter = 100,
        max_bytes: PositiveCounter = 262_144,
    ) -> OperationEventsToolResult:
        operation = OperationRef(value=operation_id)
        try:
            _assert_operation_binding(host, context, operation)
            if limit > 500:
                raise BudgetDenied("operation event page limit cannot exceed 500")
            if max_bytes > 262_144:
                raise BudgetDenied("operation event page max_bytes cannot exceed 262144")
            return OperationEventsToolResult(
                page=host.registry.event_page(
                    operation,
                    after_sequence=after_sequence,
                    limit=limit,
                    max_bytes=max_bytes,
                ),
                snapshot=host.registry.continuity_snapshot(operation),
            )
        except Exception as error:
            return OperationEventsToolResult(
                failure=_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_operation_cancel",
        description="Cancel an accepted or running operation without inferring uncertain outcomes.",
        annotations=MUTATING_CANCEL,
    )
    async def aar_operation_cancel(
        context: McpMutationContext,
        operation_id: IdentityValue,
    ) -> OperationToolResult:
        operation = OperationRef(value=operation_id)
        try:
            _validate_mutation_context(host, context, "operation.cancel")
            request = _assert_operation_binding(host, context, operation)
            if any(grant.capability == "workspace.program.execute" for grant in request.grants):
                raise InvalidTransition(
                    "programmable operations require aar_program_workspace_interrupt"
                )
            return _operation_result(
                host.cancel(
                    operation,
                    requested_by_digest=canonical_sha256(
                        {
                            "principal": request.principal,
                            "session": request.session,
                        }
                    ),
                    reason_code="mcp_cancel_requested",
                )
            )
        except Exception as error:
            return OperationToolResult(
                operation=operation,
                failure=_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_operation_reconcile",
        description=(
            "Reconcile an indeterminate operation against authoritative receipts; optional "
            "compensation remains proposal-only."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_operation_reconcile(
        context: McpMutationContext,
        operation_id: IdentityValue,
        propose_compensation: bool = False,
        compensation_grant_id: str | None = None,
    ) -> OperationToolResult:
        operation = OperationRef(value=operation_id)
        try:
            _validate_mutation_context(host, context, "operation.reconcile")
            request = _assert_operation_binding(host, context, operation)
            if compensation_grant_id is not None and not propose_compensation:
                raise ValueError("compensation_grant_id requires propose_compensation=true")
            compensation_grant = (
                _current_reference_grant(
                    host,
                    context,
                    compensation_grant_id,
                    "effect.propose",
                )
                if propose_compensation
                else None
            )
            record = host.registry.get(operation)
            if record.state is OperationState.INDETERMINATE and any(
                grant.capability.startswith("workspace.program.") for grant in request.grants
            ):
                result = _operation_result(record)
                return result.model_copy(
                    update={"failure": _program_receipt_unavailable(operation)}
                )
            if any(grant.capability == "asset.import" for grant in request.grants):
                host.reconcile_asset_import(operation)
            elif any(grant.capability == "rlm.execute" for grant in request.grants):
                if (
                    record.state is OperationState.INDETERMINATE
                    and host.brokers.has_unresolved_calls(operation)
                ):
                    host.reconcile_broker_calls(
                        operation,
                        current_capability_digest=context.capability_digest,
                        current_compensation_grant=compensation_grant,
                        propose_compensation=propose_compensation,
                    )
                host.reconcile_rlm(operation)
            else:
                host.reconcile(operation)
            return _operation_result(host.status(operation), host=host)
        except Exception as error:
            return OperationToolResult(
                operation=operation,
                failure=_failure(error, operation=operation),
            )

    @server.tool(
        name="aar_checkpoint_describe",
        description="Describe checkpoint support without claiming unavailable portability.",
        annotations=READ_ONLY,
    )
    async def aar_checkpoint_describe(
        context: McpReadContext,
    ) -> CheckpointDescribeToolResult:
        try:
            _validate_read_context(host, context)
        except Exception as error:
            failure = _failure(error)
            return CheckpointDescribeToolResult(
                supported=False,
                reason=failure.message,
                failure=failure,
            )
        return CheckpointDescribeToolResult(
            supported=True,
            reason=(
                "AR-1 supports deterministic JSON-subset manifests and reports every excluded "
                "live value; arbitrary pickle data is unsupported."
            ),
            portable_media_types=("application/vnd.aar.workspace-checkpoint.v1+json",),
        )

    @server.tool(
        name="aar_artifact_resolve",
        description="Resolve a bounded content-addressed artifact with explicit disclosure limit.",
        annotations=READ_ONLY,
    )
    async def aar_artifact_resolve(
        context: McpReadContext,
        artifact_id: IdentityValue,
        digest: Digest,
        media_type: MediaType,
        size_bytes: BudgetCounter,
        created_by_operation_id: IdentityValue,
        redacted: bool,
        max_bytes: BudgetCounter,
        allow_redacted: bool = False,
    ) -> ArtifactResolveToolResult:
        operation = OperationRef(value=created_by_operation_id)
        try:
            _validate_read_context(host, context)
            _assert_operation_binding(host, context, operation)
            if size_bytes > max_bytes:
                raise ValueError("artifact exceeds the requested disclosure limit")
            if redacted and not allow_redacted:
                raise ValueError("redacted artifact requires explicit allow_redacted")
            reference = ArtifactReference(
                artifact=ArtifactIdRef(value=artifact_id),
                digest=digest,
                media_type=media_type,
                size_bytes=size_bytes,
                created_by=operation,
                redacted=redacted,
            )
            content = host.artifacts.read(reference)
            return ArtifactResolveToolResult(
                artifact_id=artifact_id,
                digest=reference.digest,
                media_type=reference.media_type,
                size_bytes=reference.size_bytes,
                content_base64=base64.b64encode(content).decode("ascii"),
                redacted=reference.redacted,
            )
        except Exception as error:
            return ArtifactResolveToolResult(failure=_failure(error, operation=operation))

    @server.tool(
        name="aar_rlm_workbench_execute",
        description="Admit or idempotently recover one bounded RLM-native workbench operation.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_rlm_workbench_execute(
        context: dict[str, Any],
        spec: dict[str, Any],
        start_only: bool = False,
    ) -> dict[str, Any]:
        operation: OperationRef | None = None
        try:
            request = RlmWorkbenchExecuteInput.model_validate(
                {"context": context, "spec": spec, "start_only": start_only},
                strict=True,
            )
            workbench_context = McpRlmWorkbenchMutationContext.model_validate(
                request.root["context"], strict=True
            )
            current_capability = _workbench_capability(host)
            try:
                require_fully_configured_workbench_capability(current_capability)
            except ValueError as error:
                raise ReferenceHostError(str(error)) from error
            envelope = _rlm_envelope(
                host,
                workbench_context,
                canonical_sha256(request.root["spec"]),
            )
            record = host.submit_rlm_workbench(envelope, request)
            operation = record.operation
            if not start_only and record.state is OperationState.ACCEPTED:
                if host.dispatcher is None:
                    raise ReferenceHostError("durable workbench dispatch is unavailable")
                timeout_s = max(0.0, (envelope.deadline_unix_ms - host.now_ms()) / 1000)
                await asyncio.to_thread(host.dispatcher.wait, operation, timeout_s=timeout_s)
            if host.rlm_workbench is None:
                raise ReferenceHostError("RLM workbench coordinator is unavailable")
            return _workbench_result(host.rlm_workbench.snapshot(operation))
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation))

    bind_successor_tool_contract(server, "aar_rlm_workbench_execute")

    @server.tool(
        name="aar_rlm_workbench_capabilities",
        description="Read exact v8 workbench and per-method backend availability.",
        annotations=READ_ONLY,
    )
    async def aar_rlm_workbench_capabilities(
        context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            read_context = McpRlmWorkbenchReadContext.model_validate(context, strict=True)
            _validate_read_context(host, read_context)
            return _workbench_result(_workbench_capability(host))
        except Exception as error:
            return _workbench_result(_workbench_failure(error))

    bind_successor_tool_contract(server, "aar_rlm_workbench_capabilities")

    @server.tool(
        name="aar_rlm_workbench_status",
        description="Read the typed workbench phase, tickets and terminal result.",
        annotations=READ_ONLY,
    )
    async def aar_rlm_workbench_status(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_revision: int | None,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            read_context = McpRlmWorkbenchReadContext.model_validate(context, strict=True)
            operation_ref = OperationRef.model_validate(operation, strict=True)
            _assert_workbench_operation_binding(host, read_context, operation_ref)
            if host.rlm_workbench is None:
                raise ReferenceHostError("RLM workbench coordinator is unavailable")
            snapshot = host.rlm_workbench.snapshot(operation_ref)
            if expected_revision is not None and snapshot.root["revision"] != expected_revision:
                raise RlmWorkbenchConflict("workbench revision differs from expected_revision")
            return _workbench_result(snapshot)
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_rlm_workbench_status")

    @server.tool(
        name="aar_broker_work_claim",
        description="Reserve a pre-send physical caller-work attempt.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_broker_work_claim(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_control_revision: int,
        expected_cancellation_revision: int,
        expected_suspension_revision: int,
        expected_cumulative_deadline_unix_ms: int,
        ticket_id: str,
        expected_revision: int,
        ticket_digest: str,
        adapter_id: str,
        adapter_generation: int,
        claim_lease_ms: int,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            operation_ref = OperationRef.model_validate(operation, strict=True)
            payload = {
                "context": context,
                "operation": operation,
                "expected_control_revision": expected_control_revision,
                "expected_cancellation_revision": expected_cancellation_revision,
                "expected_suspension_revision": expected_suspension_revision,
                "expected_cumulative_deadline_unix_ms": expected_cumulative_deadline_unix_ms,
                "ticket_id": ticket_id,
                "expected_revision": expected_revision,
                "ticket_digest": ticket_digest,
                "adapter_id": adapter_id,
                "adapter_generation": adapter_generation,
                "claim_lease_ms": claim_lease_ms,
                "idempotency_key": idempotency_key,
            }
            return await asyncio.to_thread(
                _run_caller_work_command,
                host,
                CallerWorkClaimInput,
                "claim",
                payload,
            )
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_broker_work_claim")

    @server.tool(
        name="aar_broker_work_mark_send_started",
        description="Cross the conservative may-have-sent boundary for one reserved attempt.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_broker_work_mark_send_started(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_control_revision: int,
        expected_cancellation_revision: int,
        expected_suspension_revision: int,
        expected_cumulative_deadline_unix_ms: int,
        ticket_id: str,
        expected_revision: int,
        ticket_digest: str,
        claim_id: str,
        claim_fence: str,
        physical_attempt_id: str,
        expected_claim_expires_at_unix_ms: int,
        provider_or_child_idempotency_key: str,
        sent_request_digest: str,
        lookup_supported: bool,
        cancel_supported: bool,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            operation_ref = OperationRef.model_validate(operation, strict=True)
            payload = {
                "context": context,
                "operation": operation,
                "expected_control_revision": expected_control_revision,
                "expected_cancellation_revision": expected_cancellation_revision,
                "expected_suspension_revision": expected_suspension_revision,
                "expected_cumulative_deadline_unix_ms": expected_cumulative_deadline_unix_ms,
                "ticket_id": ticket_id,
                "expected_revision": expected_revision,
                "ticket_digest": ticket_digest,
                "claim_id": claim_id,
                "claim_fence": claim_fence,
                "physical_attempt_id": physical_attempt_id,
                "expected_claim_expires_at_unix_ms": expected_claim_expires_at_unix_ms,
                "provider_or_child_idempotency_key": provider_or_child_idempotency_key,
                "sent_request_digest": sent_request_digest,
                "lookup_supported": lookup_supported,
                "cancel_supported": cancel_supported,
                "idempotency_key": idempotency_key,
            }
            return await asyncio.to_thread(
                _run_caller_work_command,
                host,
                CallerWorkMarkSendStartedInput,
                "mark_send_started",
                payload,
            )
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_broker_work_mark_send_started")

    @server.tool(
        name="aar_broker_work_cancel_before_send",
        description=(
            "Cancel a caller-work ticket only while the send boundary is certainly uncrossed."
        ),
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_broker_work_cancel_before_send(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_control_revision: int,
        expected_cancellation_revision: int,
        expected_suspension_revision: int,
        expected_cumulative_deadline_unix_ms: int,
        ticket_id: str,
        expected_revision: int,
        ticket_digest: str,
        expected_pre_send_state: str,
        claim_id: str | None,
        claim_fence: str | None,
        physical_attempt_id: str | None,
        expected_claim_expires_at_unix_ms: int | None,
        settled_receipt_digest: str,
        settled_at_unix_ms: int,
        reason: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            operation_ref = OperationRef.model_validate(operation, strict=True)
            payload = {
                "context": context,
                "operation": operation,
                "expected_control_revision": expected_control_revision,
                "expected_cancellation_revision": expected_cancellation_revision,
                "expected_suspension_revision": expected_suspension_revision,
                "expected_cumulative_deadline_unix_ms": expected_cumulative_deadline_unix_ms,
                "ticket_id": ticket_id,
                "expected_revision": expected_revision,
                "ticket_digest": ticket_digest,
                "expected_pre_send_state": expected_pre_send_state,
                "claim_id": claim_id,
                "claim_fence": claim_fence,
                "physical_attempt_id": physical_attempt_id,
                "expected_claim_expires_at_unix_ms": expected_claim_expires_at_unix_ms,
                "settled_receipt_digest": settled_receipt_digest,
                "settled_at_unix_ms": settled_at_unix_ms,
                "reason": reason,
                "idempotency_key": idempotency_key,
            }
            return await asyncio.to_thread(
                _run_caller_work_command,
                host,
                CallerWorkCancelBeforeSendInput,
                "cancel_before_send",
                payload,
            )
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_broker_work_cancel_before_send")

    @server.tool(
        name="aar_broker_work_commit",
        description="Commit one exact host receipt against the may-have-sent attempt.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_broker_work_commit(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_control_revision: int,
        expected_cancellation_revision: int,
        expected_suspension_revision: int,
        expected_cumulative_deadline_unix_ms: int,
        ticket_id: str,
        expected_revision: int,
        ticket_digest: str,
        claim_id: str,
        claim_fence: str,
        physical_attempt_id: str,
        sent_request_digest: str,
        sent_at_unix_ms: int | None,
        provider_or_child_request_id: str | None,
        observation: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            operation_ref = OperationRef.model_validate(operation, strict=True)
            payload = {
                "context": context,
                "operation": operation,
                "expected_control_revision": expected_control_revision,
                "expected_cancellation_revision": expected_cancellation_revision,
                "expected_suspension_revision": expected_suspension_revision,
                "expected_cumulative_deadline_unix_ms": expected_cumulative_deadline_unix_ms,
                "ticket_id": ticket_id,
                "expected_revision": expected_revision,
                "ticket_digest": ticket_digest,
                "claim_id": claim_id,
                "claim_fence": claim_fence,
                "physical_attempt_id": physical_attempt_id,
                "sent_request_digest": sent_request_digest,
                "sent_at_unix_ms": sent_at_unix_ms,
                "provider_or_child_request_id": provider_or_child_request_id,
                "observation": observation,
                "idempotency_key": idempotency_key,
            }
            return await asyncio.to_thread(
                _run_caller_work_command,
                host,
                CallerWorkCommitInput,
                "commit",
                payload,
            )
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_broker_work_commit")

    @server.tool(
        name="aar_broker_work_reconcile",
        description="Reconcile one may-have-sent attempt under durable reconciler fences.",
        annotations=MUTATING_IDEMPOTENT,
    )
    async def aar_broker_work_reconcile(
        context: dict[str, Any],
        operation: dict[str, Any],
        expected_control_revision: int,
        expected_cancellation_revision: int,
        expected_suspension_revision: int,
        expected_cumulative_deadline_unix_ms: int,
        ticket_id: str,
        expected_revision: int,
        ticket_digest: str,
        physical_attempt_id: str,
        candidate_receipt_digest: str | None,
        reconciler_id: str,
        reconciler_generation: int,
        reconcile_fence: str,
        reconciliation_action: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        operation_ref: OperationRef | None = None
        try:
            operation_ref = OperationRef.model_validate(operation, strict=True)
            payload = {
                "context": context,
                "operation": operation,
                "expected_control_revision": expected_control_revision,
                "expected_cancellation_revision": expected_cancellation_revision,
                "expected_suspension_revision": expected_suspension_revision,
                "expected_cumulative_deadline_unix_ms": expected_cumulative_deadline_unix_ms,
                "ticket_id": ticket_id,
                "expected_revision": expected_revision,
                "ticket_digest": ticket_digest,
                "physical_attempt_id": physical_attempt_id,
                "candidate_receipt_digest": candidate_receipt_digest,
                "reconciler_id": reconciler_id,
                "reconciler_generation": reconciler_generation,
                "reconcile_fence": reconcile_fence,
                "reconciliation_action": reconciliation_action,
                "idempotency_key": idempotency_key,
            }
            return await asyncio.to_thread(
                _run_caller_work_command,
                host,
                CallerWorkReconcileInput,
                "reconcile",
                payload,
            )
        except Exception as error:
            return _workbench_result(_workbench_failure(error, operation=operation_ref))

    bind_successor_tool_contract(server, "aar_broker_work_reconcile")

    return AarMcpApplication(
        server=server,
        host=host,
        runtime_ownership=runtime_ownership,
    )


def _default_database() -> Path:
    configured = os.environ.get("AAR_DATABASE")
    if configured:
        return Path(configured)
    return Path.cwd() / ".aar" / "reference.sqlite3"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path)
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--embedded-reference-host",
        action="store_true",
        help=(
            "Run the compatibility reference host in this stdio process; "
            "not an AR-LT2 durability mode."
        ),
    )
    parser.add_argument(
        "--programmable-backend",
        choices=("plain", "ipython"),
        default="ipython",
    )
    parser.add_argument(
        "--hermes-mcp-sampling-luna-max",
        action="store_true",
        help=(
            "DEPRECATED compatibility only: enable the fixed owner-controlled "
            "openai-codex/gpt-5.6-luna/max MCP Sampling route in embedded stdio "
            "reference-host mode. New integrations must use caller-delegated RLM."
        ),
    )
    args = parser.parse_args(argv)
    if args.hermes_mcp_sampling_luna_max and not (
        args.embedded_reference_host or args.database is not None
    ):
        parser.error("Hermes MCP Sampling requires embedded reference-host mode")
    if not args.embedded_reference_host and args.database is None:
        from aar.runtime.supervisor_client import main as supervisor_client_main

        client_args = []
        if args.runtime_home is not None:
            client_args.extend(("--runtime-home", str(args.runtime_home)))
        return supervisor_client_main(client_args)
    if args.runtime_home is not None:
        parser.error("--runtime-home cannot be combined with embedded reference-host mode")
    model_registry = None
    sampling_transport = None
    default_profile = None
    if args.hermes_mcp_sampling_luna_max:
        warnings.warn(
            "--hermes-mcp-sampling-luna-max is deprecated compatibility behavior; "
            "migrate to caller-delegated RLM start/claim/host-execute/commit/status",
            FutureWarning,
            stacklevel=2,
        )
        model_registry, sampling_transport, default_profile = (
            hermes_mcp_sampling_luna_max_registry()
        )
    application = build_server(
        _default_database() if args.database is None else args.database,
        programmable_backend=args.programmable_backend,
        model_broker_registry=model_registry,
        default_model_route_profile=default_profile,
        mcp_sampling_transport=sampling_transport,
    )
    try:
        application.server.run(transport="stdio")
    finally:
        application.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
