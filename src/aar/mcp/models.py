"""Structured MCP inputs and outputs bound to the public AAR operation surface."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, model_validator

from aar.asset_models import (
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    OutcomeObservation,
)
from aar.broker_models import BrokerCatalog, BrokerContractSet, ModelRouteCatalog
from aar.continuity_models import OperationContinuitySnapshotV1, OperationEventPageV1
from aar.rlm_models import RlmJobSnapshot
from aar.runtime.models import (
    OperationRecord,
    RuntimeReady,
    WorkspaceHandle,
    WorkspaceSnapshot,
)
from aar.runtime.workspace_models import (
    ProgrammableWorkspaceHandle,
    ProgrammableWorkspaceSnapshot,
    WorkspaceCheckpointManifest,
    WorkspaceCloseResult,
    WorkspaceHealth,
    WorkspaceInterruptResult,
    WorkspaceProgramResult,
    WorkspaceReconciliationResult,
)
from aar.schemas import (
    ArtifactReference,
    Digest,
    FailureEnvelope,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PositiveCounter,
    Revision,
    StrictModel,
)

IdentityValue = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")]
ReferenceContextKey = Annotated[
    str,
    Field(
        min_length=1,
        max_length=96,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,95}$",
        description=(
            "Stable label for one exact mutation payload; choose a different key for a "
            "different mutation."
        ),
    ),
]


class McpReadContext(StrictModel):
    """Flat read context copied from current capabilities and host identity."""

    runtime_generation: Annotated[
        PositiveCounter,
        Field(
            description=(
                "Copy ready.runtime_generation from the most recent aar_capabilities response; "
                "refresh it after an AAR server restart."
            )
        ),
    ]
    capability_digest: Annotated[
        Digest,
        Field(
            description=(
                "Copy ready.capabilities.digest from the same aar_capabilities response as "
                "runtime_generation."
            )
        ),
    ]
    principal_id: Annotated[
        IdentityValue,
        Field(description="Use the host-provided principal identity for this lifecycle."),
    ]
    session_id: Annotated[
        IdentityValue,
        Field(
            description=(
                "Use the host-provided session identity and keep it stable for handles and "
                "operations created in this lifecycle."
            )
        ),
    ]
    deadline_unix_ms: Annotated[
        PositiveCounter,
        Field(
            description=(
                "Absolute Unix time in milliseconds, bounded from "
                "aar_capabilities.server_now_unix_ms."
            )
        ),
    ]


class McpMutationContext(McpReadContext):
    """Flat mutation context; grant and budget values are never nested objects."""

    request_id: Annotated[
        IdentityValue,
        Field(description="Use a new host-scoped request identity for this mutation."),
    ]
    idempotency_key: Annotated[
        str,
        Field(
            min_length=8,
            max_length=128,
            description=("Use a new key, or reuse one only for the exact same mutation payload."),
        ),
    ]
    grant_id: Annotated[
        IdentityValue,
        Field(
            description=(
                "Copy the grant_id whose capability matches this tool from the current host's "
                "reference_grants; supply this string directly, not a nested grant object."
            )
        ),
    ]
    budget_wall_time_ms: Annotated[
        PositiveCounter,
        Field(
            description=(
                "Flat wall-time budget in milliseconds, at most 60000 and no later than the "
                "deadline window."
            )
        ),
    ]

    @model_validator(mode="after")
    def budget_is_bounded(self) -> Self:
        if self.budget_wall_time_ms > 60_000:
            raise ValueError("reference-host wall-time budget cannot exceed 60000 ms")
        return self


class McpRlmMutationContext(McpReadContext):
    """Flat brokered-RLM mutation context with explicit grant IDs and budgets."""

    request_id: Annotated[
        IdentityValue,
        Field(description="Use a new host-scoped request identity for this RLM mutation."),
    ]
    idempotency_key: Annotated[
        str,
        Field(
            min_length=8,
            max_length=128,
            description=(
                "Use a new key, or reuse one only for the exact same RLM mutation payload."
            ),
        ),
    ]
    grant_ids: Annotated[
        list[IdentityValue],
        Field(
            description=(
                "Sorted unique grant_id strings copied from the current host's reference_grants; "
                "do not supply grant objects."
            )
        ),
    ]
    budget_wall_time_ms: Annotated[
        PositiveCounter,
        Field(
            description=(
                "Flat wall-time budget in milliseconds, at most 60000 and no later than the "
                "deadline window."
            )
        ),
    ]
    budget_model_requests: int = Field(
        ge=0,
        le=16,
        strict=True,
        description="Maximum brokered model requests for this RLM operation.",
    )
    budget_input_tokens: int = Field(
        ge=0,
        strict=True,
        description="Maximum brokered model input tokens for this RLM operation.",
    )
    budget_output_tokens: int = Field(
        ge=0,
        strict=True,
        description="Maximum brokered model output tokens for this RLM operation.",
    )
    budget_child_operations: int = Field(
        ge=0,
        strict=True,
        description="Maximum retained child operations for this RLM operation.",
    )
    budget_artifact_bytes: int = Field(
        ge=0,
        strict=True,
        description="Maximum brokered artifact bytes for this RLM operation.",
    )

    @model_validator(mode="after")
    def grants_and_budget_are_bounded(self) -> Self:
        if list(self.grant_ids) != sorted(self.grant_ids):
            raise ValueError("RLM grant_ids must be sorted")
        if len(self.grant_ids) != len(set(self.grant_ids)):
            raise ValueError("RLM grant_ids must be unique")
        if self.budget_wall_time_ms > 60_000:
            raise ValueError("reference-host wall-time budget cannot exceed 60000 ms")
        return self


class McpRlmWorkbenchReadContext(StrictModel):
    """Successor-only v8 read context without changing the frozen v7 context model."""

    principal_id: IdentityValue
    session_id: IdentityValue
    runtime_generation: PositiveCounter
    capability_digest: Digest
    deadline_unix_ms: Annotated[int, Field(ge=0, strict=True)]


class McpRlmWorkbenchMutationContext(McpRlmWorkbenchReadContext):
    """Exact v8 workbench mutation authority and cumulative budgets."""

    schema_version: Literal["aar.mcp-rlm-workbench-context.v1"] = "aar.mcp-rlm-workbench-context.v1"
    request_id: IdentityValue
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]
    grant_ids: Annotated[list[IdentityValue], Field(min_length=1, max_length=64)]
    budget_wall_time_ms: Annotated[int, Field(ge=1_000, le=900_000, strict=True)]
    budget_model_requests: Annotated[int, Field(ge=1, le=128, strict=True)]
    budget_input_tokens: Annotated[int, Field(ge=0, strict=True)]
    budget_output_tokens: Annotated[int, Field(ge=0, strict=True)]
    budget_child_operations: Annotated[int, Field(ge=0, le=64, strict=True)]
    budget_artifact_bytes: Annotated[int, Field(ge=0, le=33_554_432, strict=True)]

    @model_validator(mode="after")
    def grants_are_sorted_and_unique(self) -> Self:
        if self.grant_ids != sorted(self.grant_ids):
            raise ValueError("workbench grant_ids must be sorted")
        if len(self.grant_ids) != len(set(self.grant_ids)):
            raise ValueError("workbench grant_ids must be unique")
        return self


McpReadContextArgument = Annotated[
    McpReadContext,
    Field(
        description=(
            'Required outer argument name: "context". Pass the flat McpReadContext object shown '
            'here; do not rename the argument or add "schema_version".'
        )
    ),
]
McpMutationContextArgument = Annotated[
    McpMutationContext,
    Field(
        description=(
            'Required outer argument name: "context". Pass the flat McpMutationContext object '
            'shown here; never use "mutation_context", add "schema_version", or nest "grant" '
            'or "budget".'
        )
    ),
]
McpRlmMutationContextArgument = Annotated[
    McpRlmMutationContext,
    Field(
        description=(
            'Required outer argument name: "context". Pass the flat McpRlmMutationContext '
            'object shown here; never use "mutation_context", add "schema_version", or nest '
            "grant or budget objects."
        )
    ),
]


class ReferenceGrantDescriptor(StrictModel):
    capability: str
    grant_id: IdentityValue


class ReferenceContextToolResult(StrictModel):
    read_context: McpReadContext | None = Field(
        default=None,
        description=(
            'Copy this flat object unchanged into the outer "context" argument of a read tool.'
        ),
    )
    context: McpMutationContext | None = Field(
        default=None,
        description=(
            'Copy this flat object unchanged into the outer "context" argument of the matching '
            "mutation tool."
        ),
    )
    failure: FailureEnvelope | None = None


class OperationToolResult(StrictModel):
    operation: OperationRef | None = None
    state: OperationState | None = None
    certainty: OutcomeCertainty | None = None
    record_revision: Revision | None = None
    reconciliation_required: bool = False
    result: JsonValue | None = None
    artifacts: tuple[ArtifactReference, ...] = ()
    failure: FailureEnvelope | None = None

    @classmethod
    def from_record(
        cls,
        record: OperationRecord,
        *,
        artifacts: tuple[ArtifactReference, ...] = (),
    ) -> OperationToolResult:
        import json

        result = None if record.result_json is None else json.loads(record.result_json)
        return cls(
            operation=record.operation,
            state=record.state,
            certainty=record.certainty,
            record_revision=record.record_revision,
            reconciliation_required=record.reconciliation_required,
            result=result,
            artifacts=artifacts,
            failure=record.failure,
        )


class OperationEventsToolResult(StrictModel):
    page: OperationEventPageV1 | None = None
    snapshot: OperationContinuitySnapshotV1 | None = None
    failure: FailureEnvelope | None = None


class SupervisorCapabilityProjection(StrictModel):
    mode: Literal["embedded-reference-host", "attached-supervisor"]
    frontend_ephemeral: bool
    dispatcher_generation: PositiveCounter
    supervisor_version: str | None = None
    protocol_version: str | None = None
    protocol_digest: Digest | None = None
    process_identity_digest: Digest | None = None


class ModelBrokerCapabilityProjection(StrictModel):
    """Credential-free configuration health; provider connectivity is not inferred."""

    configured: bool
    provider_connectivity: Literal["not_probed"] = "not_probed"
    default_profile_id: str | None = None
    catalog_digest: Digest | None = None
    route_profile_count: Annotated[int, Field(ge=0, le=64, strict=True)] = 0
    journal_schema_versions: tuple[PositiveCounter, ...] = ()

    @model_validator(mode="after")
    def configuration_evidence_is_consistent(self) -> Self:
        if self.configured:
            if self.default_profile_id is None or self.catalog_digest is None:
                raise ValueError("configured model broker requires route authority evidence")
            if self.route_profile_count < 1 or not self.journal_schema_versions:
                raise ValueError(
                    "configured model broker requires bounded route and journal evidence"
                )
        elif any(
            (
                self.default_profile_id is not None,
                self.catalog_digest is not None,
                self.route_profile_count != 0,
                bool(self.journal_schema_versions),
            )
        ):
            raise ValueError("unconfigured model broker cannot publish route authority evidence")
        return self


class CapabilitiesToolResult(StrictModel):
    server_name: str
    server_now_unix_ms: PositiveCounter
    package_version: str
    sdk_name: str
    sdk_version: str
    protocol_versions: tuple[str, ...]
    negotiated_protocol_version: str
    tool_surface_version: str
    tool_surface_digest: Digest
    operation_skill_version: str
    operation_skill_digest: Digest
    schema_versions: tuple[tuple[str, str], ...]
    schema_bundle_digest: Digest
    fixture_set_digest: Digest
    model_routes: ModelRouteCatalog | None = None
    model_broker: ModelBrokerCapabilityProjection
    ready: RuntimeReady
    supervisor: SupervisorCapabilityProjection
    tool_names: tuple[str, ...]
    reference_grants: tuple[ReferenceGrantDescriptor, ...]
    unsupported_capabilities: tuple[str, ...]
    authority_statement: str


class WorkspaceHandleToolResult(StrictModel):
    handle: WorkspaceHandle | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceHandleToolResult(StrictModel):
    handle: ProgrammableWorkspaceHandle | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class WorkspaceInspectToolResult(StrictModel):
    snapshot: WorkspaceSnapshot | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceExecuteToolResult(StrictModel):
    result: WorkspaceProgramResult | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceInspectToolResult(StrictModel):
    snapshot: ProgrammableWorkspaceSnapshot | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceCheckpointToolResult(StrictModel):
    manifest: WorkspaceCheckpointManifest | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceHealthToolResult(StrictModel):
    health: WorkspaceHealth | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceInterruptToolResult(StrictModel):
    result: WorkspaceInterruptResult | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceReconcileToolResult(StrictModel):
    result: WorkspaceReconciliationResult | None = None
    failure: FailureEnvelope | None = None


class ProgramWorkspaceCloseToolResult(StrictModel):
    result: WorkspaceCloseResult | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class CheckpointDescribeToolResult(StrictModel):
    supported: bool
    reason: str
    portable_media_types: tuple[str, ...] = ()
    failure: FailureEnvelope | None = None


class ArtifactResolveToolResult(StrictModel):
    artifact_id: str | None = None
    digest: Digest | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    content_base64: str | None = None
    redacted: bool | None = None
    failure: FailureEnvelope | None = None


class AssetDocumentToolResult(StrictModel):
    document: AdaptiveAssetDocument | None = None
    failure: FailureEnvelope | None = None


class AssetBundleToolResult(StrictModel):
    bundle: AdaptiveAssetBundle | None = None
    failure: FailureEnvelope | None = None


class AssetOutcomeToolResult(StrictModel):
    observation: OutcomeObservation | None = None
    failure: FailureEnvelope | None = None


class RlmToolResult(StrictModel):
    snapshot: RlmJobSnapshot | None = None
    operation: OperationToolResult | None = None
    failure: FailureEnvelope | None = None


class BrokerCatalogToolResult(StrictModel):
    catalog: BrokerCatalog | None = None
    failure: FailureEnvelope | None = None


class BrokerContractsToolResult(StrictModel):
    contracts: BrokerContractSet | None = None
    failure: FailureEnvelope | None = None
