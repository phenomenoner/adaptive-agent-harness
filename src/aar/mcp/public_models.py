from __future__ import annotations

from pydantic import JsonValue

from aar.schemas import (
    ArtifactReference,
    Digest,
    FailureEnvelope,
    OperationState,
    OutcomeCertainty,
    Revision,
    StrictModel,
)


class PublicCapabilitiesResult(StrictModel):
    server_name: str
    package_version: str
    tool_surface_version: str
    tool_surface_digest: Digest
    tool_names: tuple[str, ...]
    authority_statement: str
    tenant_isolation: str
    limits: dict[str, int]
    unsupported_capabilities: tuple[str, ...]


class PublicWorkspaceHandle(StrictModel):
    workspace_id: str
    generation: int
    revision: Revision


class PublicWorkspaceHandleResult(StrictModel):
    handle: PublicWorkspaceHandle | None = None
    failure: FailureEnvelope | None = None


class PublicWorkspaceInspectResult(StrictModel):
    handle: PublicWorkspaceHandle | None = None
    values: dict[str, JsonValue] | None = None
    failure: FailureEnvelope | None = None


class PublicOperationResult(StrictModel):
    operation_id: str | None = None
    state: OperationState | None = None
    certainty: OutcomeCertainty | None = None
    record_revision: Revision | None = None
    reconciliation_required: bool = False
    result: JsonValue | None = None
    artifacts: tuple[ArtifactReference, ...] = ()
    failure: FailureEnvelope | None = None


class PublicArtifactResult(StrictModel):
    artifact_id: str | None = None
    digest: Digest | None = None
    media_type: str | None = None
    size_bytes: int | None = None
    content_base64: str | None = None
    redacted: bool | None = None
    failure: FailureEnvelope | None = None
