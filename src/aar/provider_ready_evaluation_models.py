"""Strict, inert evaluator evidence and paired-planning model contracts.

This module validates only supplied JSON-compatible bytes and document-local
relationships.  It does not collect evidence, inspect a provider, allocate an
attempt, launch a contender, or act as runtime authority.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self, cast

from pydantic import Field, model_validator

from aar.broker_models import ModelRouteValue
from aar.canonical import canonical_sha256
from aar.schemas import Digest, OpaqueToken, StrictModel

PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION = "aar.provider-alias-attestation.v1"
EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION = (
    "aar.evaluation-evidence-classification.v1"
)
PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION = "aar.paired-evaluation-admission.v1"

_MAX_COUNTER = 9_223_372_036_854_775_807
UnixMs = Annotated[int, Field(ge=0, le=_MAX_COUNTER, strict=True)]

EvaluationArm = Literal["aar", "prime"]
EvaluationComponent = Literal["provider", "model", "reasoning", "fallback", "cache"]
EvidenceTier = Literal["attested", "observed", "requested_only", "contradicted"]
RouteQualification = Literal[
    "aar_live_qualified",
    "prime_live_qualified",
    "insufficient",
    "contradicted",
]
UsageTier = Literal[
    "request_receipt",
    "session_aggregate",
    "partial_events",
    "unavailable",
]
AttemptVisibility = Literal["complete", "aggregate_only", "unknown"]
RequestedFallbackPolicy = Literal["none"]
RequestedCachePolicy = Literal["disabled"]
EffectiveFallbackPolicy = Literal["none", "fallback_used"] | None
EffectiveCachePolicy = Literal["disabled", "cache_used"] | None

ContradictionComponents = Annotated[
    tuple[EvaluationComponent, ...], Field(min_length=0, max_length=5)
]
SourceReceiptDigests = Annotated[tuple[Digest, ...], Field(min_length=1, max_length=128)]
ArmOrder = Annotated[tuple[EvaluationArm, ...], Field(min_length=2, max_length=2)]

_COMPONENTS: tuple[EvaluationComponent, ...] = (
    "provider",
    "model",
    "reasoning",
    "fallback",
    "cache",
)
_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def _compute_self_digest(document: StrictModel, digest_field: str) -> str:
    """Hash one complete document after omitting only its root digest field."""

    payload = document.model_dump(mode="json")
    try:
        del payload[digest_field]
    except KeyError as error:
        raise ValueError(f"self-digest field {digest_field!r} is missing") from error
    return canonical_sha256(payload)


def _issue_document(
    model_type: type[StrictModel], payload: dict[str, object], digest_field: str
) -> StrictModel:
    """Issue one self-digested document without adding a digest cycle."""

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


def _require_digest(document: StrictModel, digest_field: str, message: str) -> None:
    observed = getattr(document, digest_field)
    expected = _compute_self_digest(document, digest_field)
    if observed != expected:
        raise ValueError(message)


class ProviderAliasAttestation(StrictModel):
    """Digest-bound, credential-free normalization of the Prime launcher alias."""

    schema_version: Literal["aar.provider-alias-attestation.v1"]
    launcher_alias: Literal["hermes-codex"]
    canonical_provider_identity: Literal["openai-codex"]
    wire_api: Literal["openai-codex-responses"]
    prime_executable_digest: Digest
    prime_config_digest: Digest
    model_registry_digest: Digest
    provider_entry_digest: Digest
    credential_resolver_executable_digest: Digest
    model: Literal["gpt-5.6-luna"]
    requested_reasoning: Literal["max"]
    created_at_unix_ms: UnixMs
    alias_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.provider-alias-attestation.v1"],
        launcher_alias: Literal["hermes-codex"],
        canonical_provider_identity: Literal["openai-codex"],
        wire_api: Literal["openai-codex-responses"],
        prime_executable_digest: Digest,
        prime_config_digest: Digest,
        model_registry_digest: Digest,
        provider_entry_digest: Digest,
        credential_resolver_executable_digest: Digest,
        model: Literal["gpt-5.6-luna"],
        requested_reasoning: Literal["max"],
        created_at_unix_ms: UnixMs,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "launcher_alias": launcher_alias,
            "canonical_provider_identity": canonical_provider_identity,
            "wire_api": wire_api,
            "prime_executable_digest": prime_executable_digest,
            "prime_config_digest": prime_config_digest,
            "model_registry_digest": model_registry_digest,
            "provider_entry_digest": provider_entry_digest,
            "credential_resolver_executable_digest": credential_resolver_executable_digest,
            "model": model,
            "requested_reasoning": requested_reasoning,
            "created_at_unix_ms": created_at_unix_ms,
        }
        return cast(Self, _issue_document(cls, payload, "alias_digest"))

    @model_validator(mode="after")
    def alias_constants_are_frozen(self) -> Self:
        expected = {
            "schema_version": PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION,
            "launcher_alias": "hermes-codex",
            "canonical_provider_identity": "openai-codex",
            "wire_api": "openai-codex-responses",
            "model": "gpt-5.6-luna",
            "requested_reasoning": "max",
        }
        for field_name, expected_value in expected.items():
            if getattr(self, field_name) != expected_value:
                raise ValueError(f"{field_name} must equal {expected_value}")
        return self

    @model_validator(mode="after")
    def alias_digest_is_content_bound(self) -> Self:
        _require_digest(
            self,
            "alias_digest",
            "alias digest does not match canonical alias bytes",
        )
        return self


class EvaluationEvidenceClassification(StrictModel):
    """Strict local projection of one arm's supplied evaluation evidence."""

    schema_version: Literal["aar.evaluation-evidence-classification.v1"]
    run_id: OpaqueToken
    arm: EvaluationArm
    attempt_id: OpaqueToken
    requested_provider: ModelRouteValue
    requested_model: ModelRouteValue
    requested_reasoning: ModelRouteValue | None
    requested_fallback_policy: RequestedFallbackPolicy
    requested_cache_policy: RequestedCachePolicy
    effective_provider: ModelRouteValue | None
    effective_model: ModelRouteValue | None
    effective_reasoning: ModelRouteValue | None
    effective_fallback_policy: EffectiveFallbackPolicy
    effective_cache_policy: EffectiveCachePolicy
    provider_tier: EvidenceTier
    model_tier: EvidenceTier
    reasoning_tier: EvidenceTier
    fallback_tier: EvidenceTier
    cache_tier: EvidenceTier
    route_qualification: RouteQualification
    usage_tier: UsageTier
    attempt_visibility: AttemptVisibility
    contradiction_components: ContradictionComponents
    source_receipt_digests: SourceReceiptDigests
    classification_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.evaluation-evidence-classification.v1"],
        run_id: OpaqueToken,
        arm: EvaluationArm,
        attempt_id: OpaqueToken,
        requested_provider: ModelRouteValue,
        requested_model: ModelRouteValue,
        requested_reasoning: ModelRouteValue | None,
        requested_fallback_policy: RequestedFallbackPolicy,
        requested_cache_policy: RequestedCachePolicy,
        effective_provider: ModelRouteValue | None,
        effective_model: ModelRouteValue | None,
        effective_reasoning: ModelRouteValue | None,
        effective_fallback_policy: EffectiveFallbackPolicy,
        effective_cache_policy: EffectiveCachePolicy,
        provider_tier: EvidenceTier,
        model_tier: EvidenceTier,
        reasoning_tier: EvidenceTier,
        fallback_tier: EvidenceTier,
        cache_tier: EvidenceTier,
        route_qualification: RouteQualification,
        usage_tier: UsageTier,
        attempt_visibility: AttemptVisibility,
        contradiction_components: tuple[EvaluationComponent, ...],
        source_receipt_digests: tuple[Digest, ...],
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "run_id": run_id,
            "arm": arm,
            "attempt_id": attempt_id,
            "requested_provider": requested_provider,
            "requested_model": requested_model,
            "requested_reasoning": requested_reasoning,
            "requested_fallback_policy": requested_fallback_policy,
            "requested_cache_policy": requested_cache_policy,
            "effective_provider": effective_provider,
            "effective_model": effective_model,
            "effective_reasoning": effective_reasoning,
            "effective_fallback_policy": effective_fallback_policy,
            "effective_cache_policy": effective_cache_policy,
            "provider_tier": provider_tier,
            "model_tier": model_tier,
            "reasoning_tier": reasoning_tier,
            "fallback_tier": fallback_tier,
            "cache_tier": cache_tier,
            "route_qualification": route_qualification,
            "usage_tier": usage_tier,
            "attempt_visibility": attempt_visibility,
            "contradiction_components": tuple(sorted(contradiction_components)),
            "source_receipt_digests": tuple(sorted(source_receipt_digests)),
        }
        return cast(Self, _issue_document(cls, payload, "classification_digest"))

    @model_validator(mode="after")
    def prime_requested_provider_is_canonical(self) -> Self:
        if self.arm == "prime" and self.requested_provider != "openai-codex":
            raise ValueError("prime requested_provider must equal openai-codex")
        return self

    @model_validator(mode="after")
    def component_tiers_match_effective_values(self) -> Self:
        values = {
            "provider": (
                self.requested_provider,
                self.effective_provider,
                self.provider_tier,
            ),
            "model": (self.requested_model, self.effective_model, self.model_tier),
            "reasoning": (
                self.requested_reasoning,
                self.effective_reasoning,
                self.reasoning_tier,
            ),
            "fallback": (
                self.requested_fallback_policy,
                self.effective_fallback_policy,
                self.fallback_tier,
            ),
            "cache": (
                self.requested_cache_policy,
                self.effective_cache_policy,
                self.cache_tier,
            ),
        }
        for component, (requested, effective, tier) in values.items():
            if tier in ("attested", "observed"):
                if requested is None:
                    raise ValueError(
                        f"{component}_tier={tier} requires a non-null requested value"
                    )
                if effective is None or effective != requested:
                    raise ValueError(
                        f"{component}_tier={tier} requires effective value equal to requested"
                    )
            elif tier == "requested_only":
                if effective is not None:
                    raise ValueError(
                        f"{component}_tier=requested_only requires effective value=null"
                    )
            else:
                if effective is None or (
                    requested is not None and effective == requested
                ):
                    raise ValueError(
                        f"{component}_tier=contradicted requires a differing effective value"
                    )
        return self

    @model_validator(mode="after")
    def usage_visibility_pair_is_legal(self) -> Self:
        legal_pairs = {
            ("request_receipt", "complete"),
            ("session_aggregate", "aggregate_only"),
            ("partial_events", "aggregate_only"),
            ("partial_events", "unknown"),
            ("unavailable", "unknown"),
            ("unavailable", "complete"),
        }
        if (self.usage_tier, self.attempt_visibility) not in legal_pairs:
            raise ValueError("usage_tier and attempt_visibility are not a legal pair")
        return self

    @model_validator(mode="after")
    def qualification_and_contradictions_are_exact(self) -> Self:
        _require_sorted_unique(self.contradiction_components, "contradiction_components")
        _require_sorted_unique(self.source_receipt_digests, "source_receipt_digests")

        tier_values = {
            "provider": self.provider_tier,
            "model": self.model_tier,
            "reasoning": self.reasoning_tier,
            "fallback": self.fallback_tier,
            "cache": self.cache_tier,
        }
        expected_contradictions = tuple(
            sorted(component for component, tier in tier_values.items() if tier == "contradicted")
        )
        if self.contradiction_components != expected_contradictions:
            raise ValueError(
                "contradiction_components must equal the sorted contradicted component set"
            )

        if expected_contradictions:
            if self.route_qualification != "contradicted":
                raise ValueError(
                    "non-empty contradiction_components require route_qualification=contradicted"
                )
            return self
        if self.route_qualification == "contradicted":
            raise ValueError(
                "route_qualification=contradicted requires non-empty contradiction_components"
            )

        aar_live = self.arm == "aar" and all(
            tier_values[component] == "attested" for component in _COMPONENTS
        ) and self.usage_tier == "request_receipt" and self.attempt_visibility == "complete"
        prime_live = self.arm == "prime" and (
            self.provider_tier in ("observed", "attested")
            and self.model_tier in ("observed", "attested")
            and self.reasoning_tier in ("requested_only", "observed", "attested")
            and self.fallback_tier in ("observed", "attested")
            and self.cache_tier in ("observed", "attested")
        )
        expected_qualification: RouteQualification = (
            "aar_live_qualified"
            if aar_live
            else "prime_live_qualified"
            if prime_live
            else "insufficient"
        )
        if self.route_qualification != expected_qualification:
            raise ValueError(
                "route_qualification does not match component, usage and visibility evidence"
            )
        return self

    @model_validator(mode="after")
    def classification_digest_is_content_bound(self) -> Self:
        _require_digest(
            self,
            "classification_digest",
            "classification digest does not match canonical classification bytes",
        )
        return self


class PairedEvaluationAdmission(StrictModel):
    """Digest-bound paired-evaluation planning bytes with no launch authority."""

    schema_version: Literal["aar.paired-evaluation-admission.v1"]
    aar_qualification_run_id: OpaqueToken
    prime_qualification_run_id: OpaqueToken
    paired_run_id: OpaqueToken
    status: Literal["benchmark_ready_planned"]
    created_at_unix_ms: UnixMs
    expires_at_unix_ms: UnixMs
    candidate_digest: Digest
    aar_profile_digest: Digest
    prime_launch_digest: Digest
    prime_alias_digest: Digest
    aar_classification_digest: Digest
    prime_classification_digest: Digest
    case_digest: Digest
    fixture_digest: Digest
    hidden_oracle_digest: Digest
    artifact_contract_digest: Digest
    tool_network_policy_digest: Digest
    protocol_digest: Digest
    budget_digest: Digest
    stop_matrix_digest: Digest
    arm_order: ArmOrder
    operator_run_authority_digest: Digest
    admission_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.paired-evaluation-admission.v1"],
        aar_qualification_run_id: OpaqueToken,
        prime_qualification_run_id: OpaqueToken,
        paired_run_id: OpaqueToken,
        status: Literal["benchmark_ready_planned"],
        created_at_unix_ms: UnixMs,
        expires_at_unix_ms: UnixMs,
        candidate_digest: Digest,
        aar_profile_digest: Digest,
        prime_launch_digest: Digest,
        prime_alias_digest: Digest,
        aar_classification_digest: Digest,
        prime_classification_digest: Digest,
        case_digest: Digest,
        fixture_digest: Digest,
        hidden_oracle_digest: Digest,
        artifact_contract_digest: Digest,
        tool_network_policy_digest: Digest,
        protocol_digest: Digest,
        budget_digest: Digest,
        stop_matrix_digest: Digest,
        arm_order: tuple[EvaluationArm, ...],
        operator_run_authority_digest: Digest,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "aar_qualification_run_id": aar_qualification_run_id,
            "prime_qualification_run_id": prime_qualification_run_id,
            "paired_run_id": paired_run_id,
            "status": status,
            "created_at_unix_ms": created_at_unix_ms,
            "expires_at_unix_ms": expires_at_unix_ms,
            "candidate_digest": candidate_digest,
            "aar_profile_digest": aar_profile_digest,
            "prime_launch_digest": prime_launch_digest,
            "prime_alias_digest": prime_alias_digest,
            "aar_classification_digest": aar_classification_digest,
            "prime_classification_digest": prime_classification_digest,
            "case_digest": case_digest,
            "fixture_digest": fixture_digest,
            "hidden_oracle_digest": hidden_oracle_digest,
            "artifact_contract_digest": artifact_contract_digest,
            "tool_network_policy_digest": tool_network_policy_digest,
            "protocol_digest": protocol_digest,
            "budget_digest": budget_digest,
            "stop_matrix_digest": stop_matrix_digest,
            "arm_order": arm_order,
            "operator_run_authority_digest": operator_run_authority_digest,
        }
        return cast(Self, _issue_document(cls, payload, "admission_digest"))

    @classmethod
    def compose(
        cls,
        *,
        alias_attestation: ProviderAliasAttestation,
        aar_classification: EvaluationEvidenceClassification,
        prime_classification: EvaluationEvidenceClassification,
        paired_run_id: OpaqueToken,
        created_at_unix_ms: UnixMs,
        expires_at_unix_ms: UnixMs,
        candidate_digest: Digest,
        aar_profile_digest: Digest,
        prime_launch_digest: Digest,
        case_digest: Digest,
        fixture_digest: Digest,
        hidden_oracle_digest: Digest,
        artifact_contract_digest: Digest,
        tool_network_policy_digest: Digest,
        protocol_digest: Digest,
        budget_digest: Digest,
        stop_matrix_digest: Digest,
        arm_order: tuple[EvaluationArm, ...],
        operator_run_authority_digest: Digest,
        prime_alias_digest: Digest | None = None,
        aar_classification_digest: Digest | None = None,
        prime_classification_digest: Digest | None = None,
    ) -> Self:
        """Compose an admission from already-validated qualified evidence only."""

        if not isinstance(alias_attestation, ProviderAliasAttestation):
            raise TypeError("alias_attestation must be a validated ProviderAliasAttestation")
        if not isinstance(aar_classification, EvaluationEvidenceClassification):
            raise TypeError("aar_classification must be a validated classification")
        if not isinstance(prime_classification, EvaluationEvidenceClassification):
            raise TypeError("prime_classification must be a validated classification")

        # Revalidate current bytes rather than trusting a once-validated mutable
        # object whose fields or self-digest may have been changed afterward.
        alias_attestation = ProviderAliasAttestation.model_validate(
            alias_attestation.model_dump(mode="python"), strict=True
        )
        aar_classification = EvaluationEvidenceClassification.model_validate(
            aar_classification.model_dump(mode="python"), strict=True
        )
        prime_classification = EvaluationEvidenceClassification.model_validate(
            prime_classification.model_dump(mode="python"), strict=True
        )

        if (
            aar_classification.arm != "aar"
            or aar_classification.route_qualification != "aar_live_qualified"
        ):
            raise ValueError("AAR classification must be aar_live_qualified")
        if (
            prime_classification.arm != "prime"
            or prime_classification.route_qualification != "prime_live_qualified"
        ):
            raise ValueError("Prime classification must be prime_live_qualified")
        if aar_classification.contradiction_components:
            raise ValueError("AAR classification must have no contradiction components")
        if prime_classification.contradiction_components:
            raise ValueError("Prime classification must have no contradiction components")
        if (
            aar_classification.usage_tier != "request_receipt"
            or aar_classification.attempt_visibility != "complete"
        ):
            raise ValueError("AAR classification requires request_receipt/complete evidence")
        if prime_classification.requested_provider != alias_attestation.canonical_provider_identity:
            raise ValueError("Prime requested provider must equal alias canonical provider")
        if prime_classification.requested_model != alias_attestation.model:
            raise ValueError("Prime requested model must equal alias model")
        if prime_classification.requested_reasoning != alias_attestation.requested_reasoning:
            raise ValueError("Prime requested reasoning must equal alias requested reasoning")
        if aar_classification.run_id == prime_classification.run_id:
            raise ValueError("qualification run IDs must be distinct")
        if paired_run_id in (aar_classification.run_id, prime_classification.run_id):
            raise ValueError("paired run ID must differ from qualification run IDs")
        if (
            prime_alias_digest is not None
            and prime_alias_digest != alias_attestation.alias_digest
        ):
            raise ValueError("prime alias digest must equal alias attestation digest")
        if (
            aar_classification_digest is not None
            and aar_classification_digest != aar_classification.classification_digest
        ):
            raise ValueError("AAR classification digest must equal classification digest")
        if (
            prime_classification_digest is not None
            and prime_classification_digest != prime_classification.classification_digest
        ):
            raise ValueError("Prime classification digest must equal classification digest")

        return cls.issue(
            schema_version=PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION,
            aar_qualification_run_id=aar_classification.run_id,
            prime_qualification_run_id=prime_classification.run_id,
            paired_run_id=paired_run_id,
            status="benchmark_ready_planned",
            created_at_unix_ms=created_at_unix_ms,
            expires_at_unix_ms=expires_at_unix_ms,
            candidate_digest=candidate_digest,
            aar_profile_digest=aar_profile_digest,
            prime_launch_digest=prime_launch_digest,
            prime_alias_digest=alias_attestation.alias_digest,
            aar_classification_digest=aar_classification.classification_digest,
            prime_classification_digest=prime_classification.classification_digest,
            case_digest=case_digest,
            fixture_digest=fixture_digest,
            hidden_oracle_digest=hidden_oracle_digest,
            artifact_contract_digest=artifact_contract_digest,
            tool_network_policy_digest=tool_network_policy_digest,
            protocol_digest=protocol_digest,
            budget_digest=budget_digest,
            stop_matrix_digest=stop_matrix_digest,
            arm_order=arm_order,
            operator_run_authority_digest=operator_run_authority_digest,
        )

    @classmethod
    def from_qualified_evidence(cls, **kwargs: object) -> Self:
        """Compatibility spelling for the pure composition constructor."""

        return cls.compose(**kwargs)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def admission_fields_are_coherent(self) -> Self:
        if len({
            self.aar_qualification_run_id,
            self.prime_qualification_run_id,
            self.paired_run_id,
        }) != 3:
            raise ValueError("qualification and paired run IDs must be pairwise distinct")
        if self.created_at_unix_ms >= self.expires_at_unix_ms:
            raise ValueError("admission creation time must precede expiry time")
        if self.expires_at_unix_ms > self.created_at_unix_ms + 900_000:
            raise ValueError("admission expiry cannot exceed creation by 900000 ms")
        if len(self.arm_order) != 2 or set(self.arm_order) != {"aar", "prime"}:
            raise ValueError("arm_order must contain aar and prime exactly once")
        return self

    @model_validator(mode="after")
    def admission_digest_is_content_bound(self) -> Self:
        _require_digest(
            self,
            "admission_digest",
            "admission digest does not match canonical admission bytes",
        )
        return self


PROVIDER_READY_EVALUATION_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION: ProviderAliasAttestation,
    EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION: EvaluationEvidenceClassification,
    PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION: PairedEvaluationAdmission,
}
EVALUATION_SCHEMA_MODELS = PROVIDER_READY_EVALUATION_SCHEMA_MODELS

__all__ = [
    "EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION",
    "EVALUATION_SCHEMA_MODELS",
    "PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION",
    "PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION",
    "PROVIDER_READY_EVALUATION_SCHEMA_MODELS",
    "ArmOrder",
    "AttemptVisibility",
    "ContradictionComponents",
    "EvaluationArm",
    "EvaluationComponent",
    "EvaluationEvidenceClassification",
    "EvidenceTier",
    "PairedEvaluationAdmission",
    "ProviderAliasAttestation",
    "RouteQualification",
    "SourceReceiptDigests",
    "UnixMs",
    "UsageTier",
]
