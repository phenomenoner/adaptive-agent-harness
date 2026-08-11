"""Typed direct-SDK values for the deterministic reference host."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aar.schemas import (
    CapabilitySet,
    Digest,
    FailureEnvelope,
    JsonScalar,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PositiveCounter,
    Revision,
    StrictModel,
    WorkspaceRef,
)


class RuntimeReady(StrictModel):
    runtime_generation: PositiveCounter
    capabilities: CapabilitySet


class WorkspaceHandle(StrictModel):
    workspace: WorkspaceRef
    generation: PositiveCounter
    revision: Revision


class WorkspaceExecuteSpec(StrictModel):
    workspace: WorkspaceRef
    expected_generation: PositiveCounter
    expected_revision: Revision
    action: Literal["set", "delete", "increment"]
    key: Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$", strict=True)]
    value: JsonScalar = None

    @model_validator(mode="after")
    def action_value_is_coherent(self) -> Self:
        if self.action == "delete" and self.value is not None:
            raise ValueError("delete does not accept a value")
        if self.action == "increment" and (
            isinstance(self.value, bool) or not isinstance(self.value, int)
        ):
            raise ValueError("increment requires an integer value")
        return self


class WorkspaceExecutionResult(StrictModel):
    workspace: WorkspaceRef
    generation: PositiveCounter
    revision_before: Revision
    revision_after: Revision
    action: Literal["set", "delete", "increment"]
    key: str
    previous: JsonScalar = None
    value: JsonScalar = None


class WorkspaceSnapshot(StrictModel):
    workspace: WorkspaceRef
    generation: PositiveCounter
    revision: Revision
    values: tuple[tuple[str, JsonScalar], ...]


class OperationRecord(StrictModel):
    operation: OperationRef
    host_value: str
    principal_value: str
    idempotency_key: str
    input_digest: Digest
    state: OperationState
    certainty: OutcomeCertainty
    runtime_generation: PositiveCounter
    record_revision: Revision
    reconciliation_required: bool
    request_json: str
    payload_json: str
    result_json: str | None = None
    failure: FailureEnvelope | None = None
    created_at_unix_ms: PositiveCounter
    updated_at_unix_ms: PositiveCounter


class OperationEvent(StrictModel):
    sequence: PositiveCounter
    operation: OperationRef
    state: OperationState
    certainty: OutcomeCertainty
    record_revision: Revision
    at_unix_ms: PositiveCounter
    note: str
