"""Credential-free public inputs for provider-ready fresh installation."""

from __future__ import annotations

import os

from aar.broker_models import ModelRouteCatalog
from aar.canonical import canonical_json_bytes
from aar.provider_ready_install_models import InstallCandidateReceipt
from aar.provider_ready_models import (
    ActivationGrantPolicy,
    ActivationPlannerBinding,
    ActivationRoutePolicy,
    ActivationRuntimeBinding,
    HostActivationIntent,
    MethodAdapterManifest,
)
from aar.provider_ready_package_factory import PACKAGE_FACTORY_DECLARATIONS
from aar.runtime._install_artifacts import (
    _open_input,
    _parse_json_bytes,
    _validate_receipt,
    issue_install_candidate_receipt,
)
from aar.runtime._install_fs import InstallerError, preflight_target

_TEMPLATE_KEYS = {
    "profile_id",
    "planner",
    "adapters",
    "routes",
    "grant_policy",
    "cutover_authority_store_id",
    "recovery_compatibility_digest",
}


def issue_candidate_receipt_from_path(
    wheel_path: os.PathLike[str] | str,
    *,
    source_commit: str,
) -> InstallCandidateReceipt:
    """Issue one exact candidate receipt without installing or activating anything."""

    opened = _open_input(wheel_path, label="wheel")
    try:
        return issue_install_candidate_receipt(
            opened.data,
            source_commit=source_commit,
        )
    finally:
        opened.close()


def issue_initial_host_activation_intent(
    runtime_home: os.PathLike[str] | str,
    *,
    candidate_receipt_path: os.PathLike[str] | str,
    route_catalog_path: os.PathLike[str] | str,
    template_path: os.PathLike[str] | str,
) -> HostActivationIntent:
    """Issue a generation-1 intent bound to an absent canonical runtime target."""

    target = preflight_target(runtime_home)
    receipt_input = _open_input(candidate_receipt_path, label="candidate receipt")
    catalog_input = _open_input(route_catalog_path, label="route catalog")
    template_input = _open_input(template_path, label="host intent template")
    try:
        receipt = _validate_receipt(receipt_input.data)
        _parse_json_bytes(catalog_input.data, label="route catalog")
        catalog = ModelRouteCatalog.model_validate_json(catalog_input.data, strict=True)
        template = _parse_json_bytes(template_input.data, label="host intent template")
    finally:
        template_input.close()
        catalog_input.close()
        receipt_input.close()
    if not isinstance(template, dict) or set(template) != _TEMPLATE_KEYS:
        raise InstallerError(
            "FRESH_INSTALL_INPUT_INVALID",
            "host intent template fields are not exact",
        )
    try:
        planner = ActivationPlannerBinding.model_validate_json(
            canonical_json_bytes(template["planner"]), strict=True
        )
        adapters_value = template["adapters"]
        if not isinstance(adapters_value, list):
            raise ValueError("adapters must be an array")
        adapters = tuple(
            MethodAdapterManifest.model_validate_json(canonical_json_bytes(item), strict=True)
            for item in adapters_value
        )
        factory_digests = {
            entry.factory_id: entry.implementation_digest for entry in receipt.factory_entries
        }
        declarations = {item.method: item for item in PACKAGE_FACTORY_DECLARATIONS}
        if {adapter.method for adapter in adapters} != set(declarations):
            raise InstallerError(
                "FRESH_INSTALL_FACTORY_MISMATCH",
                "host intent template method inventory differs from package declarations",
            )
        adapters = tuple(
            MethodAdapterManifest.issue(
                schema_version=adapter.schema_version,
                method=adapter.method,
                contract_id=declarations[adapter.method].contract_id,
                request_schema_digest=declarations[adapter.method].request_schema_digest,
                response_schema_digest=declarations[adapter.method].response_schema_digest,
                backend_kind=adapter.backend_kind,
                factory_id=declarations[adapter.method].factory_id,
                factory_digest=factory_digests[declarations[adapter.method].factory_id],
                adapter_id=adapter.adapter_id,
                adapter_generation_policy=adapter.adapter_generation_policy,
                reference_only=adapter.reference_only,
                evidence_tier=adapter.evidence_tier,
                lookup_supported=adapter.lookup_supported,
                cancel_supported=adapter.cancel_supported,
            )
            for adapter in adapters
        )
        route_template = ActivationRoutePolicy.model_validate_json(
            canonical_json_bytes(template["routes"]), strict=True
        )
        catalog_profiles = {profile.profile_id for profile in catalog.profiles}
        if not set(route_template.allowed_profile_ids).issubset(catalog_profiles):
            raise InstallerError(
                "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID",
                "host intent template allows a route absent from the exact catalog",
            )
        routes = ActivationRoutePolicy.issue(
            catalog_digest=catalog.catalog_digest,
            allowed_profile_ids=route_template.allowed_profile_ids,
            fallback_policy=route_template.fallback_policy,
            cache_policy=route_template.cache_policy,
        )
        grant_policy = ActivationGrantPolicy.model_validate_json(
            canonical_json_bytes(template["grant_policy"]), strict=True
        )
        runtime = ActivationRuntimeBinding(
            runtime_home_digest=target.runtime_home_digest,
            database_identity=target.database_identity,
            required_registry_version=6,
            programmable_backend="ipython",
            security_profile="trusted_local",
        )
        return HostActivationIntent.issue(
            schema_version="aar.host-activation-intent.v1",
            profile_id=template["profile_id"],
            activation_generation=1,
            previous_activation_authority_digest=None,
            candidate=receipt.candidate,
            runtime=runtime,
            planner=planner,
            adapters=adapters,
            routes=routes,
            grant_policy=grant_policy,
            cutover_authority_store_id=template["cutover_authority_store_id"],
            recovery_compatibility_digest=template["recovery_compatibility_digest"],
        )
    except (TypeError, ValueError) as error:
        raise InstallerError(
            "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID",
            "host intent template is invalid",
        ) from error
