from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_evaluation_models import (
    EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
    EVALUATION_SCHEMA_MODELS,
    PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION,
    PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION,
    PROVIDER_READY_EVALUATION_SCHEMA_MODELS,
    EvaluationEvidenceClassification,
    PairedEvaluationAdmission,
    ProviderAliasAttestation,
)
from aar.schemas import StrictModel

DIGEST = canonical_sha256({"fixture": "evaluation-models"})
MAX_COUNTER = 9_223_372_036_854_775_807
COMPONENTS = ("provider", "model", "reasoning", "fallback", "cache")
TIERS = ("attested", "observed", "requested_only", "contradicted")
LEGAL_USAGE_VISIBILITY = (
    ("request_receipt", "complete"),
    ("session_aggregate", "aggregate_only"),
    ("partial_events", "aggregate_only"),
    ("partial_events", "unknown"),
    ("unavailable", "unknown"),
    ("unavailable", "complete"),
)


def _recompute_root(payload: dict[str, Any], digest_field: str) -> dict[str, Any]:
    amended = dict(payload)
    amended.pop(digest_field, None)
    amended[digest_field] = canonical_sha256(amended)
    return amended


def _validate_json(model_type: type[StrictModel], payload: dict[str, Any]) -> StrictModel:
    return model_type.model_validate_json(json.dumps(payload), strict=True)


def _schema_node(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        reference = node["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/$defs/")
        definitions = root["$defs"]
        assert isinstance(definitions, dict)
        node = definitions[reference.removeprefix("#/$defs/")]
        assert isinstance(node, dict)
    return node


def _schema_property(root: dict[str, Any], *path: str) -> dict[str, Any]:
    node = root
    for part in path:
        node = _schema_node(root, node)
        properties = node["properties"]
        assert isinstance(properties, dict)
        node = properties[part]
        assert isinstance(node, dict)
    return _schema_node(root, node)


def _schema_objects(root: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(node: object) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        if node.get("type") == "object":
            found.append(node)
        for child in node.values():
            visit(child)

    visit(root)
    return found


def _alias_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION,
        "launcher_alias": "hermes-codex",
        "canonical_provider_identity": "openai-codex",
        "wire_api": "openai-codex-responses",
        "prime_executable_digest": DIGEST,
        "prime_config_digest": DIGEST,
        "model_registry_digest": DIGEST,
        "provider_entry_digest": DIGEST,
        "credential_resolver_executable_digest": DIGEST,
        "model": "gpt-5.6-luna",
        "requested_reasoning": "max",
        "created_at_unix_ms": 100,
    }
    payload.update(updates)
    return _recompute_root(payload, "alias_digest")


def _alias(**updates: Any) -> ProviderAliasAttestation:
    return _validate_json(ProviderAliasAttestation, _alias_payload(**updates))  # type: ignore[return-value]


def _classification_payload(
    *,
    arm: str = "aar",
    run_id: str = "aar-run",
    attempt_id: str = "aar-attempt",
    requested_provider: str = "openai-codex",
    requested_model: str = "gpt-5.6-luna",
    requested_reasoning: str | None = "max",
    effective_provider: str | None = "openai-codex",
    effective_model: str | None = "gpt-5.6-luna",
    effective_reasoning: str | None = "max",
    effective_fallback_policy: str | None = "none",
    effective_cache_policy: str | None = "disabled",
    provider_tier: str = "attested",
    model_tier: str = "attested",
    reasoning_tier: str = "attested",
    fallback_tier: str = "attested",
    cache_tier: str = "attested",
    route_qualification: str | None = None,
    usage_tier: str = "request_receipt",
    attempt_visibility: str = "complete",
    contradiction_components: tuple[str, ...] | None = None,
    source_receipt_digests: tuple[str, ...] = (DIGEST,),
) -> dict[str, Any]:
    tiers = {
        "provider": provider_tier,
        "model": model_tier,
        "reasoning": reasoning_tier,
        "fallback": fallback_tier,
        "cache": cache_tier,
    }
    if contradiction_components is None:
        contradiction_components = tuple(
            sorted(component for component, tier in tiers.items() if tier == "contradicted")
        )
    if route_qualification is None:
        if contradiction_components:
            route_qualification = "contradicted"
        else:
            aar_live = arm == "aar" and all(
                tiers[component] == "attested" for component in COMPONENTS
            ) and usage_tier == "request_receipt" and attempt_visibility == "complete"
            prime_live = arm == "prime" and (
                provider_tier in ("observed", "attested")
                and model_tier in ("observed", "attested")
                and reasoning_tier in ("requested_only", "observed", "attested")
                and fallback_tier in ("observed", "attested")
                and cache_tier in ("observed", "attested")
            )
            route_qualification = (
                "aar_live_qualified"
                if aar_live
                else "prime_live_qualified"
                if prime_live
                else "insufficient"
            )
    payload: dict[str, Any] = {
        "schema_version": EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
        "run_id": run_id,
        "arm": arm,
        "attempt_id": attempt_id,
        "requested_provider": requested_provider,
        "requested_model": requested_model,
        "requested_reasoning": requested_reasoning,
        "requested_fallback_policy": "none",
        "requested_cache_policy": "disabled",
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
        "contradiction_components": contradiction_components,
        "source_receipt_digests": source_receipt_digests,
    }
    return _recompute_root(payload, "classification_digest")


def _classification(**updates: Any) -> EvaluationEvidenceClassification:
    payload = _classification_payload(**updates)
    return _validate_json(EvaluationEvidenceClassification, payload)  # type: ignore[return-value]


def _paired_payload(**updates: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION,
        "aar_qualification_run_id": "aar-run",
        "prime_qualification_run_id": "prime-run",
        "paired_run_id": "paired-run",
        "status": "benchmark_ready_planned",
        "created_at_unix_ms": 100,
        "expires_at_unix_ms": 900_100,
        "candidate_digest": DIGEST,
        "aar_profile_digest": DIGEST,
        "prime_launch_digest": DIGEST,
        "prime_alias_digest": _alias().alias_digest,
        "aar_classification_digest": _classification().classification_digest,
        "prime_classification_digest": _classification(
            arm="prime",
            run_id="prime-run",
            attempt_id="prime-attempt",
            provider_tier="observed",
            model_tier="observed",
            reasoning_tier="requested_only",
            effective_reasoning=None,
            fallback_tier="observed",
            cache_tier="observed",
            effective_provider="openai-codex",
            effective_model="gpt-5.6-luna",
            effective_fallback_policy="none",
            effective_cache_policy="disabled",
            usage_tier="unavailable",
            attempt_visibility="unknown",
        ).classification_digest,
        "case_digest": DIGEST,
        "fixture_digest": DIGEST,
        "hidden_oracle_digest": DIGEST,
        "artifact_contract_digest": DIGEST,
        "tool_network_policy_digest": DIGEST,
        "protocol_digest": DIGEST,
        "budget_digest": DIGEST,
        "stop_matrix_digest": DIGEST,
        "arm_order": ("aar", "prime"),
        "operator_run_authority_digest": DIGEST,
    }
    payload.update(updates)
    return _recompute_root(payload, "admission_digest")


def _paired(**updates: Any) -> PairedEvaluationAdmission:
    return _validate_json(PairedEvaluationAdmission, _paired_payload(**updates))  # type: ignore[return-value]


def _prime_classification(**updates: Any) -> EvaluationEvidenceClassification:
    defaults: dict[str, Any] = {
        "arm": "prime",
        "run_id": "prime-run",
        "attempt_id": "prime-attempt",
        "provider_tier": "observed",
        "model_tier": "observed",
        "reasoning_tier": "requested_only",
        "effective_reasoning": None,
        "fallback_tier": "observed",
        "cache_tier": "observed",
        "usage_tier": "unavailable",
        "attempt_visibility": "unknown",
    }
    defaults.update(updates)
    return _classification(**defaults)


def _compose(**updates: Any) -> PairedEvaluationAdmission:
    alias = updates.pop("alias_attestation", _alias())
    aar = updates.pop("aar_classification", _classification())
    prime = updates.pop("prime_classification", _prime_classification())
    values: dict[str, Any] = {
        "alias_attestation": alias,
        "aar_classification": aar,
        "prime_classification": prime,
        "paired_run_id": "paired-run",
        "created_at_unix_ms": 100,
        "expires_at_unix_ms": 900_100,
        "candidate_digest": DIGEST,
        "aar_profile_digest": DIGEST,
        "prime_launch_digest": DIGEST,
        "case_digest": DIGEST,
        "fixture_digest": DIGEST,
        "hidden_oracle_digest": DIGEST,
        "artifact_contract_digest": DIGEST,
        "tool_network_policy_digest": DIGEST,
        "protocol_digest": DIGEST,
        "budget_digest": DIGEST,
        "stop_matrix_digest": DIGEST,
        "arm_order": ("aar", "prime"),
        "operator_run_authority_digest": DIGEST,
    }
    values.update(updates)
    return PairedEvaluationAdmission.compose(**values)


def test_provider_ready_evaluation_model_module_exists() -> None:
    module_spec = importlib.util.find_spec("aar.provider_ready_evaluation_models")
    assert module_spec is not None, "aar.provider_ready_evaluation_models module is missing"


def test_exact_three_top_level_models_and_registry_inventory() -> None:
    module = importlib.import_module("aar.provider_ready_evaluation_models")
    expected_registry = {
        PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION: ProviderAliasAttestation,
        EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION: EvaluationEvidenceClassification,
        PAIRED_EVALUATION_ADMISSION_SCHEMA_VERSION: PairedEvaluationAdmission,
    }
    assert expected_registry == PROVIDER_READY_EVALUATION_SCHEMA_MODELS
    assert expected_registry == EVALUATION_SCHEMA_MODELS
    assert len(PROVIDER_READY_EVALUATION_SCHEMA_MODELS) == 3
    public_models = {
        name
        for name, value in vars(module).items()
        if isinstance(value, type)
        and issubclass(value, StrictModel)
        and value is not StrictModel
    }
    assert public_models == {
        "ProviderAliasAttestation",
        "EvaluationEvidenceClassification",
        "PairedEvaluationAdmission",
    }


def test_exact_field_order_counts_required_defaults_and_json_round_trip() -> None:
    expected_fields = {
        ProviderAliasAttestation: (
            "schema_version",
            "launcher_alias",
            "canonical_provider_identity",
            "wire_api",
            "prime_executable_digest",
            "prime_config_digest",
            "model_registry_digest",
            "provider_entry_digest",
            "credential_resolver_executable_digest",
            "model",
            "requested_reasoning",
            "created_at_unix_ms",
            "alias_digest",
        ),
        EvaluationEvidenceClassification: (
            "schema_version",
            "run_id",
            "arm",
            "attempt_id",
            "requested_provider",
            "requested_model",
            "requested_reasoning",
            "requested_fallback_policy",
            "requested_cache_policy",
            "effective_provider",
            "effective_model",
            "effective_reasoning",
            "effective_fallback_policy",
            "effective_cache_policy",
            "provider_tier",
            "model_tier",
            "reasoning_tier",
            "fallback_tier",
            "cache_tier",
            "route_qualification",
            "usage_tier",
            "attempt_visibility",
            "contradiction_components",
            "source_receipt_digests",
            "classification_digest",
        ),
        PairedEvaluationAdmission: (
            "schema_version",
            "aar_qualification_run_id",
            "prime_qualification_run_id",
            "paired_run_id",
            "status",
            "created_at_unix_ms",
            "expires_at_unix_ms",
            "candidate_digest",
            "aar_profile_digest",
            "prime_launch_digest",
            "prime_alias_digest",
            "aar_classification_digest",
            "prime_classification_digest",
            "case_digest",
            "fixture_digest",
            "hidden_oracle_digest",
            "artifact_contract_digest",
            "tool_network_policy_digest",
            "protocol_digest",
            "budget_digest",
            "stop_matrix_digest",
            "arm_order",
            "operator_run_authority_digest",
            "admission_digest",
        ),
    }
    documents = (_alias(), _classification(), _prime_classification(), _paired())
    for model_type, fields in expected_fields.items():
        assert tuple(model_type.model_fields) == fields
        assert len(fields) in (13, 25, 24)
        for field_info in model_type.model_fields.values():
            assert field_info.is_required()
            assert field_info.default is None or str(field_info.default).endswith(
                "PydanticUndefined"
            )
    for document in documents:
        assert (
            type(document).model_validate_json(document.model_dump_json(), strict=True)
            == document
        )


def test_schema_is_recursive_extra_forbid_and_all_properties_required() -> None:
    for model_type, document in (
        (ProviderAliasAttestation, _alias()),
        (EvaluationEvidenceClassification, _classification()),
        (PairedEvaluationAdmission, _paired()),
    ):
        payload = document.model_dump(mode="json")
        payload["unexpected"] = True
        digest_field = {
            ProviderAliasAttestation: "alias_digest",
            EvaluationEvidenceClassification: "classification_digest",
            PairedEvaluationAdmission: "admission_digest",
        }[model_type]
        payload = _recompute_root(payload, digest_field)
        with pytest.raises(ValidationError) as extra_error:
            _validate_json(model_type, payload)
        assert any(
            error["loc"] == ("unexpected",) and error["type"] == "extra_forbidden"
            for error in extra_error.value.errors()
        )
        for object_schema in _schema_objects(model_type.model_json_schema()):
            assert object_schema["additionalProperties"] is False
            assert set(object_schema["required"]) == set(object_schema["properties"])
            for property_schema in object_schema["properties"].values():
                assert "default" not in property_schema


def test_strict_scalar_domains_reject_bool_coercion() -> None:
    alias_payload = _alias_payload(created_at_unix_ms=True)
    with pytest.raises(ValidationError):
        _validate_json(ProviderAliasAttestation, alias_payload)

    classification_payload = _classification_payload(run_id=True)
    with pytest.raises(ValidationError):
        _validate_json(EvaluationEvidenceClassification, classification_payload)

    admission_payload = _paired_payload(created_at_unix_ms=True)
    with pytest.raises(ValidationError):
        _validate_json(PairedEvaluationAdmission, admission_payload)


def test_resolved_schema_bounds_and_finite_domains_are_direct_wire_assertions() -> None:
    classification_schema = EvaluationEvidenceClassification.model_json_schema()
    admission_schema = PairedEvaluationAdmission.model_json_schema()
    contradiction = _schema_property(classification_schema, "contradiction_components")
    source_receipts = _schema_property(classification_schema, "source_receipt_digests")
    arm_order = _schema_property(admission_schema, "arm_order")
    assert contradiction["minItems"] == 0
    assert contradiction["maxItems"] == 5
    assert _schema_node(classification_schema, contradiction["items"])["enum"] == [
        "provider",
        "model",
        "reasoning",
        "fallback",
        "cache",
    ]
    assert source_receipts["minItems"] == 1
    assert source_receipts["maxItems"] == 128
    assert arm_order["minItems"] == 2
    assert arm_order["maxItems"] == 2
    assert _schema_node(admission_schema, arm_order["items"])["enum"] == ["aar", "prime"]


def test_alias_constants_and_independent_root_digest_oracles() -> None:
    alias = _alias()
    payload = alias.model_dump(mode="json")
    observed = payload.pop("alias_digest")
    assert observed == canonical_sha256(payload)
    assert alias.launcher_alias == "hermes-codex"
    assert alias.canonical_provider_identity == "openai-codex"
    assert alias.wire_api == "openai-codex-responses"
    assert alias.model == "gpt-5.6-luna"
    assert alias.requested_reasoning == "max"

    for field_name, value in (
        ("launcher_alias", "other-launcher"),
        ("canonical_provider_identity", "other-provider"),
        ("wire_api", "other-api"),
        ("model", "other-model"),
        ("requested_reasoning", "low"),
    ):
        invalid = _alias_payload(**{field_name: value})
        with pytest.raises(ValidationError, match=field_name):
            _validate_json(ProviderAliasAttestation, invalid)


def test_each_top_level_root_rejects_its_own_stale_digest() -> None:
    cases = (
        (ProviderAliasAttestation, _alias(), "alias_digest", {"created_at_unix_ms": 101}),
        (
            EvaluationEvidenceClassification,
            _classification(),
            "classification_digest",
            {"attempt_id": "changed-attempt"},
        ),
        (
            PairedEvaluationAdmission,
            _paired(),
            "admission_digest",
            {"case_digest": canonical_sha256({"changed": "case"})},
        ),
    )
    for model_type, document, _digest_field, changes in cases:
        payload = document.model_dump(mode="json")
        payload.update(changes)
        with pytest.raises(ValidationError, match="digest"):
            _validate_json(model_type, payload)


def test_each_top_level_root_has_an_independent_oracle() -> None:
    documents = (
        (_alias(), "alias_digest"),
        (_classification(), "classification_digest"),
        (_paired(), "admission_digest"),
    )
    for document, digest_field in documents:
        payload = document.model_dump(mode="json")
        observed = payload.pop(digest_field)
        assert "schema_version" in payload
        assert observed == canonical_sha256(payload)


@pytest.mark.parametrize("component", COMPONENTS)
@pytest.mark.parametrize("tier", TIERS)
def test_component_matrix_is_independent_for_each_component(component: str, tier: str) -> None:
    requested: dict[str, Any] = {
        "requested_provider": "openai-codex",
        "requested_model": "gpt-5.6-luna",
        "requested_reasoning": "max",
        "effective_provider": "openai-codex",
        "effective_model": "gpt-5.6-luna",
        "effective_reasoning": "max",
        "effective_fallback_policy": "none",
        "effective_cache_policy": "disabled",
    }
    tier_values = {name: "attested" for name in COMPONENTS}
    tier_values[component] = tier
    effective_fields = {
        "provider": "effective_provider",
        "model": "effective_model",
        "reasoning": "effective_reasoning",
        "fallback": "effective_fallback_policy",
        "cache": "effective_cache_policy",
    }
    for name, field_name in effective_fields.items():
        if tier == "requested_only" and name == component:
            requested[field_name] = None
        elif tier == "contradicted" and name == component:
            requested[field_name] = {
                "provider": "other-provider",
                "model": "other-model",
                "reasoning": "low",
                "fallback": "fallback_used",
                "cache": "cache_used",
            }[component]
    requested.update({f"{name}_tier": value for name, value in tier_values.items()})
    document = _classification(**requested)
    assert getattr(document, f"{component}_tier") == tier
    assert (component in document.contradiction_components) is (tier == "contradicted")
    effective_field = {
        "provider": "effective_provider",
        "model": "effective_model",
        "reasoning": "effective_reasoning",
        "fallback": "effective_fallback_policy",
        "cache": "effective_cache_policy",
    }[component]
    assert getattr(document, effective_field) is (
        None if tier == "requested_only" else getattr(document, effective_field)
    )


def test_null_requested_reasoning_has_only_requested_or_contradicted_seams() -> None:
    requested_only = _classification(
        requested_reasoning=None,
        effective_reasoning=None,
        reasoning_tier="requested_only",
    )
    assert requested_only.reasoning_tier == "requested_only"
    assert requested_only.effective_reasoning is None

    contradicted = _classification(
        requested_reasoning=None,
        effective_reasoning="low",
        reasoning_tier="contradicted",
    )
    assert contradicted.contradiction_components == ("reasoning",)

    for tier, effective in (("attested", "max"), ("observed", "max")):
        payload = _classification_payload(
            requested_reasoning=None,
            effective_reasoning=effective,
            reasoning_tier=tier,
            route_qualification="insufficient",
        )
        with pytest.raises(ValidationError, match="reasoning_tier"):
            _validate_json(EvaluationEvidenceClassification, payload)


def test_contradiction_set_is_exact_sorted_unique_and_qualification_bounded() -> None:
    contradicted = _classification(
        provider_tier="contradicted",
        effective_provider="other-provider",
    )
    assert contradicted.contradiction_components == ("provider",)
    assert contradicted.route_qualification == "contradicted"

    for components, expected_message in (
        (("cache",), "sorted contradicted"),
        (("provider", "provider"), "sorted and unique"),
    ):
        payload = _classification_payload(
            provider_tier="contradicted",
            effective_provider="other-provider",
            cache_tier="attested",
            effective_cache_policy="disabled",
            contradiction_components=components,
        )
        with pytest.raises(ValidationError, match=expected_message):
            _validate_json(EvaluationEvidenceClassification, payload)

    payload = _classification_payload(route_qualification="contradicted")
    with pytest.raises(ValidationError, match="requires non-empty"):
        _validate_json(EvaluationEvidenceClassification, payload)
    payload = _classification_payload(
        provider_tier="contradicted",
        effective_provider="other-provider",
        route_qualification="insufficient",
    )
    with pytest.raises(ValidationError, match="non-empty contradiction"):
        _validate_json(EvaluationEvidenceClassification, payload)


def test_aar_all_attested_qualification_requires_request_receipt_complete() -> None:
    for usage_tier, attempt_visibility in LEGAL_USAGE_VISIBILITY:
        expected = (
            "aar_live_qualified"
            if (usage_tier, attempt_visibility) == ("request_receipt", "complete")
            else "insufficient"
        )
        document = _classification(
            usage_tier=usage_tier,
            attempt_visibility=attempt_visibility,
            route_qualification=expected,
        )
        assert document.route_qualification == expected
        assert document.contradiction_components == ()


def test_prime_live_matrix_and_weaker_noncontradicted_rows() -> None:
    prime = _prime_classification()
    assert prime.route_qualification == "prime_live_qualified"
    assert prime.requested_provider == "openai-codex"
    assert prime.effective_reasoning is None

    effective_field = {
        "provider": "effective_provider",
        "model": "effective_model",
        "fallback": "effective_fallback_policy",
        "cache": "effective_cache_policy",
    }
    for component in ("provider", "model", "fallback", "cache"):
        updates: dict[str, Any] = {f"{component}_tier": "requested_only"}
        updates[effective_field[component]] = None
        document = _prime_classification(**updates)
        assert document.route_qualification == "insufficient"

    document = _prime_classification(reasoning_tier="requested_only", effective_reasoning=None)
    assert document.route_qualification == "prime_live_qualified"


def test_all_usage_visibility_legal_pairs_and_illegal_pairs_are_direct() -> None:
    for usage_tier, attempt_visibility in LEGAL_USAGE_VISIBILITY:
        document = _classification(
            usage_tier=usage_tier,
            attempt_visibility=attempt_visibility,
            route_qualification=(
                "aar_live_qualified"
                if (usage_tier, attempt_visibility) == ("request_receipt", "complete")
                else "insufficient"
            ),
        )
        assert (document.usage_tier, document.attempt_visibility) == (
            usage_tier,
            attempt_visibility,
        )
    for usage_tier, attempt_visibility in (
        ("request_receipt", "aggregate_only"),
        ("request_receipt", "unknown"),
        ("session_aggregate", "complete"),
        ("session_aggregate", "unknown"),
        ("partial_events", "complete"),
        ("unavailable", "aggregate_only"),
    ):
        payload = _classification_payload(
            usage_tier=usage_tier,
            attempt_visibility=attempt_visibility,
            route_qualification="insufficient",
        )
        with pytest.raises(ValidationError, match="legal pair"):
            _validate_json(EvaluationEvidenceClassification, payload)


def test_source_receipt_bounds_order_duplicate_and_max_plus_one_are_not_masked() -> None:
    values = tuple(sorted(canonical_sha256({"receipt": index}) for index in range(1, 129)))
    maximum = _classification(source_receipt_digests=values)
    assert len(maximum.source_receipt_digests) == 128
    with pytest.raises(ValidationError):
        _classification(source_receipt_digests=())

    for receipts in ((DIGEST, DIGEST), (values[1], values[0])):
        payload = _classification_payload(source_receipt_digests=receipts)
        with pytest.raises(ValidationError, match="source_receipt_digests"):
            _validate_json(EvaluationEvidenceClassification, payload)

    overflow = tuple(sorted(canonical_sha256({"receipt": index}) for index in range(1, 130)))
    payload = _classification_payload(source_receipt_digests=overflow)
    with pytest.raises(ValidationError) as error:
        _validate_json(EvaluationEvidenceClassification, payload)
    assert any(item["loc"][-1] == "source_receipt_digests" for item in error.value.errors())
    assert "too_long" in {item["type"] for item in error.value.errors()}


def test_prime_requested_provider_negative_is_direct_and_correctly_redigested() -> None:
    payload = _classification_payload(
        arm="prime",
        requested_provider="other-provider",
        provider_tier="requested_only",
        effective_provider=None,
        model_tier="requested_only",
        effective_model=None,
        reasoning_tier="requested_only",
        effective_reasoning=None,
        fallback_tier="requested_only",
        effective_fallback_policy=None,
        cache_tier="requested_only",
        effective_cache_policy=None,
        route_qualification="insufficient",
    )
    with pytest.raises(ValidationError, match="requested_provider"):
        _validate_json(EvaluationEvidenceClassification, payload)


def test_paired_admission_run_ids_orders_expiry_and_self_digest() -> None:
    for order in (("aar", "prime"), ("prime", "aar")):
        document = _paired(arm_order=order)
        assert document.arm_order == order
    payload = _paired_payload(expires_at_unix_ms=900_100)
    assert _validate_json(PairedEvaluationAdmission, payload).expires_at_unix_ms == 900_100

    for updates, message in (
        ({"aar_qualification_run_id": "prime-run"}, "pairwise distinct"),
        ({"paired_run_id": "aar-run"}, "pairwise distinct"),
        ({"expires_at_unix_ms": 100}, "creation time"),
        ({"expires_at_unix_ms": 99}, "creation time"),
        ({"expires_at_unix_ms": 900_101}, "900000"),
        ({"arm_order": ("aar", "aar")}, "arm_order"),
        ({"arm_order": ("prime", "prime")}, "arm_order"),
        ({"arm_order": ("aar", "unknown")}, "arm_order"),
    ):
        payload = _paired_payload(**updates)
        with pytest.raises(ValidationError, match=message):
            _validate_json(PairedEvaluationAdmission, payload)

    payload = _paired_payload(case_digest=canonical_sha256({"case": "changed"}))
    document = _validate_json(PairedEvaluationAdmission, payload)
    assert document.admission_digest == payload["admission_digest"]


def test_composition_positive_copies_exact_alias_and_classification_digests() -> None:
    alias = _alias()
    aar = _classification()
    prime = _prime_classification()
    document = _compose(
        alias_attestation=alias,
        aar_classification=aar,
        prime_classification=prime,
    )
    assert document.status == "benchmark_ready_planned"
    assert document.prime_alias_digest == alias.alias_digest
    assert document.aar_classification_digest == aar.classification_digest
    assert document.prime_classification_digest == prime.classification_digest
    assert document.aar_qualification_run_id == aar.run_id
    assert document.prime_qualification_run_id == prime.run_id
    assert document.paired_run_id not in {aar.run_id, prime.run_id}


@pytest.mark.parametrize(
    "updates, message",
    (
        ({"paired_run_id": "aar-run"}, "paired run ID"),
        (
            {
                "prime_classification": _prime_classification(
                    requested_model="other-model", effective_model="other-model"
                )
            },
            "model",
        ),
        (
            {
                "prime_classification": _prime_classification(
                    requested_reasoning="low", effective_reasoning="low", reasoning_tier="observed"
                )
            },
            "reasoning",
        ),
        (
            {
                "prime_classification": _prime_classification(
                    provider_tier="requested_only",
                    effective_provider=None,
                    route_qualification="insufficient",
                )
            },
            "prime_live_qualified",
        ),
        (
            {
                "aar_classification": _classification(
                    usage_tier="session_aggregate",
                    attempt_visibility="aggregate_only",
                    route_qualification="insufficient",
                )
            },
            "aar_live_qualified",
        ),
        (
            {
                "aar_classification": _classification(
                    provider_tier="contradicted",
                    effective_provider="other-provider",
                    route_qualification="contradicted",
                )
            },
            "aar_live_qualified",
        ),
    ),
)
def test_composition_rejects_one_axis_qualification_and_binding_mismatches(
    updates: dict[str, Any], message: str
) -> None:
    with pytest.raises((ValidationError, ValueError), match=message):
        _compose(**updates)


def test_composition_rejects_prime_provider_mismatch_at_direct_join_boundary() -> None:
    alias = _alias()
    prime = _prime_classification()
    object.__setattr__(prime, "requested_provider", "other-provider")
    with pytest.raises(ValueError, match="requested_provider"):
        _compose(alias_attestation=alias, prime_classification=prime)


def test_composition_rejects_alias_digest_binding_mismatch_without_external_authority() -> None:
    alias = _alias()
    wrong_alias_digest = canonical_sha256({"wrong": "alias"})
    with pytest.raises(ValueError, match="alias digest"):
        _compose(prime_alias_digest=wrong_alias_digest)

    direct_payload = _paired_payload(prime_alias_digest=wrong_alias_digest)
    direct_document = _validate_json(PairedEvaluationAdmission, direct_payload)
    assert direct_document.prime_alias_digest == wrong_alias_digest
    assert direct_document.admission_digest == direct_payload["admission_digest"]
    assert alias.alias_digest != wrong_alias_digest


@pytest.mark.parametrize("document_name", ("alias", "aar", "prime"))
def test_composition_revalidates_current_input_bytes_and_self_digests(
    document_name: str,
) -> None:
    alias = _alias()
    aar = _classification()
    prime = _prime_classification()
    if document_name == "alias":
        object.__setattr__(alias, "created_at_unix_ms", 101)
    elif document_name == "aar":
        object.__setattr__(aar, "attempt_id", "changed-aar-attempt")
    else:
        object.__setattr__(prime, "attempt_id", "changed-prime-attempt")
    with pytest.raises(ValidationError, match="digest"):
        _compose(
            alias_attestation=alias,
            aar_classification=aar,
            prime_classification=prime,
        )


def test_inert_module_has_no_runtime_provider_or_authority_consumer() -> None:
    module = importlib.import_module("aar.provider_ready_evaluation_models")
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
        if alias.name.startswith("aar.")
        or alias.name in {"subprocess", "socket", "requests", "sqlite3"}
    }
    assert imported_names <= {"aar.broker_models", "aar.canonical", "aar.schemas"}
    for forbidden in (
        "aar.runtime",
        "aar.providers",
        "aar.mcp",
        "subprocess",
        "socket",
        "requests",
        "sqlite3",
        "os.environ",
        "time.",
        "open(",
        "attempt_allocation",
        "send_reservation",
        "replay_authority",
        "score_attempt",
        "winner",
        "stop_contender",
    ):
        assert forbidden not in source
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert calls.isdisjoint({"open", "exec", "eval", "system", "popen"})

    assert module.__file__ is not None
    package_root = Path(module.__file__).resolve().parent
    module_path = Path(module.__file__).resolve()
    authority_name_parts = ("runtime", "provider", "mcp", "cli", "supervisor", "launcher")
    for candidate in package_root.rglob("*.py"):
        if candidate.resolve() == module_path:
            continue
        if any(part in candidate.stem.lower() for part in authority_name_parts):
            assert "provider_ready_evaluation_models" not in candidate.read_text()
