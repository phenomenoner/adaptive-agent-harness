"""Strict transport-neutral activation and provider-ready model contracts."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self, cast

from pydantic import Field, StringConstraints, model_validator

from aar.broker_models import ModelRouteValue
from aar.canonical import canonical_sha256
from aar.schemas import (
    BudgetCounter,
    CapabilityName,
    Digest,
    OpaqueToken,
    PositiveCounter,
    StrictModel,
)

PROVIDER_READY_SCHEMA_VERSION = "aar.provider-ready.v1"
METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION = "aar.method-adapter-manifest.v1"
HOST_ACTIVATION_INTENT_SCHEMA_VERSION = "aar.host-activation-intent.v1"
HOST_ACTIVATION_PROFILE_SCHEMA_VERSION = "aar.host-activation-profile.v1"

PackageVersion = Annotated[
    str,
    StringConstraints(
        max_length=64,
        pattern=(
            r"^(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*)\."
            r"(?:0|[1-9][0-9]*)"
            r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?"
            r"(?:\.post(?:0|[1-9][0-9]*))?"
            r"(?:\.dev(?:0|[1-9][0-9]*))?$"
        ),
        strict=True,
    ),
]
LocalAuthorityStoreId = Annotated[
    str,
    StringConstraints(
        min_length=25,
        max_length=152,
        pattern=r"^local-file-authority-v1:[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$",
        strict=True,
    ),
]
SourceCommit = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$", strict=True)]
WorkbenchActivationMethodName = Literal[
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
]
CapabilityEvidenceTier = Literal[
    "unknown",
    "caller_observed",
    "host_receipt_bound",
    "provider_attested",
]

PlannerMode = Literal["caller_delegated_ticketed", "service_managed"]
BackendKind = Literal["native", "caller_driver", "reference"]

_BUDGET_MAX = 9_223_372_036_854_775_807
_WALL_TIME = Annotated[int, Field(ge=1_000, le=900_000, strict=True)]
_MODEL_REQUESTS = Annotated[int, Field(ge=1, le=128, strict=True)]
_MAX_DEADLINE = Annotated[int, Field(ge=1_000, le=900_000, strict=True)]
_CHILD_OPERATIONS = Annotated[int, Field(ge=0, le=64, strict=True)]
_ARTIFACT_BYTES = Annotated[int, Field(ge=0, le=33_554_432, strict=True)]
_ADAPTER_RANK = {
    "model.request": 0,
    "subagent.submit": 1,
    "subagent.result": 2,
    "evidence.query": 3,
    "artifact.put": 4,
    "effect.propose": 5,
}
_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def _compute_self_digest(document: StrictModel, digest_field: str) -> str:
    """Hash one complete validated document after removing only its root digest."""

    payload = document.model_dump(mode="json")
    try:
        del payload[digest_field]
    except KeyError as error:
        raise ValueError(f"self-digest field {digest_field!r} is missing") from error
    return canonical_sha256(payload)


def _issue_document(
    model_type: type[StrictModel], payload: dict[str, object], digest_field: str
) -> StrictModel:
    provisional = model_type.model_construct(
        **payload, **{digest_field: _PLACEHOLDER_DIGEST}, _fields_set=set(payload) | {digest_field}
    )
    digest = _compute_self_digest(provisional, digest_field)
    return model_type(**payload, **{digest_field: digest})


def _require_sorted_unique(values: Iterable[str], field_name: str) -> None:
    materialized = list(values)
    if materialized != sorted(materialized) or len(materialized) != len(set(materialized)):
        raise ValueError(f"{field_name} must be sorted and unique")


class ProviderReadyCandidate(StrictModel):
    package_version: PackageVersion
    source_commit: SourceCommit
    wheel_digest: Digest
    contract_manifest_digest: Digest
    skill_digest: Digest


class ActivationRuntimeBinding(StrictModel):
    runtime_home_digest: Digest
    database_identity: OpaqueToken
    required_registry_version: Literal[6]
    programmable_backend: Literal["ipython"]
    security_profile: Literal["trusted_local"]


class ActivationPlannerBinding(StrictModel):
    mode: PlannerMode
    method: Literal["model.request"]
    directive_schema_version: Literal["aar.rlm-directive.v1"]
    directive_schema_digest: Digest


class ActivationRoutePolicy(StrictModel):
    catalog_digest: Digest
    allowed_profile_ids: Annotated[
        tuple[ModelRouteValue, ...], Field(min_length=1, max_length=64)
    ]
    fallback_policy: Literal["none"]
    cache_policy: Literal["disabled"]

    @classmethod
    def issue(
        cls,
        *,
        catalog_digest: Digest,
        allowed_profile_ids: tuple[ModelRouteValue, ...],
        fallback_policy: Literal["none"],
        cache_policy: Literal["disabled"],
    ) -> Self:
        return cls(
            catalog_digest=catalog_digest,
            allowed_profile_ids=tuple(sorted(allowed_profile_ids)),
            fallback_policy=fallback_policy,
            cache_policy=cache_policy,
        )

    @model_validator(mode="after")
    def allowed_profile_ids_are_canonical(self) -> Self:
        _require_sorted_unique(self.allowed_profile_ids, "allowed_profile_ids")
        return self


class GrantBudgetCeiling(StrictModel):
    wall_time_ms: _WALL_TIME
    model_requests: _MODEL_REQUESTS
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    child_operations: _CHILD_OPERATIONS
    artifact_bytes: _ARTIFACT_BYTES


class ActivationGrantPolicy(StrictModel):
    principal_patterns: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=64)]
    capabilities: Annotated[tuple[CapabilityName, ...], Field(min_length=1, max_length=64)]
    budget_ceiling: GrantBudgetCeiling
    max_deadline_ms: _MAX_DEADLINE

    @classmethod
    def issue(
        cls,
        *,
        principal_patterns: tuple[OpaqueToken, ...],
        capabilities: tuple[CapabilityName, ...],
        budget_ceiling: GrantBudgetCeiling,
        max_deadline_ms: int,
    ) -> Self:
        return cls(
            principal_patterns=tuple(sorted(principal_patterns)),
            capabilities=tuple(sorted(capabilities)),
            budget_ceiling=budget_ceiling,
            max_deadline_ms=max_deadline_ms,
        )

    @model_validator(mode="after")
    def policy_collections_are_canonical(self) -> Self:
        _require_sorted_unique(self.principal_patterns, "principal_patterns")
        _require_sorted_unique(self.capabilities, "capabilities")
        return self


class MethodAdapterManifest(StrictModel):
    schema_version: Literal["aar.method-adapter-manifest.v1"]
    method: WorkbenchActivationMethodName
    contract_id: CapabilityName
    request_schema_digest: Digest
    response_schema_digest: Digest
    backend_kind: BackendKind
    factory_id: CapabilityName
    factory_digest: Digest
    adapter_id: OpaqueToken
    adapter_generation_policy: Literal["runtime_generation"]
    reference_only: bool
    evidence_tier: CapabilityEvidenceTier
    lookup_supported: bool
    cancel_supported: bool
    manifest_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.method-adapter-manifest.v1"],
        method: WorkbenchActivationMethodName,
        contract_id: CapabilityName,
        request_schema_digest: Digest,
        response_schema_digest: Digest,
        backend_kind: BackendKind,
        factory_id: CapabilityName,
        factory_digest: Digest,
        adapter_id: OpaqueToken,
        adapter_generation_policy: Literal["runtime_generation"],
        reference_only: bool,
        evidence_tier: CapabilityEvidenceTier,
        lookup_supported: bool,
        cancel_supported: bool,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "method": method,
            "contract_id": contract_id,
            "request_schema_digest": request_schema_digest,
            "response_schema_digest": response_schema_digest,
            "backend_kind": backend_kind,
            "factory_id": factory_id,
            "factory_digest": factory_digest,
            "adapter_id": adapter_id,
            "adapter_generation_policy": adapter_generation_policy,
            "reference_only": reference_only,
            "evidence_tier": evidence_tier,
            "lookup_supported": lookup_supported,
            "cancel_supported": cancel_supported,
        }
        return cast(Self, _issue_document(cls, payload, "manifest_digest"))

    @model_validator(mode="after")
    def backend_truth_is_consistent(self) -> Self:
        if self.backend_kind == "reference":
            if not self.reference_only:
                raise ValueError("reference backend requires reference_only=true")
            if self.evidence_tier != "unknown":
                raise ValueError("reference backend requires evidence_tier=unknown")
        elif self.reference_only:
            raise ValueError("native and caller_driver backends require reference_only=false")
        if self.method == "artifact.put" and self.backend_kind == "caller_driver":
            raise ValueError("artifact.put cannot use caller_driver backend")
        return self

    @model_validator(mode="after")
    def manifest_digest_is_content_bound(self) -> Self:
        if self.manifest_digest != _compute_self_digest(self, "manifest_digest"):
            raise ValueError("manifest digest does not match canonical manifest bytes")
        return self


class HostActivationIntent(StrictModel):
    schema_version: Literal["aar.host-activation-intent.v1"]
    profile_id: OpaqueToken
    activation_generation: PositiveCounter
    previous_activation_authority_digest: Digest | None
    candidate: ProviderReadyCandidate
    runtime: ActivationRuntimeBinding
    planner: ActivationPlannerBinding
    adapters: Annotated[tuple[MethodAdapterManifest, ...], Field(min_length=1, max_length=6)]
    routes: ActivationRoutePolicy
    grant_policy: ActivationGrantPolicy
    cutover_authority_store_id: LocalAuthorityStoreId
    recovery_compatibility_digest: Digest
    intent_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.host-activation-intent.v1"],
        profile_id: OpaqueToken,
        activation_generation: PositiveCounter,
        previous_activation_authority_digest: Digest | None,
        candidate: ProviderReadyCandidate,
        runtime: ActivationRuntimeBinding,
        planner: ActivationPlannerBinding,
        adapters: tuple[MethodAdapterManifest, ...],
        routes: ActivationRoutePolicy,
        grant_policy: ActivationGrantPolicy,
        cutover_authority_store_id: LocalAuthorityStoreId,
        recovery_compatibility_digest: Digest,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "profile_id": profile_id,
            "activation_generation": activation_generation,
            "previous_activation_authority_digest": previous_activation_authority_digest,
            "candidate": candidate,
            "runtime": runtime,
            "planner": planner,
            "adapters": tuple(sorted(adapters, key=lambda item: _ADAPTER_RANK[item.method])),
            "routes": routes,
            "grant_policy": grant_policy,
            "cutover_authority_store_id": cutover_authority_store_id,
            "recovery_compatibility_digest": recovery_compatibility_digest,
        }
        return cast(Self, _issue_document(cls, payload, "intent_digest"))

    @model_validator(mode="after")
    def adapters_are_canonical_and_planner_bound(self) -> Self:
        methods = [adapter.method for adapter in self.adapters]
        if methods != sorted(methods, key=lambda item: _ADAPTER_RANK[item]):
            raise ValueError("adapters must use frozen catalog order")
        if len(methods) != len(set(methods)):
            raise ValueError("adapters must contain unique methods")
        model_manifests = [
            adapter for adapter in self.adapters if adapter.method == "model.request"
        ]
        if len(model_manifests) != 1:
            raise ValueError("activation intent requires exactly one model.request adapter")
        model_manifest = model_manifests[0]
        expected_backend: BackendKind = (
            "caller_driver"
            if self.planner.mode == "caller_delegated_ticketed"
            else "native"
        )
        if (
            model_manifest.backend_kind != expected_backend
            or model_manifest.reference_only
        ):
            raise ValueError("planner mode must bind its matching executable model.request adapter")
        return self

    @model_validator(mode="after")
    def intent_digest_is_content_bound(self) -> Self:
        if self.intent_digest != _compute_self_digest(self, "intent_digest"):
            raise ValueError("intent digest does not match canonical intent bytes")
        return self


class HostActivationProfile(StrictModel):
    schema_version: Literal["aar.host-activation-profile.v1"]
    intent: HostActivationIntent
    migration_attestation_digest: Digest
    profile_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.host-activation-profile.v1"],
        intent: HostActivationIntent,
        migration_attestation_digest: Digest,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "intent": intent,
            "migration_attestation_digest": migration_attestation_digest,
        }
        return cast(Self, _issue_document(cls, payload, "profile_digest"))

    @model_validator(mode="after")
    def profile_digest_is_content_bound(self) -> Self:
        if self.profile_digest != _compute_self_digest(self, "profile_digest"):
            raise ValueError("profile digest does not match canonical profile bytes")
        return self


PROVIDER_READY_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "aar.method-adapter-manifest.v1": MethodAdapterManifest,
    "aar.host-activation-intent.v1": HostActivationIntent,
    "aar.host-activation-profile.v1": HostActivationProfile,
}
