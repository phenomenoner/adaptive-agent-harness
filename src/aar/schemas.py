"""Host-neutral public schemas for the AR-0A contract candidate."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from aar.canonical import canonical_sha256
from aar.versions import (
    ARTIFACT_SCHEMA_VERSION,
    BROKER_SCHEMA_VERSION,
    ENVELOPE_SCHEMA_VERSION,
    RUNTIME_SCHEMA_VERSION,
    WORKSPACE_SCHEMA_VERSION,
)

OpaqueToken = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$", strict=True),
]
CapabilityName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$", strict=True),
]
Digest = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$", strict=True)]
MediaType = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*$",
        strict=True,
    ),
]
PositiveCounter = Annotated[int, Field(ge=1, le=9_223_372_036_854_775_807, strict=True)]
Revision = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807, strict=True)]
BudgetCounter = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807, strict=True)]
JsonScalar = str | int | bool | None


class StrictModel(BaseModel):
    """Immutable, deny-unknown base for public AAR contract values."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )


class _TypedRef(StrictModel):
    value: OpaqueToken


class HostRef(_TypedRef):
    type: Literal["host"] = "host"


class PrincipalRef(_TypedRef):
    type: Literal["principal"] = "principal"


class LaneRef(_TypedRef):
    type: Literal["lane"] = "lane"


class SessionRef(_TypedRef):
    type: Literal["session"] = "session"


class WorkspaceRef(_TypedRef):
    type: Literal["workspace"] = "workspace"


class OperationRef(_TypedRef):
    type: Literal["operation"] = "operation"


class ArtifactIdRef(_TypedRef):
    type: Literal["artifact"] = "artifact"


class AccessMode(StrEnum):
    READ = "read"
    WRITE = "write"


class CapabilityLimit(StrictModel):
    name: CapabilityName
    value: BudgetCounter


class CapabilityDescriptor(StrictModel):
    name: CapabilityName
    access: AccessMode
    limits: tuple[CapabilityLimit, ...] = ()

    @model_validator(mode="after")
    def limits_are_canonical(self) -> Self:
        names = [item.name for item in self.limits]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("capability limits must be sorted by unique name")
        return self


class CapabilitySet(StrictModel):
    schema_version: Literal["aar.runtime.v1"] = RUNTIME_SCHEMA_VERSION
    capabilities: tuple[CapabilityDescriptor, ...]
    digest: Digest

    @classmethod
    def issue(cls, capabilities: tuple[CapabilityDescriptor, ...]) -> CapabilitySet:
        payload = {
            "schema_version": RUNTIME_SCHEMA_VERSION,
            "capabilities": [item.model_dump(mode="json") for item in capabilities],
        }
        return cls(capabilities=capabilities, digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def capabilities_are_canonical_and_bound(self) -> Self:
        names = [item.name for item in self.capabilities]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("capabilities must be sorted by unique name")
        payload = {
            "schema_version": self.schema_version,
            "capabilities": [item.model_dump(mode="json") for item in self.capabilities],
        }
        if self.digest != canonical_sha256(payload):
            raise ValueError("capability digest does not match canonical capability bytes")
        return self


class GrantConstraint(StrictModel):
    name: CapabilityName
    value: JsonScalar


class Grant(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    grant_id: OpaqueToken
    capability: CapabilityName
    issued_to: PrincipalRef
    expires_at_unix_ms: PositiveCounter
    constraints: tuple[GrantConstraint, ...] = ()

    @model_validator(mode="after")
    def constraints_are_canonical(self) -> Self:
        names = [item.name for item in self.constraints]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("grant constraints must be sorted by unique name")
        return self


class Budget(StrictModel):
    schema_version: Literal["aar.runtime.v1"] = RUNTIME_SCHEMA_VERSION
    wall_time_ms: BudgetCounter
    model_requests: BudgetCounter = 0
    input_tokens: BudgetCounter = 0
    output_tokens: BudgetCounter = 0
    child_operations: BudgetCounter = 0
    artifact_bytes: BudgetCounter = 0


class RequestEnvelope(StrictModel):
    schema_version: Literal["aar.envelope.v1"] = ENVELOPE_SCHEMA_VERSION
    runtime_schema_version: Literal["aar.runtime.v1"] = RUNTIME_SCHEMA_VERSION
    workspace_schema_version: Literal["aar.workspace.v1"] = WORKSPACE_SCHEMA_VERSION
    artifact_schema_version: Literal["aar.artifact.v1"] = ARTIFACT_SCHEMA_VERSION
    broker_schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    request_id: OpaqueToken
    idempotency_key: Annotated[str, StringConstraints(min_length=8, max_length=128, strict=True)]
    host: HostRef
    principal: PrincipalRef
    lane: LaneRef
    session: SessionRef
    workspace: WorkspaceRef | None = None
    parent_operation: OperationRef | None = None
    runtime_generation: PositiveCounter
    workspace_generation: PositiveCounter | None = None
    expected_workspace_revision: Revision | None = None
    capability_digest: Digest
    deadline_unix_ms: PositiveCounter
    grants: tuple[Grant, ...]
    budget: Budget
    trace_id: OpaqueToken
    input_digest: Digest

    @model_validator(mode="after")
    def bound_context_is_coherent(self) -> Self:
        workspace_fields = (self.workspace_generation, self.expected_workspace_revision)
        if self.workspace is None and any(item is not None for item in workspace_fields):
            raise ValueError("workspace generation and revision require a workspace reference")
        if self.workspace is not None and self.workspace_generation is None:
            raise ValueError("workspace reference requires a workspace generation")
        grant_ids = [grant.grant_id for grant in self.grants]
        if grant_ids != sorted(grant_ids) or len(grant_ids) != len(set(grant_ids)):
            raise ValueError("grants must be sorted by unique grant_id")
        for grant in self.grants:
            if grant.issued_to != self.principal:
                raise ValueError("every grant must be issued to the envelope principal")
            if grant.expires_at_unix_ms < self.deadline_unix_ms:
                raise ValueError("grant expiry cannot precede the request deadline")
        return self


class FailureCategory(StrEnum):
    VALIDATION = "validation"
    AUTHORITY = "authority"
    BUDGET = "budget"
    DEADLINE = "deadline"
    CONFLICT = "conflict"
    STALE = "stale"
    CANCELLED = "cancelled"
    TRANSPORT = "transport"
    WORKER = "worker"
    INTERNAL = "internal"


class OutcomeCertainty(StrEnum):
    CERTAIN = "certain"
    INDETERMINATE = "indeterminate"


class FailureDetail(StrictModel):
    name: CapabilityName
    value: JsonScalar


class FailureEnvelope(StrictModel):
    schema_version: Literal["aar.envelope.v1"] = ENVELOPE_SCHEMA_VERSION
    category: FailureCategory
    code: Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{2,63}$", strict=True)]
    message: Annotated[str, StringConstraints(min_length=1, max_length=512, strict=True)]
    retryable: bool
    certainty: OutcomeCertainty
    operation: OperationRef | None = None
    details: tuple[FailureDetail, ...] = ()

    @model_validator(mode="after")
    def details_are_canonical(self) -> Self:
        names = [item.name for item in self.details]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("failure details must be sorted by unique name")
        return self


class OperationState(StrEnum):
    ACCEPTED = "accepted"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    INDETERMINATE = "indeterminate"


class ReconciliationReport(StrictModel):
    schema_version: Literal["aar.runtime.v1"] = RUNTIME_SCHEMA_VERSION
    operation: OperationRef
    state: OperationState
    certainty: OutcomeCertainty
    runtime_generation: PositiveCounter
    workspace_generation: PositiveCounter | None = None
    observed_revision: Revision | None = None
    reconciliation_required: bool

    @model_validator(mode="after")
    def uncertainty_is_explicit(self) -> Self:
        if self.state is OperationState.INDETERMINATE:
            if self.certainty is not OutcomeCertainty.INDETERMINATE:
                raise ValueError("indeterminate state requires indeterminate certainty")
            if not self.reconciliation_required:
                raise ValueError("indeterminate state requires reconciliation")
        elif self.certainty is OutcomeCertainty.INDETERMINATE:
            raise ValueError("indeterminate certainty requires indeterminate state")
        return self


class ArtifactReference(StrictModel):
    schema_version: Literal["aar.artifact.v1"] = ARTIFACT_SCHEMA_VERSION
    artifact: ArtifactIdRef
    digest: Digest
    media_type: MediaType
    size_bytes: BudgetCounter
    created_by: OperationRef
    redacted: bool


class SecurityBoundary(StrictModel):
    schema_version: Literal["aar.runtime.v1"] = RUNTIME_SCHEMA_VERSION
    runtime_responsibilities: tuple[CapabilityName, ...]
    host_authority: tuple[CapabilityName, ...]
    forbidden_in_runtime: tuple[CapabilityName, ...]

    _SETS: ClassVar = (
        "runtime_responsibilities",
        "host_authority",
        "forbidden_in_runtime",
    )

    @model_validator(mode="after")
    def entries_are_canonical(self) -> Self:
        for field_name in self._SETS:
            values = getattr(self, field_name)
            if list(values) != sorted(values) or len(values) != len(set(values)):
                raise ValueError(f"{field_name} must be sorted and unique")
        return self


SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "artifact_reference": ArtifactReference,
    "budget": Budget,
    "capability_set": CapabilitySet,
    "failure_envelope": FailureEnvelope,
    "grant": Grant,
    "reconciliation_report": ReconciliationReport,
    "request_envelope": RequestEnvelope,
    "security_boundary": SecurityBoundary,
}
