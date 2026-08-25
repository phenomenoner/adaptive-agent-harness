from __future__ import annotations

import importlib
import importlib.util
import inspect
import json

import pytest
from pydantic import ValidationError

from aar.broker_models import ModelRouteValue
from aar.canonical import canonical_sha256
from aar.provider_ready_models import (
    HOST_ACTIVATION_INTENT_SCHEMA_VERSION,
    HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
    METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
    PROVIDER_READY_SCHEMA_MODELS,
    ActivationGrantPolicy,
    ActivationPlannerBinding,
    ActivationRoutePolicy,
    ActivationRuntimeBinding,
    GrantBudgetCeiling,
    HostActivationIntent,
    HostActivationProfile,
    MethodAdapterManifest,
    ProviderReadyCandidate,
)
from aar.schemas import StrictModel

DIGEST = canonical_sha256({"fixture": "provider-ready"})
MAX_COUNTER = 9_223_372_036_854_775_807
AUTHORITY_STORE_ID = "local-file-authority-v1:runtime-primary"
ADAPTER_METHODS = (
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
)
PUBLIC_MODELS = (
    ProviderReadyCandidate,
    ActivationRuntimeBinding,
    ActivationPlannerBinding,
    ActivationRoutePolicy,
    GrantBudgetCeiling,
    ActivationGrantPolicy,
    MethodAdapterManifest,
    HostActivationIntent,
    HostActivationProfile,
)


def _manifest(
    method: str,
    *,
    backend_kind: str = "native",
    reference_only: bool = False,
    evidence_tier: str = "host_receipt_bound",
    contract_id: str | None = None,
) -> MethodAdapterManifest:
    suffix = method.replace(".", "-")
    return MethodAdapterManifest.issue(
        schema_version=METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
        method=method,  # type: ignore[arg-type]
        contract_id=contract_id or f"aar.broker-contract.{suffix}.v2",
        request_schema_digest=DIGEST,
        response_schema_digest=DIGEST,
        backend_kind=backend_kind,  # type: ignore[arg-type]
        factory_id=f"aar.factory.{suffix}.v1",
        factory_digest=DIGEST,
        adapter_id=f"adapter-{suffix}",
        adapter_generation_policy="runtime_generation",
        reference_only=reference_only,
        evidence_tier=evidence_tier,  # type: ignore[arg-type]
        lookup_supported=True,
        cancel_supported=False,
    )


def _budget(**updates: int) -> GrantBudgetCeiling:
    values = {
        "wall_time_ms": 900_000,
        "model_requests": 128,
        "input_tokens": MAX_COUNTER,
        "output_tokens": MAX_COUNTER,
        "child_operations": 64,
        "artifact_bytes": 33_554_432,
    }
    values.update(updates)
    return GrantBudgetCeiling(**values)


def _routes(
    *,
    allowed_profile_ids: tuple[ModelRouteValue, ...] = ("hermes-codex-luna-max",),
) -> ActivationRoutePolicy:
    return ActivationRoutePolicy.issue(
        catalog_digest=DIGEST,
        allowed_profile_ids=allowed_profile_ids,
        fallback_policy="none",
        cache_policy="disabled",
    )


def _grant_policy(
    *,
    principal_patterns: tuple[str, ...] = ("aar-eval-runner",),
    capabilities: tuple[str, ...] = (
        "rlm.workbench.execute",
        "rlm.workbench.read",
    ),
) -> ActivationGrantPolicy:
    return ActivationGrantPolicy.issue(
        principal_patterns=principal_patterns,  # type: ignore[arg-type]
        capabilities=capabilities,  # type: ignore[arg-type]
        budget_ceiling=_budget(),
        max_deadline_ms=900_000,
    )


def _candidate(*, package_version: str = "0.6.0a0") -> ProviderReadyCandidate:
    return ProviderReadyCandidate(
        package_version=package_version,  # type: ignore[arg-type]
        source_commit="0" * 40,
        wheel_digest=DIGEST,
        contract_manifest_digest=DIGEST,
        skill_digest=DIGEST,
    )


def _runtime() -> ActivationRuntimeBinding:
    return ActivationRuntimeBinding(
        runtime_home_digest=DIGEST,
        database_identity="runtime-primary",
        required_registry_version=6,
        programmable_backend="ipython",
        security_profile="trusted_local",
    )


def _planner(mode: str = "caller_delegated_ticketed") -> ActivationPlannerBinding:
    return ActivationPlannerBinding(
        mode=mode,  # type: ignore[arg-type]
        method="model.request",
        directive_schema_version="aar.rlm-directive.v1",
        directive_schema_digest=DIGEST,
    )


def _intent(
    *,
    mode: str = "caller_delegated_ticketed",
    adapters: tuple[MethodAdapterManifest, ...] | None = None,
    previous_activation_authority_digest: str | None = None,
) -> HostActivationIntent:
    if adapters is None:
        model_backend = "caller_driver" if mode == "caller_delegated_ticketed" else "native"
        adapters = tuple(
            _manifest(
                method,
                backend_kind=model_backend if method == "model.request" else "native",
                evidence_tier=(
                    "caller_observed" if method == "model.request" else "host_receipt_bound"
                ),
            )
            for method in ADAPTER_METHODS
        )
    return HostActivationIntent.issue(
        schema_version=HOST_ACTIVATION_INTENT_SCHEMA_VERSION,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=1,
        previous_activation_authority_digest=previous_activation_authority_digest,
        candidate=_candidate(),
        runtime=_runtime(),
        planner=_planner(mode),
        adapters=adapters,
        routes=_routes(),
        grant_policy=_grant_policy(),
        cutover_authority_store_id=AUTHORITY_STORE_ID,
        recovery_compatibility_digest=DIGEST,
    )


def _profile(*, intent: HostActivationIntent | None = None) -> HostActivationProfile:
    return HostActivationProfile.issue(
        schema_version=HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
        intent=_intent() if intent is None else intent,
        migration_attestation_digest=DIGEST,
    )


def _schema_node(root: dict[str, object], node: dict[str, object]) -> dict[str, object]:
    while "$ref" in node:
        reference = node["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/$defs/")
        definition = reference.removeprefix("#/$defs/")
        definitions = root["$defs"]
        assert isinstance(definitions, dict)
        node = definitions[definition]
        assert isinstance(node, dict)
    return node


def _schema_property(root: dict[str, object], *path: str) -> dict[str, object]:
    node = root
    for part in path:
        node = _schema_node(root, node)
        properties = node["properties"]
        assert isinstance(properties, dict)
        child = properties[part]
        assert isinstance(child, dict)
        node = child
    return _schema_node(root, node)


def _schema_objects(root: dict[str, object]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    seen: set[int] = set()

    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        marker = id(node)
        if marker in seen:
            return
        seen.add(marker)
        if node.get("type") == "object":
            found.append(node)
            properties = node.get("properties", {})
            if isinstance(properties, dict):
                for child in properties.values():
                    visit(child)
        for child in node.values():
            visit(child)

    visit(root)
    return found


def test_provider_ready_model_module_exists_with_required_surface() -> None:
    module_spec = importlib.util.find_spec("aar.provider_ready_models")
    assert module_spec is not None, "aar.provider_ready_models module is missing"

    module = importlib.import_module("aar.provider_ready_models")
    required_surface = {
        "ProviderReadyCandidate",
        "ActivationRuntimeBinding",
        "ActivationPlannerBinding",
        "ActivationRoutePolicy",
        "GrantBudgetCeiling",
        "ActivationGrantPolicy",
        "MethodAdapterManifest",
        "HostActivationIntent",
        "HostActivationProfile",
    }
    assert required_surface <= set(vars(module))


def test_all_public_models_round_trip_strictly_and_registry_is_exact() -> None:
    models: tuple[StrictModel, ...] = (
        _candidate(),
        _runtime(),
        _planner(),
        _routes(),
        _budget(),
        _grant_policy(),
        _manifest("model.request", backend_kind="caller_driver", evidence_tier="caller_observed"),
        _intent(),
        _profile(),
    )
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json(), strict=True) == model

    assert {
        "aar.method-adapter-manifest.v1": MethodAdapterManifest,
        "aar.host-activation-intent.v1": HostActivationIntent,
        "aar.host-activation-profile.v1": HostActivationProfile,
    } == PROVIDER_READY_SCHEMA_MODELS


def test_schema_literals_and_required_planner_shape_are_frozen() -> None:
    assert (
        _manifest(
            "model.request", backend_kind="caller_driver", evidence_tier="caller_observed"
        ).schema_version
        == METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION
    )
    assert _intent().schema_version == HOST_ACTIVATION_INTENT_SCHEMA_VERSION
    assert _profile().schema_version == HOST_ACTIVATION_PROFILE_SCHEMA_VERSION
    planner_fields = set(ActivationPlannerBinding.model_fields)
    assert planner_fields == {
        "mode",
        "method",
        "directive_schema_version",
        "directive_schema_digest",
    }
    planner_schema = _schema_node(
        HostActivationIntent.model_json_schema(),
        _schema_property(HostActivationIntent.model_json_schema(), "planner"),
    )
    assert set(planner_schema["required"]) == planner_fields
    assert "oneOf" not in planner_schema
    assert not any("factory" in name or "import" in name for name in planner_fields)


def test_each_top_level_digest_has_an_independent_oracle() -> None:
    documents = (
        (
            _manifest(
                "model.request", backend_kind="caller_driver", evidence_tier="caller_observed"
            ),
            "manifest_digest",
        ),
        (_intent(), "intent_digest"),
        (_profile(), "profile_digest"),
    )
    for document, digest_field in documents:
        payload = document.model_dump(mode="json")
        observed_digest = payload.pop(digest_field)
        assert "schema_version" in payload
        assert observed_digest == canonical_sha256(payload)


def test_wrong_root_digests_are_rejected_for_all_top_level_documents() -> None:
    documents = (
        (
            _manifest(
                "model.request", backend_kind="caller_driver", evidence_tier="caller_observed"
            ),
            "manifest_digest",
        ),
        (_intent(), "intent_digest"),
        (_profile(), "profile_digest"),
    )
    for document, digest_field in documents:
        payload = document.model_dump(mode="json")
        payload[digest_field] = canonical_sha256({"tampered": digest_field})
        with pytest.raises(ValidationError, match="digest"):
            type(document).model_validate_json(json.dumps(payload), strict=True)


def test_nested_digest_binding_is_checked_inner_then_outer() -> None:
    manifest = _manifest("subagent.submit")
    stale_manifest = manifest.model_dump(mode="json")
    stale_manifest["contract_id"] = "aar.broker-contract.changed.v2"
    with pytest.raises(ValidationError, match="manifest digest"):
        MethodAdapterManifest.model_validate(stale_manifest, strict=True)

    adapters = list(_intent().adapters)
    changed = _manifest("subagent.submit", contract_id="aar.broker-contract.changed.v2")
    adapters[1] = changed
    original = _intent()
    stale_intent = original.model_dump(mode="json")
    stale_intent["adapters"] = [item.model_dump(mode="json") for item in adapters]
    with pytest.raises(ValidationError, match="intent digest"):
        HostActivationIntent.model_validate_json(json.dumps(stale_intent), strict=True)

    changed_intent = _intent(adapters=tuple(adapters))
    changed_profile = _profile(intent=changed_intent)
    stale_profile = changed_profile.model_dump(mode="json")
    stale_profile["profile_digest"] = _profile().profile_digest
    with pytest.raises(ValidationError, match="profile digest"):
        HostActivationProfile.model_validate_json(json.dumps(stale_profile), strict=True)

    changed_attestation = changed_profile.model_dump(mode="json")
    changed_attestation["migration_attestation_digest"] = canonical_sha256(
        {"attestation": "changed"}
    )
    changed_attestation["profile_digest"] = changed_profile.profile_digest
    with pytest.raises(ValidationError, match="profile digest"):
        HostActivationProfile.model_validate_json(json.dumps(changed_attestation), strict=True)


def test_previous_authority_digest_is_digest_bound_but_history_order_is_out_of_scope() -> None:
    absent = _intent(previous_activation_authority_digest=None)
    prior = _intent(previous_activation_authority_digest=DIGEST)
    assert absent.intent_digest != prior.intent_digest
    assert absent.previous_activation_authority_digest is None
    assert prior.previous_activation_authority_digest == DIGEST

    payload = prior.model_dump(mode="json")
    payload["previous_activation_authority_digest"] = None
    with pytest.raises(ValidationError, match="intent digest"):
        HostActivationIntent.model_validate_json(json.dumps(payload), strict=True)


def test_all_four_canonical_collections_enforce_bounds_uniqueness_and_order() -> None:
    def assert_too_long(error: pytest.ExceptionInfo[ValidationError]) -> None:
        assert "too_long" in {item["type"] for item in error.value.errors()}

    intent = _intent()
    intent_payload = intent.model_dump(mode="python")
    valid_adapters = intent_payload["adapters"]
    assert isinstance(valid_adapters, tuple)
    with pytest.raises(ValidationError):
        HostActivationIntent.model_validate({**intent_payload, "adapters": ()}, strict=True)
    with pytest.raises(ValidationError) as adapters_too_long:
        HostActivationIntent.model_validate(
            {**intent_payload, "adapters": (*valid_adapters, valid_adapters[0])}, strict=True
        )
    assert_too_long(adapters_too_long)
    with pytest.raises(ValidationError):
        HostActivationIntent.model_validate(
            {**intent_payload, "adapters": tuple(reversed(valid_adapters))}, strict=True
        )
    assert len(intent.adapters) == 6
    minimum_adapters = (
        _manifest(
            "model.request",
            backend_kind="caller_driver",
            evidence_tier="caller_observed",
        ),
    )
    assert len(_intent(adapters=minimum_adapters).adapters) == 1
    with pytest.raises(ValidationError):
        _manifest("artifact.read")

    route = _routes(allowed_profile_ids=("a", "b"))
    with pytest.raises(ValidationError):
        ActivationRoutePolicy.model_validate(
            {**route.model_dump(mode="python"), "allowed_profile_ids": ()}, strict=True
        )
    with pytest.raises(ValidationError):
        ActivationRoutePolicy.model_validate(
            {**route.model_dump(mode="python"), "allowed_profile_ids": ("a", "a")},
            strict=True,
        )
    with pytest.raises(ValidationError):
        ActivationRoutePolicy.model_validate(
            {**route.model_dump(mode="python"), "allowed_profile_ids": ("b", "a")},
            strict=True,
        )
    assert (
        len(
            _routes(
                allowed_profile_ids=tuple(f"p-{index:02d}" for index in range(1, 65))
            ).allowed_profile_ids
        )
        == 64
    )
    assert len(_routes().allowed_profile_ids) == 1
    with pytest.raises(ValidationError) as routes_too_long:
        _routes(allowed_profile_ids=tuple(f"p-{index:02d}" for index in range(1, 66)))
    assert_too_long(routes_too_long)

    policy = _grant_policy(principal_patterns=("a", "b"), capabilities=("a.cap", "b.cap"))
    policy_payload = policy.model_dump(mode="python")
    for field_name in ("principal_patterns", "capabilities"):
        with pytest.raises(ValidationError):
            ActivationGrantPolicy.model_validate(
                {**policy_payload, field_name: ()}, strict=True
            )
        with pytest.raises(ValidationError):
            ActivationGrantPolicy.model_validate(
                {**policy_payload, field_name: ("a", "a")}, strict=True
            )
        with pytest.raises(ValidationError):
            ActivationGrantPolicy.model_validate(
                {**policy_payload, field_name: ("b", "a")}, strict=True
            )
    maximum_policy = _grant_policy(
        principal_patterns=tuple(f"principal-{index:02d}" for index in range(1, 65)),
        capabilities=tuple(f"capability.{index:02d}" for index in range(1, 65)),
    )
    assert len(maximum_policy.principal_patterns) == 64
    assert len(maximum_policy.capabilities) == 64
    minimum_policy = _grant_policy(principal_patterns=("principal-01",), capabilities=("a.cap",))
    assert len(minimum_policy.principal_patterns) == 1
    assert len(minimum_policy.capabilities) == 1
    with pytest.raises(ValidationError) as principals_too_long:
        _grant_policy(
            principal_patterns=tuple(f"principal-{index:02d}" for index in range(1, 66)),
            capabilities=("a.cap",),
        )
    assert_too_long(principals_too_long)
    with pytest.raises(ValidationError) as capabilities_too_long:
        _grant_policy(
            principal_patterns=("principal-01",),
            capabilities=tuple(f"capability.{index:02d}" for index in range(1, 66)),
        )
    assert_too_long(capabilities_too_long)


def test_schema_array_bounds_are_direct_wire_assertions() -> None:
    intent_schema = HostActivationIntent.model_json_schema()
    route_schema = ActivationRoutePolicy.model_json_schema()
    policy_schema = ActivationGrantPolicy.model_json_schema()
    assert _schema_property(intent_schema, "adapters")["minItems"] == 1
    assert _schema_property(intent_schema, "adapters")["maxItems"] == 6
    assert _schema_property(route_schema, "allowed_profile_ids")["minItems"] == 1
    assert _schema_property(route_schema, "allowed_profile_ids")["maxItems"] == 64
    assert _schema_property(policy_schema, "principal_patterns")["minItems"] == 1
    assert _schema_property(policy_schema, "principal_patterns")["maxItems"] == 64
    assert _schema_property(policy_schema, "capabilities")["minItems"] == 1
    assert _schema_property(policy_schema, "capabilities")["maxItems"] == 64


def test_planner_modes_require_exact_executable_model_manifest() -> None:
    for mode, expected_backend in (
        ("caller_delegated_ticketed", "caller_driver"),
        ("service_managed", "native"),
    ):
        valid = _intent(mode=mode)
        model_manifest = next(item for item in valid.adapters if item.method == "model.request")
        assert model_manifest.backend_kind == expected_backend

        missing = tuple(item for item in valid.adapters if item.method != "model.request")
        with pytest.raises(ValidationError, match=r"exactly one model.request"):
            _intent(mode=mode, adapters=missing)

        opposite = tuple(
            _manifest(
                item.method,
                backend_kind=("native" if expected_backend == "caller_driver" else "caller_driver")
                if item.method == "model.request"
                else item.backend_kind,
                evidence_tier=item.evidence_tier,
            )
            for item in valid.adapters
        )
        with pytest.raises(ValidationError, match="matching executable"):
            _intent(mode=mode, adapters=opposite)

        reference = tuple(
            _manifest(
                item.method,
                backend_kind="reference" if item.method == "model.request" else item.backend_kind,
                reference_only=item.method == "model.request",
                evidence_tier="unknown" if item.method == "model.request" else item.evidence_tier,
            )
            for item in valid.adapters
        )
        with pytest.raises(ValidationError, match="matching executable"):
            _intent(mode=mode, adapters=reference)


def test_backend_reference_truth_and_artifact_caller_restriction() -> None:
    reference = _manifest(
        "artifact.put",
        backend_kind="reference",
        reference_only=True,
        evidence_tier="unknown",
    )
    native = _manifest("artifact.put", backend_kind="native", reference_only=False)
    assert reference.reference_only
    assert native.reference_only is False

    for updates in (
        {"reference_only": False, "evidence_tier": "unknown"},
        {"reference_only": True, "evidence_tier": "host_receipt_bound"},
    ):
        payload = reference.model_dump(mode="json")
        payload.update(updates)
        with pytest.raises(ValidationError, match="reference backend"):
            MethodAdapterManifest.model_validate(payload, strict=True)

    for backend_kind in ("native", "caller_driver"):
        payload = native.model_dump(mode="json")
        payload.update(backend_kind=backend_kind, reference_only=True)
        with pytest.raises(ValidationError, match="reference_only"):
            MethodAdapterManifest.model_validate(payload, strict=True)

    with pytest.raises(ValidationError, match=r"artifact.put"):
        _manifest("artifact.put", backend_kind="caller_driver", evidence_tier="caller_observed")


def test_lexical_domains_literals_and_scalar_coercions_are_strict() -> None:
    for package_version in ("00.06.000a00", "1.2.3rc01.post02.dev03", "1.2.3+local", "1.2"):
        with pytest.raises(ValidationError):
            _candidate(package_version=package_version)
    for source_commit in ("A" * 40, "0" * 39, "0" * 41):
        with pytest.raises(ValidationError):
            ProviderReadyCandidate(
                package_version="0.6.0a0",
                source_commit=source_commit,
                wheel_digest=DIGEST,
                contract_manifest_digest=DIGEST,
                skill_digest=DIGEST,
            )
    for value in ("local-file-authority-v1:*", "local-file-authority-v1:../authority", "authority"):
        with pytest.raises(ValidationError):
            HostActivationIntent.model_validate(
                {**_intent().model_dump(mode="json"), "cutover_authority_store_id": value},
                strict=True,
            )

    for field_name, value in (
        ("required_registry_version", 5),
        ("programmable_backend", "plain-python"),
        ("security_profile", "managed_restricted"),
    ):
        payload = _runtime().model_dump(mode="json")
        payload[field_name] = value
        with pytest.raises(ValidationError):
            ActivationRuntimeBinding.model_validate(payload, strict=True)

    for field_name, value in (
        ("activation_generation", 0),
        ("activation_generation", MAX_COUNTER + 1),
        ("activation_generation", True),
    ):
        payload = _intent().model_dump(mode="json")
        payload[field_name] = value
        with pytest.raises(ValidationError):
            HostActivationIntent.model_validate(payload, strict=True)

    for field_name, value in (
        ("mode", "caller_delegated"),
        ("method", "artifact.put"),
        ("directive_schema_version", "aar.other.v1"),
    ):
        payload = _planner().model_dump(mode="json")
        payload[field_name] = value
        with pytest.raises(ValidationError):
            ActivationPlannerBinding.model_validate(payload, strict=True)


def test_budget_bounds_include_exact_edges_but_reject_out_of_range_and_coercion() -> None:
    assert _budget(
        wall_time_ms=1_000,
        model_requests=1,
        input_tokens=0,
        output_tokens=0,
        child_operations=0,
        artifact_bytes=0,
    )
    cases = (
        ("wall_time_ms", 999),
        ("wall_time_ms", 900_001),
        ("model_requests", 0),
        ("model_requests", 129),
        ("input_tokens", -1),
        ("input_tokens", MAX_COUNTER + 1),
        ("output_tokens", -1),
        ("output_tokens", MAX_COUNTER + 1),
        ("child_operations", -1),
        ("child_operations", 65),
        ("artifact_bytes", -1),
        ("artifact_bytes", 33_554_433),
    )
    for field_name, value in cases:
        with pytest.raises(ValidationError):
            _budget(**{field_name: value})
    for field_name in ("wall_time_ms", "model_requests", "input_tokens", "output_tokens"):
        with pytest.raises(ValidationError):
            _budget(**{field_name: True})

    policy = _grant_policy()
    for value in (999, 900_001, 0, True):
        payload = policy.model_dump(mode="json")
        payload["max_deadline_ms"] = value
        with pytest.raises(ValidationError):
            ActivationGrantPolicy.model_validate(payload, strict=True)


def test_unknown_fields_required_fields_schema_strictness_and_inert_import_boundary() -> None:
    for model_type in PUBLIC_MODELS:
        for field_name, field_info in model_type.model_fields.items():
            assert field_info.is_required(), (model_type.__name__, field_name)
        for field_info in model_type.model_fields.values():
            assert field_info.default is None or str(field_info.default).endswith(
                "PydanticUndefined"
            )

        document = None
        if model_type is ProviderReadyCandidate:
            document = _candidate().model_dump(mode="json")
        elif model_type is ActivationRuntimeBinding:
            document = _runtime().model_dump(mode="json")
        elif model_type is ActivationPlannerBinding:
            document = _planner().model_dump(mode="json")
        elif model_type is ActivationRoutePolicy:
            document = _routes().model_dump(mode="json")
        elif model_type is GrantBudgetCeiling:
            document = _budget().model_dump(mode="json")
        elif model_type is ActivationGrantPolicy:
            document = _grant_policy().model_dump(mode="json")
        elif model_type is MethodAdapterManifest:
            document = _manifest(
                "model.request",
                backend_kind="caller_driver",
                evidence_tier="caller_observed",
            ).model_dump(mode="json")
        elif model_type is HostActivationIntent:
            document = _intent().model_dump(mode="json")
        else:
            document = _profile().model_dump(mode="json")
        document["unexpected"] = True
        with pytest.raises(ValidationError):
            model_type.model_validate(document, strict=True)

        schema = model_type.model_json_schema()
        for object_schema in _schema_objects(schema):
            assert object_schema["additionalProperties"] is False
            assert set(object_schema["required"]) == set(object_schema["properties"])
            for property_schema in object_schema["properties"].values():
                assert "default" not in property_schema

    source = inspect.getsource(importlib.import_module("aar.provider_ready_models"))
    for forbidden in (
        "aar.runtime",
        "aar.providers",
        "aar.mcp",
        "import subprocess",
        "import socket",
        "import requests",
    ):
        assert forbidden not in source


def test_principal_patterns_are_exact_inert_ids_not_glob_or_regex() -> None:
    for value in ("aar-*", "^aar-.*$", "aar-eval-runner/child", "aar?runner"):
        with pytest.raises(ValidationError):
            _grant_policy(principal_patterns=(value,))  # type: ignore[arg-type]
    policy = _grant_policy(principal_patterns=("aar-eval-runner",))
    assert policy.principal_patterns == ("aar-eval-runner",)
