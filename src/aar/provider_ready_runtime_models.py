"""Strict, inert activation readback and workbench grant models.

This module validates only transport-neutral bytes, canonical collections,
document-local state/nullability rules, and root self digests.  It performs no
runtime observation, grant issuance, I/O, or provider work.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from aar.broker_models import ModelRouteValue
from aar.canonical import canonical_sha256
from aar.provider_ready_models import (
    CapabilityEvidenceTier,
    GrantBudgetCeiling,
    LocalAuthorityStoreId,
    PlannerMode,
    WorkbenchActivationMethodName,
)
from aar.provider_ready_operator_models import CandidateBinding, UnixMs
from aar.schemas import (
    BudgetCounter,
    CapabilityName,
    Digest,
    OpaqueToken,
    PositiveCounter,
    StrictModel,
)

ACTIVATION_READBACK_SCHEMA_VERSION = "aar.activation-readback.v1"
WORKBENCH_GRANT_SET_SCHEMA_VERSION = "aar.workbench-grant-set.v1"

ReadbackBackendKind = Literal["unconfigured", "native", "caller_driver", "reference"]
ActivationReadbackState = Literal[
    "unconfigured",
    "migration_required",
    "profile_invalid",
    "profile_verified",
    "starting",
    "active",
    "degraded",
    "recovery_required",
]
ActivationReadbackReasonCode = Literal[
    "none",
    "REGISTRY_VERSION_UNSUPPORTED",
    "ACTIVATION_PROFILE_INVALID",
    "ACTIVATION_BINDING_MISMATCH",
    "GRANT_POLICY_INVALID",
    "ACTIVATION_HISTORY_CONFLICT",
    "CUTOVER_RECOVERY_REQUIRED",
    "ABORT_OR_APPLY_REQUIRED",
    "RECONCILE_INPUT_REQUIRED",
    "GRANT_BINDING_MISMATCH",
    "PLANNER_UNAVAILABLE",
    "CAPABILITY_UNAVAILABLE",
    "STALE_ADAPTER_GENERATION",
]
OperatorEpochKind = Literal["cutover", "restore"]
EvidenceSource = Literal[
    "installed_assets",
    "registry",
    "activation_history",
    "activation_current",
    "epoch_marker",
    "supervisor",
    "capability_registry",
    "grant_issuer",
]
SessionBindingPolicy = Literal["bind_exact_request_session"]

_METHODS: tuple[WorkbenchActivationMethodName, ...] = (
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
)
_METHOD_RANK = {method: index for index, method in enumerate(_METHODS)}
_MAX_COUNTER = 9_223_372_036_854_775_807
_WALL_TIME = Annotated[int, Field(ge=1_000, le=900_000, strict=True)]
_MODEL_REQUESTS = Annotated[int, Field(ge=1, le=128, strict=True)]
_CHILD_OPERATIONS = Annotated[int, Field(ge=0, le=64, strict=True)]
_ARTIFACT_BYTES = Annotated[int, Field(ge=0, le=33_554_432, strict=True)]
_MAX_TTL = Annotated[int, Field(ge=1_000, le=900_000, strict=True)]
_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def _compute_self_digest(document: StrictModel, digest_field: str) -> str:
    """Hash a complete validated document after removing only its root digest."""

    payload = document.model_dump(mode="json")
    try:
        del payload[digest_field]
    except KeyError as error:
        raise ValueError(f"self-digest field {digest_field!r} is missing") from error
    return canonical_sha256(payload)


def _issue_document(
    model_type: type[StrictModel], payload: dict[str, object], digest_field: str
) -> StrictModel:
    """Build one self-digested document without introducing a digest cycle."""

    provisional = model_type.model_construct(
        **payload,
        **{digest_field: _PLACEHOLDER_DIGEST},
        _fields_set=set(payload) | {digest_field},
    )
    digest = _compute_self_digest(provisional, digest_field)
    return model_type(**payload, **{digest_field: digest})


def _require_sorted_unique(values: Iterable[str], field_name: str) -> None:
    materialized = list(values)
    if materialized != sorted(materialized) or len(materialized) != len(set(materialized)):
        raise ValueError(f"{field_name} must be sorted and unique")


def _require_null(value: object, field_name: str, state: str) -> None:
    if value is not None:
        raise ValueError(f"{state} state requires {field_name}=null")


def _require_non_null(value: object, field_name: str, state: str) -> None:
    if value is None:
        raise ValueError(f"{state} state requires {field_name} to be non-null")


def _require_reason(
    actual: str,
    allowed: tuple[str, ...],
    state: str,
) -> None:
    if actual not in allowed:
        expected = "|".join(allowed)
        raise ValueError(f"{state} state requires reason_code={expected}")


class BackendAvailability(StrictModel):
    """One readback-only capability row for a frozen broker method."""

    method: WorkbenchActivationMethodName
    contract_id: CapabilityName
    request_schema_digest: Digest
    response_schema_digest: Digest
    backend_kind: ReadbackBackendKind
    configured: bool
    reference_only: bool
    adapter_id: OpaqueToken | None
    adapter_generation: PositiveCounter | None
    evidence_tier: CapabilityEvidenceTier

    @model_validator(mode="after")
    def backend_tuple_is_coherent(self) -> Self:
        if self.backend_kind == "unconfigured":
            if self.configured:
                raise ValueError("unconfigured backend requires configured=false")
            if self.reference_only:
                raise ValueError("unconfigured backend requires reference_only=false")
            if self.adapter_id is not None or self.adapter_generation is not None:
                raise ValueError(
                    "unconfigured backend requires null adapter_id and adapter_generation"
                )
            if self.evidence_tier != "unknown":
                raise ValueError("unconfigured backend requires evidence_tier=unknown")
        elif self.backend_kind == "reference":
            if not self.configured:
                raise ValueError("reference backend requires configured=true")
            if not self.reference_only:
                raise ValueError("reference backend requires reference_only=true")
            if self.adapter_id is not None or self.adapter_generation is not None:
                raise ValueError(
                    "reference backend requires null adapter_id and adapter_generation"
                )
            if self.evidence_tier != "unknown":
                raise ValueError("reference backend requires evidence_tier=unknown")
        else:
            if not self.configured:
                raise ValueError("native and caller_driver backends require configured=true")
            if self.reference_only:
                raise ValueError("native and caller_driver backends require reference_only=false")
            if self.adapter_id is None or self.adapter_generation is None:
                raise ValueError(
                    "native and caller_driver backends require non-null adapter_id "
                    "and adapter_generation"
                )
        if self.method == "artifact.put" and self.backend_kind == "caller_driver":
            raise ValueError("artifact.put cannot use caller_driver backend")
        return self


class PlannerReadback(StrictModel):
    """Document-local planner binding and readiness projection."""

    mode: PlannerMode | None
    ready: bool
    factory_id: CapabilityName | None
    factory_digest: Digest | None
    method_manifest_digest: Digest | None

    @model_validator(mode="after")
    def mode_bindings_are_coherent(self) -> Self:
        bindings = (self.factory_id, self.factory_digest, self.method_manifest_digest)
        if self.mode is None:
            if any(value is not None for value in bindings):
                raise ValueError("null planner mode requires all planner bindings to be null")
            if self.ready:
                raise ValueError("null planner mode requires ready=false")
        elif any(value is None for value in bindings):
            raise ValueError("non-null planner mode requires all planner bindings to be non-null")
        return self


class WorkbenchGrantSet(StrictModel):
    """Self-digested server-side workbench policy record."""

    schema_version: Literal["aar.workbench-grant-set.v1"]
    runtime_generation: PositiveCounter
    activation_generation: PositiveCounter
    profile_id: OpaqueToken
    profile_digest: Digest
    activation_authority_digest: Digest
    capability_digest: Digest
    route_catalog_digest: Digest
    principal_ids: Annotated[tuple[OpaqueToken, ...], Field(min_length=1, max_length=64)]
    session_binding_policy: SessionBindingPolicy
    capabilities: Annotated[tuple[CapabilityName, ...], Field(min_length=1, max_length=64)]
    budget_ceiling: GrantBudgetCeiling
    max_ttl_ms: _MAX_TTL
    grant_set_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.workbench-grant-set.v1"],
        runtime_generation: PositiveCounter,
        activation_generation: PositiveCounter,
        profile_id: OpaqueToken,
        profile_digest: Digest,
        activation_authority_digest: Digest,
        capability_digest: Digest,
        route_catalog_digest: Digest,
        principal_ids: tuple[OpaqueToken, ...],
        session_binding_policy: SessionBindingPolicy,
        capabilities: tuple[CapabilityName, ...],
        budget_ceiling: GrantBudgetCeiling,
        max_ttl_ms: int,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "runtime_generation": runtime_generation,
            "activation_generation": activation_generation,
            "profile_id": profile_id,
            "profile_digest": profile_digest,
            "activation_authority_digest": activation_authority_digest,
            "capability_digest": capability_digest,
            "route_catalog_digest": route_catalog_digest,
            "principal_ids": tuple(sorted(principal_ids)),
            "session_binding_policy": session_binding_policy,
            "capabilities": tuple(sorted(capabilities)),
            "budget_ceiling": budget_ceiling,
            "max_ttl_ms": max_ttl_ms,
        }
        return cast(Self, _issue_document(cls, payload, "grant_set_digest"))

    @model_validator(mode="after")
    def collections_are_canonical(self) -> Self:
        _require_sorted_unique(self.principal_ids, "principal_ids")
        _require_sorted_unique(self.capabilities, "capabilities")
        return self

    @model_validator(mode="after")
    def grant_set_digest_is_content_bound(self) -> Self:
        if self.grant_set_digest != _compute_self_digest(self, "grant_set_digest"):
            raise ValueError("grant set digest does not match canonical grant-set bytes")
        return self


class IssuedWorkbenchGrant(StrictModel):
    """Internal server-owned issued grant; not a public schema document."""

    grant_id: OpaqueToken
    grant_set_digest: Digest
    capability: CapabilityName
    principal_id: OpaqueToken
    session_id: OpaqueToken
    runtime_generation: PositiveCounter
    activation_generation: PositiveCounter
    profile_digest: Digest
    activation_authority_digest: Digest
    capability_digest: Digest
    route_catalog_digest: Digest
    wall_time_ms: _WALL_TIME
    model_requests: _MODEL_REQUESTS
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    child_operations: _CHILD_OPERATIONS
    artifact_bytes: _ARTIFACT_BYTES
    issued_at_unix_ms: UnixMs
    expires_at_unix_ms: UnixMs
    revoked: bool

    @model_validator(mode="after")
    def issuance_precedes_expiry(self) -> Self:
        if self.issued_at_unix_ms >= self.expires_at_unix_ms:
            raise ValueError("issued_at_unix_ms must be less than expires_at_unix_ms")
        return self


class ActivationReadback(StrictModel):
    """Self-digested strict projection of read-only activation observations."""

    schema_version: Literal["aar.activation-readback.v1"]
    observed_at_unix_ms: UnixMs
    state: ActivationReadbackState
    reason_code: ActivationReadbackReasonCode
    runtime_generation: PositiveCounter | None
    supervisor_process_identity_digest: Digest | None
    candidate: CandidateBinding | None
    registry_schema_version: Literal[5, 6] | None
    registry_schema_digest: Digest | None
    migration_attestation_digest: Digest | None
    profile_id: OpaqueToken | None
    activation_generation: PositiveCounter | None
    intent_digest: Digest | None
    profile_digest: Digest | None
    previous_activation_authority_digest: Digest | None
    activation_authority_digest: Digest | None
    grant_set_digest: Digest | None
    capability_digest: Digest | None
    broker_catalog_digest: Digest | None
    tool_surface_digest: Digest | None
    methods: Annotated[tuple[BackendAvailability, ...], Field(min_length=6, max_length=6)]
    planner: PlannerReadback
    route_catalog_digest: Digest | None
    route_profile_ids: Annotated[tuple[ModelRouteValue, ...], Field(min_length=0, max_length=64)]
    authority_store_id: LocalAuthorityStoreId | None
    authority_history_tip_digest: Digest | None
    operator_epoch_kind: OperatorEpochKind | None
    operator_epoch: OpaqueToken | None
    latest_operator_receipt_digest: Digest | None
    evidence_sources: Annotated[tuple[EvidenceSource, ...], Field(min_length=0, max_length=8)]
    readback_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.activation-readback.v1"],
        observed_at_unix_ms: UnixMs,
        state: ActivationReadbackState,
        reason_code: ActivationReadbackReasonCode,
        runtime_generation: PositiveCounter | None,
        supervisor_process_identity_digest: Digest | None,
        candidate: CandidateBinding | None,
        registry_schema_version: Literal[5, 6] | None,
        registry_schema_digest: Digest | None,
        migration_attestation_digest: Digest | None,
        profile_id: OpaqueToken | None,
        activation_generation: PositiveCounter | None,
        intent_digest: Digest | None,
        profile_digest: Digest | None,
        previous_activation_authority_digest: Digest | None,
        activation_authority_digest: Digest | None,
        grant_set_digest: Digest | None,
        capability_digest: Digest | None,
        broker_catalog_digest: Digest | None,
        tool_surface_digest: Digest | None,
        methods: tuple[BackendAvailability, ...],
        planner: PlannerReadback,
        route_catalog_digest: Digest | None,
        route_profile_ids: tuple[ModelRouteValue, ...],
        authority_store_id: LocalAuthorityStoreId | None,
        authority_history_tip_digest: Digest | None,
        operator_epoch_kind: OperatorEpochKind | None,
        operator_epoch: OpaqueToken | None,
        latest_operator_receipt_digest: Digest | None,
        evidence_sources: tuple[EvidenceSource, ...],
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "observed_at_unix_ms": observed_at_unix_ms,
            "state": state,
            "reason_code": reason_code,
            "runtime_generation": runtime_generation,
            "supervisor_process_identity_digest": supervisor_process_identity_digest,
            "candidate": candidate,
            "registry_schema_version": registry_schema_version,
            "registry_schema_digest": registry_schema_digest,
            "migration_attestation_digest": migration_attestation_digest,
            "profile_id": profile_id,
            "activation_generation": activation_generation,
            "intent_digest": intent_digest,
            "profile_digest": profile_digest,
            "previous_activation_authority_digest": previous_activation_authority_digest,
            "activation_authority_digest": activation_authority_digest,
            "grant_set_digest": grant_set_digest,
            "capability_digest": capability_digest,
            "broker_catalog_digest": broker_catalog_digest,
            "tool_surface_digest": tool_surface_digest,
            "methods": tuple(methods),
            "planner": planner,
            "route_catalog_digest": route_catalog_digest,
            "route_profile_ids": tuple(sorted(route_profile_ids)),
            "authority_store_id": authority_store_id,
            "authority_history_tip_digest": authority_history_tip_digest,
            "operator_epoch_kind": operator_epoch_kind,
            "operator_epoch": operator_epoch,
            "latest_operator_receipt_digest": latest_operator_receipt_digest,
            "evidence_sources": tuple(sorted(evidence_sources)),
        }
        return cast(Self, _issue_document(cls, payload, "readback_digest"))

    @model_validator(mode="after")
    def collections_and_methods_are_canonical(self) -> Self:
        methods = tuple(item.method for item in self.methods)
        if len(set(methods)) != len(methods):
            raise ValueError("methods must contain each frozen broker method exactly once")
        if methods != _METHODS:
            raise ValueError("methods must use the exact frozen broker-catalog order")
        _require_sorted_unique(self.route_profile_ids, "route_profile_ids")
        _require_sorted_unique(self.evidence_sources, "evidence_sources")
        return self

    @model_validator(mode="after")
    def local_bindings_are_coherent(self) -> Self:
        if (self.registry_schema_version is None) != (self.registry_schema_digest is None):
            raise ValueError(
                "registry_schema_version and registry_schema_digest must be both "
                "null or non-null"
            )

        if self.operator_epoch_kind is None:
            if self.operator_epoch is not None or self.latest_operator_receipt_digest is not None:
                raise ValueError(
                    "null operator_epoch_kind requires null operator_epoch and receipt digest"
                )
        elif self.operator_epoch is None:
            raise ValueError("non-null operator_epoch_kind requires non-null operator_epoch")

        if self.planner.ready != (self.state == "active"):
            raise ValueError("planner.ready must be true iff activation state is active")

        state = self.state
        if state == "unconfigured":
            _require_reason(self.reason_code, ("none",), state)
            for field_name in (
                "runtime_generation",
                "supervisor_process_identity_digest",
                "registry_schema_version",
                "registry_schema_digest",
                "migration_attestation_digest",
                "profile_id",
                "activation_generation",
                "intent_digest",
                "profile_digest",
                "previous_activation_authority_digest",
                "activation_authority_digest",
                "grant_set_digest",
                "capability_digest",
                "authority_store_id",
                "authority_history_tip_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            if self.planner.mode is not None:
                raise ValueError("unconfigured state requires planner.mode=null")
            self._require_non_executable_methods(state)
        elif state == "migration_required":
            _require_reason(self.reason_code, ("REGISTRY_VERSION_UNSUPPORTED",), state)
            if self.registry_schema_version != 5:
                raise ValueError("migration_required state requires registry_schema_version=5")
            _require_non_null(self.registry_schema_digest, "registry_schema_digest", state)
            for field_name in (
                "runtime_generation",
                "supervisor_process_identity_digest",
                "migration_attestation_digest",
                "profile_id",
                "activation_generation",
                "intent_digest",
                "profile_digest",
                "previous_activation_authority_digest",
                "activation_authority_digest",
                "grant_set_digest",
                "capability_digest",
                "authority_store_id",
                "authority_history_tip_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            self._require_non_executable_methods(state)
        elif state == "profile_invalid":
            _require_reason(
                self.reason_code,
                (
                    "ACTIVATION_PROFILE_INVALID",
                    "ACTIVATION_BINDING_MISMATCH",
                    "GRANT_POLICY_INVALID",
                ),
                state,
            )
            self._require_registry_version(6, state)
            _require_non_null(self.candidate, "candidate", state)
            for field_name in (
                "runtime_generation",
                "supervisor_process_identity_digest",
                "grant_set_digest",
                "capability_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            self._require_non_executable_methods(state)
        elif state == "profile_verified":
            _require_reason(self.reason_code, ("none",), state)
            self._require_registry_version(6, state)
            for field_name in (
                "candidate",
                "migration_attestation_digest",
                "profile_id",
                "activation_generation",
                "intent_digest",
                "profile_digest",
                "activation_authority_digest",
                "authority_store_id",
                "authority_history_tip_digest",
            ):
                _require_non_null(getattr(self, field_name), field_name, state)
            self._require_history_tip_match(state)
            for field_name in (
                "runtime_generation",
                "supervisor_process_identity_digest",
                "grant_set_digest",
                "capability_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            self._require_non_executable_methods(state)
        elif state == "starting":
            _require_reason(self.reason_code, ("none",), state)
            self._require_profile_verified_bindings(state)
            _require_non_null(self.runtime_generation, "runtime_generation", state)
            _require_non_null(
                self.supervisor_process_identity_digest,
                "supervisor_process_identity_digest",
                state,
            )
            for field_name in (
                "grant_set_digest",
                "capability_digest",
                "broker_catalog_digest",
                "tool_surface_digest",
                "route_catalog_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            if self.route_profile_ids:
                raise ValueError("starting state requires route_profile_ids=()")
            self._require_non_executable_methods(state)
        elif state == "active":
            _require_reason(self.reason_code, ("none",), state)
            self._require_registry_version(6, state)
            self._require_active_bindings(state)
            self._require_active_methods(state)
        elif state == "degraded":
            _require_reason(
                self.reason_code,
                (
                    "GRANT_BINDING_MISMATCH",
                    "PLANNER_UNAVAILABLE",
                    "CAPABILITY_UNAVAILABLE",
                    "STALE_ADAPTER_GENERATION",
                ),
                state,
            )
            self._require_registry_version(6, state)
            self._require_active_bindings(state)
            self._require_active_methods(state)
        else:
            _require_reason(
                self.reason_code,
                (
                    "ACTIVATION_HISTORY_CONFLICT",
                    "CUTOVER_RECOVERY_REQUIRED",
                    "ABORT_OR_APPLY_REQUIRED",
                    "RECONCILE_INPUT_REQUIRED",
                ),
                state,
            )
            for field_name in (
                "runtime_generation",
                "supervisor_process_identity_digest",
                "grant_set_digest",
                "capability_digest",
            ):
                _require_null(getattr(self, field_name), field_name, state)
            self._require_non_executable_methods(state)
        return self

    def _require_registry_version(self, expected: Literal[5, 6], state: str) -> None:
        if self.registry_schema_version != expected:
            raise ValueError(f"{state} state requires registry_schema_version={expected}")
        _require_non_null(self.registry_schema_digest, "registry_schema_digest", state)

    def _require_history_tip_match(self, state: str) -> None:
        if self.authority_history_tip_digest != self.activation_authority_digest:
            raise ValueError(
                f"{state} state requires authority_history_tip_digest to equal "
                "activation_authority_digest"
            )

    def _require_profile_verified_bindings(self, state: str) -> None:
        self._require_registry_version(6, state)
        for field_name in (
            "candidate",
            "migration_attestation_digest",
            "profile_id",
            "activation_generation",
            "intent_digest",
            "profile_digest",
            "activation_authority_digest",
            "authority_store_id",
            "authority_history_tip_digest",
        ):
            _require_non_null(getattr(self, field_name), field_name, state)
        self._require_history_tip_match(state)

    def _require_active_bindings(self, state: str) -> None:
        self._require_profile_verified_bindings(state)
        for field_name in (
            "runtime_generation",
            "supervisor_process_identity_digest",
            "grant_set_digest",
            "capability_digest",
            "broker_catalog_digest",
            "tool_surface_digest",
            "route_catalog_digest",
        ):
            _require_non_null(getattr(self, field_name), field_name, state)
        if not self.route_profile_ids:
            raise ValueError(f"{state} state requires non-empty route_profile_ids")

    def _require_non_executable_methods(self, state: str) -> None:
        if any(item.backend_kind in ("native", "caller_driver") for item in self.methods):
            raise ValueError(
                f"{state} state cannot contain configured native/caller-driver method rows"
            )

    def _require_active_methods(self, state: str) -> None:
        if any(item.backend_kind == "unconfigured" for item in self.methods):
            raise ValueError(
                f"{state} state requires methods to be configured or reference-only"
            )
        assert self.runtime_generation is not None
        for item in self.methods:
            if (
                item.backend_kind in ("native", "caller_driver")
                and item.adapter_generation != self.runtime_generation
            ):
                raise ValueError(
                    f"{state} executable method adapter_generation must equal "
                    "runtime_generation"
                )

    @model_validator(mode="after")
    def readback_digest_is_content_bound(self) -> Self:
        if self.readback_digest != _compute_self_digest(self, "readback_digest"):
            raise ValueError("readback digest does not match canonical readback bytes")
        return self


PROVIDER_READY_RUNTIME_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    ACTIVATION_READBACK_SCHEMA_VERSION: ActivationReadback,
    WORKBENCH_GRANT_SET_SCHEMA_VERSION: WorkbenchGrantSet,
}
RUNTIME_SCHEMA_MODELS = PROVIDER_READY_RUNTIME_SCHEMA_MODELS

__all__ = [
    "ACTIVATION_READBACK_SCHEMA_VERSION",
    "PROVIDER_READY_RUNTIME_SCHEMA_MODELS",
    "RUNTIME_SCHEMA_MODELS",
    "WORKBENCH_GRANT_SET_SCHEMA_VERSION",
    "ActivationReadback",
    "ActivationReadbackReasonCode",
    "ActivationReadbackState",
    "BackendAvailability",
    "EvidenceSource",
    "IssuedWorkbenchGrant",
    "OperatorEpochKind",
    "PlannerReadback",
    "ReadbackBackendKind",
    "SessionBindingPolicy",
    "WorkbenchGrantSet",
]
