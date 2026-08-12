"""Backend-neutral programmable workspace values for AR-1."""

from __future__ import annotations

import platform
import sys
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, field_validator, model_validator

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.schemas import (
    ArtifactReference,
    Digest,
    OpaqueToken,
    OperationRef,
    PositiveCounter,
    Revision,
    SessionRef,
    StrictModel,
    WorkspaceRef,
)

WorkspaceExecutionStatus = Literal["succeeded", "failed", "interrupted", "timed_out"]
WorkspaceEventKind = Literal[
    "stdout", "stderr", "display", "exception", "progress", "artifact"
]
WorkspaceHealthStatus = Literal["ready", "busy", "closed", "lost"]
WorkspaceBackendKind = Literal["plain-python", "ipython"]
WorkspaceCheckpointFormat = Literal["aar.workspace-checkpoint.v1"]


class WorkspaceBackendDescriptor(StrictModel):
    kind: WorkspaceBackendKind
    version: Annotated[str, Field(min_length=1, max_length=64, strict=True)]
    capability_digest: Digest
    checkpoint_formats: tuple[WorkspaceCheckpointFormat, ...]
    features: tuple[Annotated[str, Field(min_length=1, max_length=128, strict=True)], ...]

    @field_validator("checkpoint_formats", "features", mode="before")
    @classmethod
    def json_arrays_are_normalized(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @classmethod
    def issue(
        cls,
        *,
        kind: WorkspaceBackendKind,
        version: str,
        checkpoint_formats: tuple[WorkspaceCheckpointFormat, ...],
        features: tuple[str, ...],
    ) -> WorkspaceBackendDescriptor:
        payload = {
            "kind": kind,
            "version": version,
            "checkpoint_formats": tuple(sorted(checkpoint_formats)),
            "features": tuple(sorted(features)),
        }
        return cls(**payload, capability_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def entries_are_canonical_and_digest_matches(self) -> Self:
        if list(self.checkpoint_formats) != sorted(set(self.checkpoint_formats)):
            raise ValueError("checkpoint formats must be sorted and unique")
        if list(self.features) != sorted(set(self.features)):
            raise ValueError("workspace features must be sorted and unique")
        payload = self.model_dump(mode="python", exclude={"capability_digest"})
        if self.capability_digest != canonical_sha256(payload):
            raise ValueError("workspace backend capability digest mismatch")
        return self


class ProgrammableWorkspaceHandle(StrictModel):
    workspace: WorkspaceRef
    backend: WorkspaceBackendDescriptor
    generation: PositiveCounter
    revision: Revision


class WorkspaceEnvironmentFingerprint(StrictModel):
    entries: tuple[tuple[str, str], ...]
    digest: Digest

    @field_validator("entries", mode="before")
    @classmethod
    def json_arrays_are_normalized(cls, value):
        if isinstance(value, list):
            return tuple(tuple(item) for item in value)
        return value

    @classmethod
    def current(
        cls,
        *,
        extra: tuple[tuple[str, str], ...] = (),
    ) -> WorkspaceEnvironmentFingerprint:
        entries = tuple(
            sorted(
                (
                    ("platform_machine", platform.machine() or "unknown"),
                    ("platform_system", platform.system() or "unknown"),
                    ("python_implementation", sys.implementation.name),
                    ("python_version", platform.python_version()),
                    *extra,
                )
            )
        )
        return cls(entries=entries, digest=canonical_sha256(entries))

    @model_validator(mode="after")
    def entries_are_canonical_and_digest_matches(self) -> Self:
        names = [name for name, _value in self.entries]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("environment entries must be sorted by unique name")
        if self.digest != canonical_sha256(self.entries):
            raise ValueError("workspace environment digest mismatch")
        return self


class WorkspaceProgramSpec(StrictModel):
    code: Annotated[str, Field(min_length=1, max_length=65_536, strict=True)]
    wall_time_ms: Annotated[int, Field(ge=1, le=60_000, strict=True)] = 10_000
    max_output_chars: Annotated[int, Field(ge=256, le=65_536, strict=True)] = 16_384
    max_events: Annotated[int, Field(ge=8, le=256, strict=True)] = 64
    checkpoint_replay_safe: bool = False


class WorkspaceEvent(StrictModel):
    sequence: PositiveCounter
    kind: WorkspaceEventKind
    text: Annotated[str, Field(max_length=8_192, strict=True)] | None = None
    data: JsonValue | None = None
    artifact: ArtifactReference | None = None
    truncated: bool = False

    @model_validator(mode="after")
    def payload_matches_kind(self) -> Self:
        if self.kind in {"stdout", "stderr"}:
            if self.text is None or self.data is not None or self.artifact is not None:
                raise ValueError("stream events require only text")
        elif self.kind == "artifact":
            if self.artifact is None or self.text is not None or self.data is not None:
                raise ValueError("artifact events require only an artifact reference")
        elif self.data is None or self.text is not None or self.artifact is not None:
            raise ValueError(f"{self.kind} events require only structured data")
        if self.data is not None and len(canonical_json_bytes(self.data)) > 16_384:
            raise ValueError("structured event data exceeds 16384 bytes")
        return self


class WorkspaceProgramResult(StrictModel):
    operation: OperationRef
    workspace: WorkspaceRef
    backend: WorkspaceBackendDescriptor
    generation: PositiveCounter
    revision_before: Revision
    revision_after: Revision
    status: WorkspaceExecutionStatus
    workspace_lost: bool = False
    result: JsonValue | None = None
    result_excluded_reason: str | None = None
    events: tuple[WorkspaceEvent, ...]

    @model_validator(mode="after")
    def result_is_portable_and_revision_advances(self) -> Self:
        if self.revision_after != self.revision_before + 1:
            raise ValueError("executed programs must advance exactly one revision")
        if self.result is not None and len(canonical_json_bytes(self.result)) > 65_536:
            raise ValueError("portable result exceeds 65536 bytes")
        if self.result is not None and self.result_excluded_reason is not None:
            raise ValueError("portable result cannot also be excluded")
        if [event.sequence for event in self.events] != list(range(1, len(self.events) + 1)):
            raise ValueError("workspace event sequences must be contiguous")
        return self


class WorkspaceVariableSummary(StrictModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", strict=True)]
    type_name: Annotated[str, Field(min_length=1, max_length=128, strict=True)]
    portable: bool
    preview: Annotated[str, Field(max_length=256, strict=True)]


class ProgrammableWorkspaceSnapshot(StrictModel):
    workspace: WorkspaceRef
    backend: WorkspaceBackendDescriptor
    generation: PositiveCounter
    revision: Revision
    variables: tuple[WorkspaceVariableSummary, ...]


class WorkspaceCheckpointPolicy(StrictModel):
    max_values: Annotated[int, Field(ge=1, le=1_024, strict=True)] = 256
    max_bytes: Annotated[int, Field(ge=1_024, le=16_777_216, strict=True)] = 1_048_576
    max_depth: Annotated[int, Field(ge=1, le=32, strict=True)] = 12
    max_collection_items: Annotated[int, Field(ge=1, le=65_536, strict=True)] = 4_096


class WorkspaceCheckpointValue(StrictModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", strict=True)]
    value: JsonValue

    @model_validator(mode="after")
    def value_is_portable(self) -> Self:
        canonical_json_bytes(self.value)
        return self


class WorkspaceCheckpointExclusion(StrictModel):
    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", strict=True)]
    type_name: Annotated[str, Field(min_length=1, max_length=128, strict=True)]
    reason: Annotated[str, Field(min_length=1, max_length=256, strict=True)]


class WorkspaceCheckpointManifest(StrictModel):
    schema_version: Literal["aar.workspace-checkpoint.v1"] = "aar.workspace-checkpoint.v1"
    source_handle: ProgrammableWorkspaceHandle
    creation_operation: OperationRef
    trace_id: OpaqueToken
    environment: WorkspaceEnvironmentFingerprint
    values: tuple[WorkspaceCheckpointValue, ...]
    exclusions: tuple[WorkspaceCheckpointExclusion, ...]
    artifacts: tuple[ArtifactReference, ...] = ()
    content_digest: Digest

    @field_validator("values", "exclusions", "artifacts", mode="before")
    @classmethod
    def json_arrays_are_normalized(cls, value):
        return tuple(value) if isinstance(value, list) else value

    @classmethod
    def issue(
        cls,
        *,
        source_handle: ProgrammableWorkspaceHandle,
        creation_operation: OperationRef,
        trace_id: str,
        environment: WorkspaceEnvironmentFingerprint,
        values: tuple[WorkspaceCheckpointValue, ...],
        exclusions: tuple[WorkspaceCheckpointExclusion, ...],
        artifacts: tuple[ArtifactReference, ...] = (),
    ) -> WorkspaceCheckpointManifest:
        payload = {
            "schema_version": "aar.workspace-checkpoint.v1",
            "source_handle": source_handle,
            "creation_operation": creation_operation,
            "trace_id": trace_id,
            "environment": environment,
            "values": values,
            "exclusions": exclusions,
            "artifacts": artifacts,
        }
        return cls(
            **payload,
            content_digest=canonical_sha256(payload),
        )

    @model_validator(mode="after")
    def entries_are_canonical_and_digest_matches(self) -> Self:
        value_names = [item.name for item in self.values]
        excluded_names = [item.name for item in self.exclusions]
        if value_names != sorted(value_names) or len(value_names) != len(set(value_names)):
            raise ValueError("checkpoint values must be sorted and unique")
        if excluded_names != sorted(excluded_names) or len(excluded_names) != len(
            set(excluded_names)
        ):
            raise ValueError("checkpoint exclusions must be sorted and unique")
        if set(value_names) & set(excluded_names):
            raise ValueError("checkpoint names cannot be both included and excluded")
        payload = self.model_dump(mode="python", exclude={"content_digest"})
        if self.content_digest != canonical_sha256(payload):
            raise ValueError("checkpoint content digest mismatch")
        return self


class WorkspaceRestoreSpec(StrictModel):
    workspace: WorkspaceRef
    session: SessionRef
    expected_handle: ProgrammableWorkspaceHandle | None = None
    recover_lost_generation: bool = False

    @model_validator(mode="after")
    def handle_targets_workspace(self) -> Self:
        if self.expected_handle is not None and self.expected_handle.workspace != self.workspace:
            raise ValueError("restore handle must target the requested workspace")
        if self.recover_lost_generation and self.expected_handle is None:
            raise ValueError("lost-generation recovery requires an exact expected handle")
        return self


class WorkspaceHealth(StrictModel):
    workspace: WorkspaceRef
    backend: WorkspaceBackendDescriptor
    generation: PositiveCounter
    revision: Revision
    status: WorkspaceHealthStatus
    running_operation: OperationRef | None = None


class WorkspaceInterruptResult(StrictModel):
    operation: OperationRef
    accepted: bool
    reason: str


class WorkspaceCloseResult(StrictModel):
    handle: ProgrammableWorkspaceHandle
    closed: bool
    reason: Annotated[str, Field(min_length=1, max_length=256, strict=True)]


class WorkspaceReconciliationResult(StrictModel):
    operation: OperationRef
    backend: WorkspaceBackendDescriptor
    state: Literal["running", "completed", "lost"]
    observed_revision: Revision
    result: WorkspaceProgramResult | None = None
