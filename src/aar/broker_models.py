"""Transport-neutral broker contract models and catalog metadata."""

from __future__ import annotations

import base64
from typing import Annotated, Literal, Self

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
from aar.versions import (
    BROKER_SCHEMA_VERSION,
    MODEL_RESPONSE_SCHEMA_VERSION,
    MODEL_ROUTE_SCHEMA_VERSION,
    MODEL_USAGE_SCHEMA_VERSION,
)

ModelRouteValue = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._~:/+-]*$", strict=True),
]
ReasoningEffort = ModelRouteValue | None
FallbackPolicy = Literal["none", "explicit"]
CachePolicy = Literal["disabled", "run-scoped", "provider-managed"]
AccountingSource = Literal["reference", "provider_reported", "estimated"]


class ModelRouteProfile(StrictModel):
    """Owner-authored, credential-free route policy."""

    schema_version: Literal["aar.model-route.v1"] = MODEL_ROUTE_SCHEMA_VERSION
    profile_id: ModelRouteValue
    provider_driver: ModelRouteValue
    provider: ModelRouteValue
    model: ModelRouteValue
    reasoning_effort: ReasoningEffort = None
    max_output_tokens: PositiveCounter
    fallback_policy: FallbackPolicy = "none"
    cache_policy: CachePolicy = "disabled"

    @property
    def profile_digest(self) -> str:
        return canonical_sha256(self._digest_payload())

    def _digest_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "provider_driver": self.provider_driver,
            "provider": self.provider,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens": self.max_output_tokens,
            "fallback_policy": self.fallback_policy,
            "cache_policy": self.cache_policy,
        }


class ModelRouteCatalog(StrictModel):
    """Canonical owner-authorized route catalog with no credential material."""

    schema_version: Literal["aar.model-route.v1"] = MODEL_ROUTE_SCHEMA_VERSION
    profiles: Annotated[
        tuple[ModelRouteProfile, ...], Field(min_length=1, max_length=64)
    ]
    catalog_digest: Digest

    @classmethod
    def issue(cls, profiles: tuple[ModelRouteProfile, ...]) -> Self:
        if len(profiles) > 64:
            raise ValueError("model route catalog supports at most 64 profiles")
        ordered = tuple(sorted(profiles, key=lambda item: item.profile_id))
        payload = {
            "schema_version": MODEL_ROUTE_SCHEMA_VERSION,
            "profiles": [item.model_dump(mode="json") for item in ordered],
        }
        return cls(profiles=ordered, catalog_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def catalog_is_canonical_and_content_bound(self) -> Self:
        ids = [item.profile_id for item in self.profiles]
        if ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("model route profiles must be sorted by unique profile_id")
        payload = {
            "schema_version": self.schema_version,
            "profiles": [item.model_dump(mode="json") for item in self.profiles],
        }
        if self.catalog_digest != canonical_sha256(payload):
            raise ValueError("model route catalog digest mismatch")
        return self


class ModelRouteBinding(StrictModel):
    """Admission-time immutable binding to one exact catalog profile."""

    schema_version: Literal["aar.model-route.v1"] = MODEL_ROUTE_SCHEMA_VERSION
    profile_id: ModelRouteValue
    catalog_digest: Digest
    profile_digest: Digest
    provider_driver: ModelRouteValue
    provider: ModelRouteValue
    model: ModelRouteValue
    reasoning_effort: ReasoningEffort = None
    max_output_tokens: PositiveCounter
    fallback_policy: FallbackPolicy
    cache_policy: CachePolicy

    @classmethod
    def issue(cls, catalog: ModelRouteCatalog, profile: ModelRouteProfile) -> Self:
        member = next(
            (item for item in catalog.profiles if item.profile_id == profile.profile_id),
            None,
        )
        if member is None or member != profile:
            raise ValueError("model route profile is not an exact member of the catalog")
        return cls(
            profile_id=member.profile_id,
            catalog_digest=catalog.catalog_digest,
            profile_digest=member.profile_digest,
            provider_driver=member.provider_driver,
            provider=member.provider,
            model=member.model,
            reasoning_effort=member.reasoning_effort,
            max_output_tokens=member.max_output_tokens,
            fallback_policy=member.fallback_policy,
            cache_policy=member.cache_policy,
        )

    @model_validator(mode="after")
    def profile_digest_is_content_bound(self) -> Self:
        profile = ModelRouteProfile(
            profile_id=self.profile_id,
            provider_driver=self.provider_driver,
            provider=self.provider,
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            max_output_tokens=self.max_output_tokens,
            fallback_policy=self.fallback_policy,
            cache_policy=self.cache_policy,
        )
        if self.profile_digest != profile.profile_digest:
            raise ValueError("model route profile digest mismatch")
        return self


class EffectiveModelRoute(StrictModel):
    schema_version: Literal["aar.model-route.v1"] = MODEL_ROUTE_SCHEMA_VERSION
    provider_driver: ModelRouteValue
    provider: ModelRouteValue
    model: ModelRouteValue
    reasoning_effort: ReasoningEffort = None


class ModelUsageRecord(StrictModel):
    schema_version: Literal["aar.model-usage.v1"] = MODEL_USAGE_SCHEMA_VERSION
    accounting_source: AccountingSource
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    cache_read_tokens: BudgetCounter | None = None
    cache_write_tokens: BudgetCounter | None = None
    reasoning_tokens: BudgetCounter | None = None
    total_tokens: BudgetCounter
    retry_ordinal: BudgetCounter = 0
    wasted: bool = False

    @model_validator(mode="after")
    def total_is_exact(self) -> Self:
        if self.total_tokens != self.input_tokens + self.output_tokens:
            raise ValueError("model usage total must equal input plus output tokens")
        return self


class ModelRouteReceipt(StrictModel):
    schema_version: Literal["aar.model-response.v1"] = MODEL_RESPONSE_SCHEMA_VERSION
    requested: ModelRouteBinding
    effective: EffectiveModelRoute
    finish_reason: ModelRouteValue
    provider_response_id: ModelRouteValue | None = None
    fallback_chain: tuple[EffectiveModelRoute, ...] = ()
    lookup_supported: bool = False
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        requested: ModelRouteBinding,
        effective: EffectiveModelRoute,
        finish_reason: str,
        provider_response_id: str | None = None,
        fallback_chain: tuple[EffectiveModelRoute, ...] = (),
        lookup_supported: bool = False,
    ) -> Self:
        payload = {
            "requested": requested,
            "effective": effective,
            "finish_reason": finish_reason,
            "provider_response_id": provider_response_id,
            "fallback_chain": fallback_chain,
            "lookup_supported": lookup_supported,
        }
        return cls(
            requested=requested,
            effective=effective,
            finish_reason=finish_reason,
            provider_response_id=provider_response_id,
            fallback_chain=fallback_chain,
            lookup_supported=lookup_supported,
            receipt_digest=canonical_sha256(payload),
        )

    @model_validator(mode="after")
    def receipt_is_content_bound_and_policy_compliant(self) -> Self:
        payload = {
            "requested": self.requested,
            "effective": self.effective,
            "finish_reason": self.finish_reason,
            "provider_response_id": self.provider_response_id,
            "fallback_chain": self.fallback_chain,
            "lookup_supported": self.lookup_supported,
        }
        if self.receipt_digest != canonical_sha256(payload):
            raise ValueError("model route receipt digest mismatch")
        expected = (
            self.requested.provider_driver,
            self.requested.provider,
            self.requested.model,
            self.requested.reasoning_effort,
        )
        actual = (
            self.effective.provider_driver,
            self.effective.provider,
            self.effective.model,
            self.effective.reasoning_effort,
        )
        if actual != expected:
            raise ValueError("effective model route drifted from the bound profile")
        if self.requested.fallback_policy == "none" and self.fallback_chain:
            raise ValueError("fallback chain is forbidden by the bound route profile")
        return self


class ModelResponse(StrictModel):
    schema_version: Literal["aar.model-response.v1"] = MODEL_RESPONSE_SCHEMA_VERSION
    output_text: Annotated[str, Field(max_length=1_048_576, strict=True)]
    route_receipt: ModelRouteReceipt
    usage: ModelUsageRecord

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
    state: Literal["started", "rejected_before_send", "succeeded", "failed"]
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
    "model_response": ModelResponse,
    "model_route_binding": ModelRouteBinding,
    "model_route_catalog": ModelRouteCatalog,
    "model_route_effective": EffectiveModelRoute,
    "model_route_profile": ModelRouteProfile,
    "model_route_receipt": ModelRouteReceipt,
    "model_usage_record": ModelUsageRecord,
    "broker_receipt": BrokerReceipt,
    "broker_retained_subagent_handle": RetainedSubagentHandle,
    "broker_subagent_result_request": SubagentResultRequest,
    "broker_subagent_submit": SubagentSubmit,
    "broker_usage": BrokerUsage,
}
