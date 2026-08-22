from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from typing import Any

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_models import GrantBudgetCeiling
from aar.provider_ready_operator_models import CandidateBinding
from aar.provider_ready_runtime_models import (
    ACTIVATION_READBACK_SCHEMA_VERSION,
    PROVIDER_READY_RUNTIME_SCHEMA_MODELS,
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    ActivationReadback,
    BackendAvailability,
    IssuedWorkbenchGrant,
    PlannerReadback,
    WorkbenchGrantSet,
)

DIGEST = canonical_sha256({"fixture": "runtime-readback"})
MAX_COUNTER = 9_223_372_036_854_775_807
AUTHORITY_STORE_ID = "local-file-authority-v1:runtime-primary"
METHODS = (
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
)
EVIDENCE_SOURCES = (
    "installed_assets",
    "registry",
    "activation_history",
    "activation_current",
    "epoch_marker",
    "supervisor",
    "capability_registry",
    "grant_issuer",
)
PUBLIC_MODELS = (ActivationReadback, WorkbenchGrantSet)
ALL_MODELS = (
    ActivationReadback,
    WorkbenchGrantSet,
    BackendAvailability,
    PlannerReadback,
    IssuedWorkbenchGrant,
)


def _candidate() -> CandidateBinding:
    return CandidateBinding(
        package_version="0.6.0a0",
        source_commit="0" * 40,
        wheel_digest=DIGEST,
        contract_manifest_digest=DIGEST,
        skill_digest=DIGEST,
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


def _row(
    method: str,
    *,
    backend_kind: str = "unconfigured",
    adapter_generation: int = 7,
    evidence_tier: str = "unknown",
) -> BackendAvailability:
    executable = backend_kind in {"native", "caller_driver"}
    reference = backend_kind == "reference"
    return BackendAvailability(
        method=method,
        contract_id=f"aar.broker-contract.{method.replace('.', '-')}.v2",
        request_schema_digest=DIGEST,
        response_schema_digest=DIGEST,
        backend_kind=backend_kind,  # type: ignore[arg-type]
        configured=backend_kind != "unconfigured",
        reference_only=reference,
        adapter_id=f"adapter-{method.replace('.', '-')}" if executable else None,
        adapter_generation=adapter_generation if executable else None,
        evidence_tier=(evidence_tier if executable else "unknown"),  # type: ignore[arg-type]
    )


def _inactive_methods() -> tuple[BackendAvailability, ...]:
    return tuple(
        _row(method, backend_kind="reference" if method == "artifact.put" else "unconfigured")
        for method in METHODS
    )


def _active_methods() -> tuple[BackendAvailability, ...]:
    return (
        _row("model.request", backend_kind="caller_driver", evidence_tier="caller_observed"),
        _row("subagent.submit", backend_kind="native", evidence_tier="host_receipt_bound"),
        _row("subagent.result", backend_kind="native", evidence_tier="provider_attested"),
        _row("evidence.query", backend_kind="native", evidence_tier="unknown"),
        _row("artifact.put", backend_kind="reference"),
        _row("effect.propose", backend_kind="native", evidence_tier="host_receipt_bound"),
    )


def _planner(mode: str | None = None, *, ready: bool = False) -> PlannerReadback:
    if mode is None:
        return PlannerReadback(
            mode=None,
            ready=ready,
            factory_id=None,
            factory_digest=None,
            method_manifest_digest=None,
        )
    return PlannerReadback(
        mode=mode,  # type: ignore[arg-type]
        ready=ready,
        factory_id="aar.factory.model-request.v1",
        factory_digest=DIGEST,
        method_manifest_digest=DIGEST,
    )


def _base(*, state: str = "unconfigured", reason_code: str = "none") -> dict[str, Any]:
    return {
        "schema_version": ACTIVATION_READBACK_SCHEMA_VERSION,
        "observed_at_unix_ms": 100,
        "state": state,
        "reason_code": reason_code,
        "runtime_generation": None,
        "supervisor_process_identity_digest": None,
        "candidate": None,
        "registry_schema_version": None,
        "registry_schema_digest": None,
        "migration_attestation_digest": None,
        "profile_id": None,
        "activation_generation": None,
        "intent_digest": None,
        "profile_digest": None,
        "previous_activation_authority_digest": None,
        "activation_authority_digest": None,
        "grant_set_digest": None,
        "capability_digest": None,
        "broker_catalog_digest": None,
        "tool_surface_digest": None,
        "methods": _inactive_methods(),
        "planner": _planner(),
        "route_catalog_digest": None,
        "route_profile_ids": (),
        "authority_store_id": None,
        "authority_history_tip_digest": None,
        "operator_epoch_kind": None,
        "operator_epoch": None,
        "latest_operator_receipt_digest": None,
        "evidence_sources": (),
    }


def _profile_verified() -> dict[str, Any]:
    payload = _base(state="profile_verified")
    payload.update(
        candidate=_candidate(),
        registry_schema_version=6,
        registry_schema_digest=DIGEST,
        migration_attestation_digest=DIGEST,
        profile_id="profile-primary",
        activation_generation=7,
        intent_digest=DIGEST,
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        authority_store_id=AUTHORITY_STORE_ID,
        authority_history_tip_digest=DIGEST,
    )
    return payload


def _profile_invalid(
    reason_code: str = "ACTIVATION_PROFILE_INVALID", *, retained: bool = False
) -> dict[str, Any]:
    payload = _base(state="profile_invalid", reason_code=reason_code)
    payload.update(candidate=_candidate(), registry_schema_version=6, registry_schema_digest=DIGEST)
    if retained:
        payload.update(
            migration_attestation_digest=DIGEST,
            profile_id="profile-primary",
            activation_generation=7,
            intent_digest=DIGEST,
            profile_digest=DIGEST,
            activation_authority_digest=DIGEST,
            authority_store_id=AUTHORITY_STORE_ID,
            authority_history_tip_digest=DIGEST,
            broker_catalog_digest=DIGEST,
            tool_surface_digest=DIGEST,
            route_catalog_digest=DIGEST,
            route_profile_ids=("route-a",),
        )
    return payload


def _migration(*, candidate: bool = False, retained_observations: bool = False) -> dict[str, Any]:
    payload = _base(state="migration_required", reason_code="REGISTRY_VERSION_UNSUPPORTED")
    payload.update(registry_schema_version=5, registry_schema_digest=DIGEST)
    if candidate:
        payload["candidate"] = _candidate()
    if retained_observations:
        payload.update(
            broker_catalog_digest=DIGEST,
            tool_surface_digest=DIGEST,
            route_catalog_digest=DIGEST,
            route_profile_ids=("route-a",),
        )
    return payload


def _starting() -> dict[str, Any]:
    payload = _profile_verified()
    payload.update(
        state="starting",
        runtime_generation=7,
        supervisor_process_identity_digest=DIGEST,
    )
    return payload


def _active() -> dict[str, Any]:
    payload = _profile_verified()
    payload.update(
        state="active",
        runtime_generation=7,
        supervisor_process_identity_digest=DIGEST,
        grant_set_digest=DIGEST,
        capability_digest=DIGEST,
        broker_catalog_digest=DIGEST,
        tool_surface_digest=DIGEST,
        route_catalog_digest=DIGEST,
        methods=_active_methods(),
        planner=_planner("caller_delegated_ticketed", ready=True),
        route_profile_ids=("route-a", "route-b"),
    )
    return payload


def _degraded(reason_code: str) -> dict[str, Any]:
    payload = _active()
    payload.update(
        state="degraded",
        reason_code=reason_code,
        planner=_planner("caller_delegated_ticketed", ready=False),
    )
    return payload


def _recovery(
    reason_code: str = "ACTIVATION_HISTORY_CONFLICT", *, retained: bool = False
) -> dict[str, Any]:
    payload = _base(state="recovery_required", reason_code=reason_code)
    if retained:
        payload.update(
            candidate=_candidate(),
            registry_schema_version=6,
            registry_schema_digest=DIGEST,
            migration_attestation_digest=DIGEST,
            profile_id="profile-primary",
            activation_generation=7,
            intent_digest=DIGEST,
            profile_digest=DIGEST,
            activation_authority_digest=DIGEST,
            authority_store_id=AUTHORITY_STORE_ID,
            authority_history_tip_digest=DIGEST,
            broker_catalog_digest=DIGEST,
            tool_surface_digest=DIGEST,
            route_catalog_digest=DIGEST,
            route_profile_ids=("route-a",),
            operator_epoch_kind="restore",
            operator_epoch="restore-7",
            latest_operator_receipt_digest=DIGEST,
            evidence_sources=("activation_history", "activation_current", "epoch_marker"),
        )
    return payload


def _issue(payload: dict[str, Any]) -> ActivationReadback:
    return ActivationReadback.issue(**payload)


def _python_payload(document: Any) -> dict[str, Any]:
    payload = document.model_dump(mode="python")
    payload.pop("readback_digest", None)
    payload["readback_digest"] = canonical_sha256(payload)
    return payload


def _json_payload(document: Any) -> dict[str, Any]:
    payload = document.model_dump(mode="json")
    payload.pop("readback_digest", None)
    payload["readback_digest"] = canonical_sha256(payload)
    return payload


def _refresh_readback_digest(payload: dict[str, Any]) -> None:
    payload["readback_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "readback_digest"}
    )


def _grant_set(
    *,
    principal_ids: tuple[str, ...] = ("principal-a",),
    capabilities: tuple[str, ...] = ("rlm.workbench.execute",),
) -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=7,
        activation_generation=3,
        profile_id="profile-primary",
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        capability_digest=DIGEST,
        route_catalog_digest=DIGEST,
        principal_ids=principal_ids,  # type: ignore[arg-type]
        session_binding_policy="bind_exact_request_session",
        capabilities=capabilities,  # type: ignore[arg-type]
        budget_ceiling=_budget(),
        max_ttl_ms=900_000,
    )


def _grant() -> IssuedWorkbenchGrant:
    return IssuedWorkbenchGrant(
        grant_id="grant-1",
        grant_set_digest=DIGEST,
        capability="rlm.workbench.execute",
        principal_id="principal-a",
        session_id="session-a",
        runtime_generation=7,
        activation_generation=3,
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        capability_digest=DIGEST,
        route_catalog_digest=DIGEST,
        wall_time_ms=1_000,
        model_requests=1,
        input_tokens=0,
        output_tokens=0,
        child_operations=0,
        artifact_bytes=0,
        issued_at_unix_ms=100,
        expires_at_unix_ms=1_100,
        revoked=False,
    )


def _schema_node(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        reference = node["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/$defs/")
        definition = reference.removeprefix("#/$defs/")
        node = root["$defs"][definition]
    return node


def _schema_property(root: dict[str, Any], *path: str) -> dict[str, Any]:
    node: dict[str, Any] = root
    for part in path:
        node = _schema_node(root, node)
        node = node["properties"][part]
    return _schema_node(root, node)


def test_provider_ready_runtime_model_module_exists() -> None:
    module_spec = importlib.util.find_spec("aar.provider_ready_runtime_models")
    assert module_spec is not None, "aar.provider_ready_runtime_models module is missing"


def test_model_inventory_and_exact_local_registry() -> None:
    module = importlib.import_module("aar.provider_ready_runtime_models")
    assert {
        "ActivationReadback",
        "WorkbenchGrantSet",
        "BackendAvailability",
        "PlannerReadback",
        "IssuedWorkbenchGrant",
    } <= set(vars(module))
    assert {
        ACTIVATION_READBACK_SCHEMA_VERSION: ActivationReadback,
        WORKBENCH_GRANT_SET_SCHEMA_VERSION: WorkbenchGrantSet,
    } == PROVIDER_READY_RUNTIME_SCHEMA_MODELS
    assert set(PROVIDER_READY_RUNTIME_SCHEMA_MODELS) == {
        "aar.activation-readback.v1",
        "aar.workbench-grant-set.v1",
    }
    assert "IssuedWorkbenchGrant" not in PROVIDER_READY_RUNTIME_SCHEMA_MODELS

    assert list(BackendAvailability.model_fields) == [
        "method",
        "contract_id",
        "request_schema_digest",
        "response_schema_digest",
        "backend_kind",
        "configured",
        "reference_only",
        "adapter_id",
        "adapter_generation",
        "evidence_tier",
    ]
    assert list(PlannerReadback.model_fields) == [
        "mode",
        "ready",
        "factory_id",
        "factory_digest",
        "method_manifest_digest",
    ]
    assert len(IssuedWorkbenchGrant.model_fields) == 20
    assert "schema_version" not in IssuedWorkbenchGrant.model_fields
    assert "grant_digest" not in IssuedWorkbenchGrant.model_fields


def test_public_documents_round_trip_strictly_and_have_independent_digest_oracles() -> None:
    readback = _issue(_active())
    grant_set = _grant_set()
    for document in (readback, grant_set):
        assert (
            type(document).model_validate_json(document.model_dump_json(), strict=True)
            == document
        )
        payload = document.model_dump(mode="json")
        digest_field = (
            "readback_digest" if isinstance(document, ActivationReadback) else "grant_set_digest"
        )
        observed = payload.pop(digest_field)
        assert observed == canonical_sha256(payload)

    assert ActivationReadback.model_validate(_python_payload(readback), strict=True) == readback
    assert WorkbenchGrantSet.model_validate(
        {**grant_set.model_dump(mode="python"), "grant_set_digest": grant_set.grant_set_digest},
        strict=True,
    ) == grant_set


def test_recursive_strict_schema_shape_and_required_no_defaults() -> None:
    def visit(node: object) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False
            assert set(node["required"]) == set(node["properties"])
            for property_schema in node["properties"].values():
                assert "default" not in property_schema
        for child in node.values():
            if isinstance(child, (dict, list)):
                visit(child)

    for model_type in ALL_MODELS:
        for field_info in model_type.model_fields.values():
            assert field_info.is_required(), (model_type.__name__, field_info)
        visit(model_type.model_json_schema())


def test_backend_availability_exhaustive_matrix_and_artifact_caller_restriction() -> None:
    for kind, configured, reference_only, adapter_id, adapter_generation, evidence in (
        ("unconfigured", False, False, None, None, "unknown"),
        ("reference", True, True, None, None, "unknown"),
        ("native", True, False, "adapter-x", 7, "host_receipt_bound"),
        ("caller_driver", True, False, "adapter-x", 7, "caller_observed"),
    ):
        row = BackendAvailability(
            method="model.request",
            contract_id="aar.broker-contract.model-request.v2",
            request_schema_digest=DIGEST,
            response_schema_digest=DIGEST,
            backend_kind=kind,  # type: ignore[arg-type]
            configured=configured,
            reference_only=reference_only,
            adapter_id=adapter_id,
            adapter_generation=adapter_generation,
            evidence_tier=evidence,  # type: ignore[arg-type]
        )
        assert row.backend_kind == kind

    valid = _row("artifact.put", backend_kind="native")
    assert valid.backend_kind == "native"
    assert _row("artifact.put", backend_kind="reference").reference_only
    for payload in (
        valid.model_dump(mode="python") | {"reference_only": True},
        valid.model_dump(mode="python") | {"configured": False},
        valid.model_dump(mode="python") | {"adapter_id": None},
    ):
        with pytest.raises(ValidationError, match="backend"):
            BackendAvailability.model_validate(payload, strict=True)
    caller_payload = _row("model.request", backend_kind="caller_driver").model_dump(mode="python")
    caller_payload["method"] = "artifact.put"
    with pytest.raises(ValidationError, match=r"artifact\.put cannot use caller_driver"):
        BackendAvailability.model_validate(caller_payload, strict=True)


def test_planner_readback_null_and_nonnull_binding_matrix() -> None:
    assert _planner() == PlannerReadback(
        mode=None,
        ready=False,
        factory_id=None,
        factory_digest=None,
        method_manifest_digest=None,
    )
    assert _planner("service_managed")
    for field_name, value in (
        ("factory_id", "aar.factory.model-request.v1"),
        ("factory_digest", DIGEST),
        ("method_manifest_digest", DIGEST),
    ):
        payload = _planner().model_dump(mode="python")
        payload[field_name] = value
        with pytest.raises(ValidationError, match="planner"):
            PlannerReadback.model_validate(payload, strict=True)
    payload = _planner("service_managed").model_dump(mode="python")
    payload["factory_id"] = None
    with pytest.raises(ValidationError, match="all planner bindings"):
        PlannerReadback.model_validate(payload, strict=True)
    payload = _planner().model_dump(mode="python")
    payload["ready"] = True
    with pytest.raises(ValidationError, match="ready=false"):
        PlannerReadback.model_validate(payload, strict=True)


def test_direct_schema_bounds_and_enums_are_unconditional() -> None:
    readback_schema = ActivationReadback.model_json_schema()
    methods_schema = _schema_property(readback_schema, "methods")
    assert methods_schema["minItems"] == 6
    assert methods_schema["maxItems"] == 6
    backend_schema = _schema_node(readback_schema, methods_schema["items"])
    assert backend_schema["properties"]["backend_kind"]["enum"] == [
        "unconfigured",
        "native",
        "caller_driver",
        "reference",
    ]
    assert _schema_property(readback_schema, "route_profile_ids")["minItems"] == 0
    assert _schema_property(readback_schema, "route_profile_ids")["maxItems"] == 64
    evidence_schema = _schema_property(readback_schema, "evidence_sources")
    assert evidence_schema["minItems"] == 0
    assert evidence_schema["maxItems"] == 8
    assert _schema_node(readback_schema, evidence_schema["items"])["enum"] == list(EVIDENCE_SOURCES)

    grant_schema = WorkbenchGrantSet.model_json_schema()
    assert _schema_property(grant_schema, "principal_ids")["minItems"] == 1
    assert _schema_property(grant_schema, "principal_ids")["maxItems"] == 64
    assert _schema_property(grant_schema, "capabilities")["minItems"] == 1
    assert _schema_property(grant_schema, "capabilities")["maxItems"] == 64


def test_methods_are_exactly_six_unique_rows_in_frozen_order() -> None:
    active = _issue(_active())
    payload = _python_payload(active)
    methods = list(payload["methods"])
    methods[0], methods[1] = methods[1], methods[0]
    payload["methods"] = tuple(methods)
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="frozen broker-catalog order"):
        ActivationReadback.model_validate(payload, strict=True)

    payload = _python_payload(active)
    methods = list(payload["methods"])
    methods[1] = methods[0]
    payload["methods"] = tuple(methods)
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="exactly once"):
        ActivationReadback.model_validate(payload, strict=True)


def test_all_readback_state_positive_witnesses_and_source_dependent_variants() -> None:
    assert _issue(_base())
    candidate_unconfigured = _base()
    candidate_unconfigured["candidate"] = _candidate()
    assert _issue(candidate_unconfigured)
    observed_unconfigured = _base()
    observed_unconfigured.update(
        broker_catalog_digest=DIGEST,
        tool_surface_digest=DIGEST,
        route_catalog_digest=DIGEST,
        route_profile_ids=("route-a",),
    )
    assert _issue(observed_unconfigured)

    assert _issue(_migration())
    assert _issue(_migration(candidate=True))
    assert _issue(_migration(candidate=True, retained_observations=True))

    for reason in (
        "ACTIVATION_PROFILE_INVALID",
        "ACTIVATION_BINDING_MISMATCH",
        "GRANT_POLICY_INVALID",
    ):
        assert _issue(_profile_invalid(reason))
        assert _issue(_profile_invalid(reason, retained=True))

    assert _issue(_profile_verified())
    for retained in (
        {"broker_catalog_digest": DIGEST},
        {"tool_surface_digest": DIGEST},
        {"route_catalog_digest": DIGEST, "route_profile_ids": ("route-a",)},
        {
            "broker_catalog_digest": DIGEST,
            "tool_surface_digest": DIGEST,
            "route_catalog_digest": DIGEST,
            "route_profile_ids": ("route-a",),
        },
    ):
        payload = _profile_verified()
        payload.update(retained)
        assert _issue(payload)

    assert _issue(_starting())
    assert _issue(_active())
    for reason in (
        "GRANT_BINDING_MISMATCH",
        "PLANNER_UNAVAILABLE",
        "CAPABILITY_UNAVAILABLE",
        "STALE_ADAPTER_GENERATION",
    ):
        assert _issue(_degraded(reason))

    for reason in (
        "ACTIVATION_HISTORY_CONFLICT",
        "CUTOVER_RECOVERY_REQUIRED",
        "ABORT_OR_APPLY_REQUIRED",
        "RECONCILE_INPUT_REQUIRED",
    ):
        assert _issue(_recovery(reason))
        assert _issue(_recovery(reason, retained=True))


def test_first_generation_active_and_degraded_keep_previous_authority_nullable() -> None:
    for payload in (_active(), _degraded("CAPABILITY_UNAVAILABLE")):
        assert payload["previous_activation_authority_digest"] is None
        assert _issue(payload)
        payload["previous_activation_authority_digest"] = DIGEST
        assert _issue(payload)


def test_operator_epoch_triple_variants_are_local_and_cross_family() -> None:
    payloads = (
        _base(),
        _profile_invalid(retained=True),
        _profile_verified(),
        _active(),
        _degraded("CAPABILITY_UNAVAILABLE"),
        _recovery(retained=True),
    )
    for payload in payloads:
        assert _issue(payload)
        with_epoch = dict(payload)
        with_epoch.update(operator_epoch_kind="cutover", operator_epoch="epoch-1")
        assert _issue(with_epoch)
        with_receipt = dict(with_epoch)
        with_receipt["latest_operator_receipt_digest"] = DIGEST
        assert _issue(with_receipt)

    invalid = _python_payload(_issue(_active()))
    invalid["operator_epoch_kind"] = "restore"
    invalid["operator_epoch"] = None
    _refresh_readback_digest(invalid)
    with pytest.raises(ValidationError, match="non-null operator_epoch_kind"):
        ActivationReadback.model_validate(invalid, strict=True)


def test_state_rules_reject_one_axis_mutations_with_recomputed_root() -> None:
    cases = (
        (
            _issue(_active()),
            "reason_code",
            "REGISTRY_VERSION_UNSUPPORTED",
            "active state requires reason_code",
        ),
        (_issue(_active()), "candidate", None, "active state requires candidate"),
        (
            _issue(_profile_invalid()),
            "runtime_generation",
            7,
            "profile_invalid state requires runtime_generation=null",
        ),
        (
            _issue(_starting()),
            "grant_set_digest",
            DIGEST,
            "starting state requires grant_set_digest=null",
        ),
        (
            _issue(_recovery()),
            "capability_digest",
            DIGEST,
            "recovery_required state requires capability_digest=null",
        ),
        (
            _issue(_profile_verified()),
            "authority_history_tip_digest",
            None,
            "profile_verified state requires authority_history_tip_digest",
        ),
    )
    for document, field_name, value, message in cases:
        payload = _python_payload(document)
        payload[field_name] = value
        _refresh_readback_digest(payload)
        with pytest.raises(ValidationError, match=message):
            ActivationReadback.model_validate(payload, strict=True)


def test_active_degraded_generation_and_planner_readiness_are_distinct() -> None:
    for document in (_issue(_active()), _issue(_degraded("STALE_ADAPTER_GENERATION"))):
        assert document.runtime_generation == 7
        assert all(
            row.adapter_generation in (None, 7)
            for row in document.methods
        )

    payload = _python_payload(_issue(_active()))
    payload["methods"] = tuple(
        {**row, "adapter_generation": 6} if row["backend_kind"] == "native" else row
        for row in payload["methods"]
    )
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="adapter_generation must equal runtime_generation"):
        ActivationReadback.model_validate(payload, strict=True)

    payload = _python_payload(_issue(_active()))
    payload["planner"] = {**payload["planner"], "ready": False}
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match=r"planner\.ready must be true iff"):
        ActivationReadback.model_validate(payload, strict=True)

    payload = _python_payload(_issue(_degraded("PLANNER_UNAVAILABLE")))
    payload["planner"] = {**payload["planner"], "ready": True}
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match=r"planner\.ready must be true iff"):
        ActivationReadback.model_validate(payload, strict=True)


def test_migration_negative_capability_and_configured_method_are_separate_recomputed_mutants(
) -> None:
    document = _issue(_migration(candidate=True))
    payload = _python_payload(document)
    payload["capability_digest"] = DIGEST
    _refresh_readback_digest(payload)
    with pytest.raises(
        ValidationError,
        match="migration_required state requires capability_digest=null",
    ):
        ActivationReadback.model_validate(payload, strict=True)

    document = _issue(_migration(candidate=True))
    payload = _python_payload(document)
    methods = list(payload["methods"])
    methods[0] = _row("model.request", backend_kind="native").model_dump(mode="python")
    payload["methods"] = tuple(methods)
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="migration_required state cannot contain configured"):
        ActivationReadback.model_validate(payload, strict=True)


def test_route_and_evidence_canonicalization_and_direct_rejection() -> None:
    payload = _profile_verified()
    payload["route_profile_ids"] = ("route-b", "route-a")
    payload["evidence_sources"] = ("registry", "installed_assets")
    issued = _issue(payload)
    assert issued.route_profile_ids == ("route-a", "route-b")
    assert issued.evidence_sources == ("installed_assets", "registry")

    document = _issue(_active())
    payload = _python_payload(document)
    payload["route_profile_ids"] = ("route-b", "route-a")
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="route_profile_ids must be sorted"):
        ActivationReadback.model_validate(payload, strict=True)
    payload = _python_payload(document)
    payload["route_profile_ids"] = ("route-a", "route-a")
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="route_profile_ids must be sorted"):
        ActivationReadback.model_validate(payload, strict=True)

    payload = _python_payload(document)
    payload["evidence_sources"] = ("registry", "installed_assets")
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError, match="evidence_sources must be sorted"):
        ActivationReadback.model_validate(payload, strict=True)


def test_collection_exact_maxima_and_max_plus_one_are_directly_discriminated() -> None:
    routes = tuple(f"route-{index:02d}" for index in range(64))
    readback_payload = _active()
    readback_payload["route_profile_ids"] = routes
    readback = _issue(readback_payload)
    assert len(readback.route_profile_ids) == 64

    invalid_readback = _python_payload(readback)
    invalid_readback["route_profile_ids"] = (*routes, "route-64")
    _refresh_readback_digest(invalid_readback)
    with pytest.raises(ValidationError) as readback_error:
        ActivationReadback.model_validate(invalid_readback, strict=True)
    assert any(
        error["loc"] == ("route_profile_ids",) and error["type"] == "too_long"
        for error in readback_error.value.errors()
    )

    principals = tuple(f"principal-{index:02d}" for index in range(64))
    capabilities = tuple(f"capability.{index:02d}" for index in range(64))
    grant_set = _grant_set(principal_ids=principals, capabilities=capabilities)
    assert len(grant_set.principal_ids) == 64
    assert len(grant_set.capabilities) == 64
    for field_name, values, extra in (
        ("principal_ids", principals, "principal-64"),
        ("capabilities", capabilities, "capability.64"),
    ):
        invalid_grant = grant_set.model_dump(mode="python")
        invalid_grant[field_name] = (*values, extra)
        invalid_grant["grant_set_digest"] = canonical_sha256(
            {key: value for key, value in invalid_grant.items() if key != "grant_set_digest"}
        )
        with pytest.raises(ValidationError) as grant_error:
            WorkbenchGrantSet.model_validate(invalid_grant, strict=True)
        assert any(
            error["loc"] == (field_name,) and error["type"] == "too_long"
            for error in grant_error.value.errors()
        )


def test_no_false_positive_from_stale_root_digest_for_semantic_negative() -> None:
    document = _issue(_active())
    payload = document.model_dump(mode="json")
    payload["reason_code"] = "REGISTRY_VERSION_UNSUPPORTED"
    payload["readback_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "readback_digest"}
    )
    with pytest.raises(ValidationError, match="active state requires reason_code"):
        ActivationReadback.model_validate_json(json.dumps(payload), strict=True)


def test_workbench_grant_set_collections_budget_ttl_and_digest() -> None:
    grant_set = _grant_set(
        principal_ids=("principal-b", "principal-a"),
        capabilities=("z.cap", "a.cap"),
    )
    assert grant_set.principal_ids == ("principal-a", "principal-b")
    assert grant_set.capabilities == ("a.cap", "z.cap")
    assert grant_set.max_ttl_ms == 900_000

    payload = grant_set.model_dump(mode="python")
    payload["principal_ids"] = ("principal-b", "principal-a")
    payload["grant_set_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "grant_set_digest"}
    )
    with pytest.raises(ValidationError, match="principal_ids must be sorted"):
        WorkbenchGrantSet.model_validate(payload, strict=True)

    for field_name, value in (
        ("max_ttl_ms", 999),
        ("max_ttl_ms", 900_001),
        ("runtime_generation", 0),
        ("activation_generation", 0),
    ):
        payload = grant_set.model_dump(mode="python")
        payload[field_name] = value
        payload["grant_set_digest"] = canonical_sha256(
            {key: value for key, value in payload.items() if key != "grant_set_digest"}
        )
        with pytest.raises(ValidationError):
            WorkbenchGrantSet.model_validate(payload, strict=True)

    payload = grant_set.model_dump(mode="json")
    payload["grant_set_digest"] = DIGEST
    with pytest.raises(ValidationError, match="grant set digest"):
        WorkbenchGrantSet.model_validate_json(json.dumps(payload), strict=True)


def test_issued_grant_has_exact_internal_shape_budget_bounds_and_time_order() -> None:
    grant = _grant()
    assert len(IssuedWorkbenchGrant.model_fields) == 20
    assert "schema_version" not in IssuedWorkbenchGrant.model_fields
    assert "grant_digest" not in IssuedWorkbenchGrant.model_fields
    assert grant.revoked is False

    payload = grant.model_dump(mode="python")
    payload["issued_at_unix_ms"] = payload["expires_at_unix_ms"]
    with pytest.raises(ValidationError, match="issued_at_unix_ms must be less"):
        IssuedWorkbenchGrant.model_validate(payload, strict=True)
    payload = grant.model_dump(mode="python")
    payload["wall_time_ms"] = 999
    with pytest.raises(ValidationError):
        IssuedWorkbenchGrant.model_validate(payload, strict=True)
    payload = grant.model_dump(mode="python")
    payload["revoked"] = 1
    with pytest.raises(ValidationError):
        IssuedWorkbenchGrant.model_validate(payload, strict=True)


def test_nested_change_recomputes_containing_root_digest_and_wrong_roots_reject() -> None:
    original = _issue(_active())
    changed_methods = list(original.methods)
    changed = changed_methods[0].model_copy(update={"contract_id": "aar.changed.contract.v2"})
    changed_methods[0] = changed
    changed_payload = _active()
    changed_payload["methods"] = tuple(changed_methods)
    changed_document = _issue(changed_payload)
    assert changed_document.readback_digest != original.readback_digest

    root = original.model_dump(mode="json")
    root["readback_digest"] = DIGEST
    with pytest.raises(ValidationError, match="readback digest"):
        ActivationReadback.model_validate_json(json.dumps(root), strict=True)

    grant_original = _grant_set()
    grant_changed = grant_original.model_copy(
        update={"budget_ceiling": _budget(artifact_bytes=1)}
    )
    reissued = WorkbenchGrantSet.issue(
        schema_version=grant_changed.schema_version,
        runtime_generation=grant_changed.runtime_generation,
        activation_generation=grant_changed.activation_generation,
        profile_id=grant_changed.profile_id,
        profile_digest=grant_changed.profile_digest,
        activation_authority_digest=grant_changed.activation_authority_digest,
        capability_digest=grant_changed.capability_digest,
        route_catalog_digest=grant_changed.route_catalog_digest,
        principal_ids=grant_changed.principal_ids,
        session_binding_policy=grant_changed.session_binding_policy,
        capabilities=grant_changed.capabilities,
        budget_ceiling=grant_changed.budget_ceiling,
        max_ttl_ms=grant_changed.max_ttl_ms,
    )
    assert reissued.grant_set_digest != grant_original.grant_set_digest


def test_unknown_fields_strict_scalars_and_recursive_inert_import_boundary() -> None:
    payload = _python_payload(_issue(_active()))
    payload["unexpected"] = True
    _refresh_readback_digest(payload)
    with pytest.raises(ValidationError):
        ActivationReadback.model_validate(payload, strict=True)

    for model_type, document in (
        (BackendAvailability, _row("model.request")),
        (PlannerReadback, _planner()),
        (IssuedWorkbenchGrant, _grant()),
        (WorkbenchGrantSet, _grant_set()),
    ):
        candidate = document.model_dump(mode="python")
        first_field = next(iter(model_type.model_fields))
        candidate[first_field] = True
        with pytest.raises(ValidationError):
            model_type.model_validate(candidate, strict=True)

    readback_payload = _python_payload(_issue(_active()))
    readback_payload["observed_at_unix_ms"] = True
    _refresh_readback_digest(readback_payload)
    with pytest.raises(ValidationError) as readback_bool_error:
        ActivationReadback.model_validate(readback_payload, strict=True)
    assert any(
        error["loc"] == ("observed_at_unix_ms",) and error["type"] == "int_type"
        for error in readback_bool_error.value.errors()
    )

    grant_payload = _grant().model_dump(mode="python")
    grant_payload["wall_time_ms"] = True
    with pytest.raises(ValidationError) as grant_bool_error:
        IssuedWorkbenchGrant.model_validate(grant_payload, strict=True)
    assert any(
        error["loc"] == ("wall_time_ms",) and error["type"] == "int_type"
        for error in grant_bool_error.value.errors()
    )

    source = inspect.getsource(importlib.import_module("aar.provider_ready_runtime_models"))
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    assert imported_modules <= {
        "__future__",
        "collections.abc",
        "typing",
        "pydantic",
        "aar.broker_models",
        "aar.canonical",
        "aar.provider_ready_models",
        "aar.provider_ready_operator_models",
        "aar.schemas",
    }
    for forbidden in (
        "aar.runtime",
        "aar.providers",
        "aar.mcp",
        "import subprocess",
        "import socket",
        "import requests",
        "sqlite3",
        "os.environ",
        "time.time",
    ):
        assert forbidden not in source


def test_evidence_source_enum_and_json_array_path_are_intentionally_exercised() -> None:
    document = _issue(_profile_verified())
    payload = document.model_dump(mode="json")
    payload["evidence_sources"] = list(sorted(EVIDENCE_SOURCES))
    payload["readback_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "readback_digest"}
    )
    assert (
        ActivationReadback.model_validate_json(json.dumps(payload), strict=True).evidence_sources
        == tuple(sorted(EVIDENCE_SOURCES))
    )
    payload["evidence_sources"] = ["not-a-frozen-source"]
    payload["readback_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "readback_digest"}
    )
    with pytest.raises(ValidationError):
        ActivationReadback.model_validate_json(json.dumps(payload), strict=True)
