"""Transport-neutral broker contract models and catalog metadata."""

from __future__ import annotations

import base64
from typing import Annotated, Literal

from pydantic import Field, model_validator

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.schemas import (
    AccessMode,
    ArtifactReference,
    BudgetCounter,
    CapabilityName,
    Digest,
    MediaType,
    OperationRef,
    PositiveCounter,
    StrictModel,
)
from aar.versions import BROKER_SCHEMA_VERSION

BrokerMethodName = Literal[
    "artifact.put",
    "artifact.read",
    "effect.propose",
    "evidence.query",
    "model.request",
    "subagent.result",
    "subagent.submit",
]


class BrokerContext(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    parent_operation: OperationRef
    grant_id: str
    deadline_unix_ms: int
    idempotency_key: str


class BrokerReceipt(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    kind: str
    handle: str
    digest: str
    value: str


class BrokerUsage(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    model_requests: BudgetCounter = 0
    input_tokens: BudgetCounter = 0
    output_tokens: BudgetCounter = 0
    child_operations: BudgetCounter = 0
    artifact_bytes: BudgetCounter = 0

    def plus(self, other: BrokerUsage) -> BrokerUsage:
        return BrokerUsage(
            model_requests=self.model_requests + other.model_requests,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            child_operations=self.child_operations + other.child_operations,
            artifact_bytes=self.artifact_bytes + other.artifact_bytes,
        )


class ModelRequest(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    prompt: Annotated[str, Field(min_length=1, max_length=65_536, strict=True)]


class EvidenceQuery(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    text: Annotated[str, Field(min_length=1, max_length=8_192, strict=True)]


class SubagentSubmit(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    task: Annotated[str, Field(min_length=1, max_length=16_384, strict=True)]


class RetainedSubagentHandle(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    handle: str
    state: Literal["retained"] = "retained"


class SubagentResultRequest(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    handle: str


class EffectProposal(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    effect_type: CapabilityName
    payload_digest: Digest


class ArtifactPutRequest(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    content_digest: Digest
    media_type: MediaType
    size_bytes: BudgetCounter
    redacted: bool = False


class ArtifactReadRequest(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    reference: ArtifactReference


class ArtifactReadResult(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    reference: ArtifactReference
    content_base64: str

    def content(self) -> bytes:
        return base64.b64decode(self.content_base64, validate=True)


class BrokerMethodSummary(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    name: BrokerMethodName
    capability: CapabilityName
    access: AccessMode
    description: str


class BrokerCatalog(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    methods: tuple[BrokerMethodSummary, ...]


class BrokerMethodContract(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    summary: BrokerMethodSummary
    request_model: str
    response_model: str
    request_schema_json: str
    response_schema_json: str
    contract_digest: Digest


class BrokerContractSet(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    contracts: tuple[BrokerMethodContract, ...]


BrokerReconciliationAction = Literal[
    "receipt_recovered",
    "safe_replay",
    "pending",
    "quarantine",
    "compensation_proposed",
]


class BrokerCallTrace(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    sequence: PositiveCounter
    parent_operation: OperationRef
    method: BrokerMethodName
    grant_id: str
    idempotency_key: str
    request_digest: Digest
    state: Literal["started", "succeeded", "failed"]
    response_digest: Digest | None = None
    response_model: str
    usage_delta: BrokerUsage
    failure_code: str | None = None
    reconciliation_action: BrokerReconciliationAction | None = None
    authority_digest: Digest | None = None
    reconciliation_digest: Digest | None = None
    compensation_digest: Digest | None = None


class BrokerCallReconciliation(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    sequence: PositiveCounter
    method: BrokerMethodName
    action: BrokerReconciliationAction
    reason_code: str
    request_digest: Digest
    authority_digest: Digest
    response_digest: Digest | None = None
    compensation_receipt: BrokerReceipt | None = None


class BrokerReconciliationReport(StrictModel):
    schema_version: Literal["aar.broker.v1"] = BROKER_SCHEMA_VERSION
    operation: OperationRef
    calls: tuple[BrokerCallReconciliation, ...]
    unresolved: bool
    report_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        operation: OperationRef,
        calls: tuple[BrokerCallReconciliation, ...],
        unresolved: bool,
    ) -> BrokerReconciliationReport:
        payload = {
            "operation": operation,
            "calls": calls,
            "unresolved": unresolved,
        }
        return cls(
            operation=operation,
            calls=calls,
            unresolved=unresolved,
            report_digest=canonical_sha256(payload),
        )

    @model_validator(mode="after")
    def validate_report_digest(self) -> BrokerReconciliationReport:
        expected = canonical_sha256(
            {
                "operation": self.operation,
                "calls": self.calls,
                "unresolved": self.unresolved,
            }
        )
        if self.report_digest != expected:
            raise ValueError("broker reconciliation report digest mismatch")
        return self


_METHODS: dict[
    BrokerMethodName,
    tuple[CapabilityName, AccessMode, str, type[StrictModel], type[StrictModel]],
] = {
    "artifact.put": (
        "artifact.write",
        AccessMode.WRITE,
        "Persist content by digest and return an authoritative artifact reference.",
        ArtifactPutRequest,
        ArtifactReference,
    ),
    "artifact.read": (
        "artifact.read",
        AccessMode.READ,
        "Read retained content through its digest-bound artifact reference.",
        ArtifactReadRequest,
        ArtifactReadResult,
    ),
    "effect.propose": (
        "effect.propose",
        AccessMode.WRITE,
        "Create an effect proposal receipt; the facade has no effect execution method.",
        EffectProposal,
        BrokerReceipt,
    ),
    "evidence.query": (
        "evidence.query",
        AccessMode.READ,
        "Query host-owned evidence without exposing provider credentials.",
        EvidenceQuery,
        BrokerReceipt,
    ),
    "model.request": (
        "model.request",
        AccessMode.WRITE,
        "Request a model result through a host-owned broker.",
        ModelRequest,
        BrokerReceipt,
    ),
    "subagent.result": (
        "subagent.result",
        AccessMode.READ,
        "Retrieve a retained child result explicitly by handle.",
        SubagentResultRequest,
        BrokerReceipt,
    ),
    "subagent.submit": (
        "subagent.submit",
        AccessMode.WRITE,
        "Submit bounded child work and return only a retained handle.",
        SubagentSubmit,
        RetainedSubagentHandle,
    ),
}


def _summary(name: BrokerMethodName) -> BrokerMethodSummary:
    capability, access, description, _request, _response = _METHODS[name]
    return BrokerMethodSummary(
        name=name,
        capability=capability,
        access=access,
        description=description,
    )


def _contract(name: BrokerMethodName) -> BrokerMethodContract:
    _capability, _access, _description, request_model, response_model = _METHODS[name]
    request_schema = canonical_json_bytes(request_model.model_json_schema()).decode()
    response_schema = canonical_json_bytes(response_model.model_json_schema()).decode()
    payload = {
        "name": name,
        "capability": _METHODS[name][0],
        "request_model": request_model.__name__,
        "response_model": response_model.__name__,
        "request_schema_json": request_schema,
        "response_schema_json": response_schema,
    }
    return BrokerMethodContract(
        summary=_summary(name),
        request_model=request_model.__name__,
        response_model=response_model.__name__,
        request_schema_json=request_schema,
        response_schema_json=response_schema,
        contract_digest=canonical_sha256(payload),
    )


BROKER_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "broker_artifact_put_request": ArtifactPutRequest,
    "broker_artifact_read_request": ArtifactReadRequest,
    "broker_artifact_read_result": ArtifactReadResult,
    "broker_call_trace": BrokerCallTrace,
    "broker_call_reconciliation": BrokerCallReconciliation,
    "broker_reconciliation_report": BrokerReconciliationReport,
    "broker_catalog": BrokerCatalog,
    "broker_contract_set": BrokerContractSet,
    "broker_effect_proposal": EffectProposal,
    "broker_evidence_query": EvidenceQuery,
    "broker_method_contract": BrokerMethodContract,
    "broker_model_request": ModelRequest,
    "broker_receipt": BrokerReceipt,
    "broker_retained_subagent_handle": RetainedSubagentHandle,
    "broker_subagent_result_request": SubagentResultRequest,
    "broker_subagent_submit": SubagentSubmit,
    "broker_usage": BrokerUsage,
}
