"""Standalone provider-ready composition for a trusted-local Hermes host."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aar.broker_models import (
    BrokerContext,
    ModelRequest,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteProfile,
)
from aar.provider_ready_runtime_models import (
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    WorkbenchGrantSet,
)
from aar.runtime.model_broker import (
    ModelBroker,
    ModelProviderFailure,
    ReferenceModelBroker,
    StaticModelBrokerRegistry,
)
from aar.runtime.provider_ready_activation import (
    ProviderReadyActivationCoordinator,
    ProviderReadyActivationStore,
)
from aar.runtime.provider_ready_startup import ProviderReadyStartup, ProviderReadyStartupError


def load_hermes_route_catalog(path: Path) -> ModelRouteCatalog:
    """Load exact route-catalog bytes without following a symlinked authority path."""

    resolved = path.expanduser()
    if resolved.is_symlink():
        raise ProviderReadyStartupError("Hermes route catalog path must not be a symlink")
    try:
        return ModelRouteCatalog.model_validate_json(resolved.read_bytes(), strict=True)
    except (OSError, ValueError) as error:
        raise ProviderReadyStartupError("Hermes route catalog is unavailable or invalid") from error


class _CallerDelegatedModelBroker:
    """Fail closed if a caller-delegated route reaches service-owned execution."""

    def request(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse:
        del request, context, binding
        raise ModelProviderFailure(
            "Hermes caller-delegated routes require the claim/mark-send/commit protocol"
        )

    def reconcile(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse | None:
        del request, context, binding
        return None

    def close(self) -> None:
        return None


def build_hermes_model_registry(catalog: ModelRouteCatalog) -> StaticModelBrokerRegistry:
    """Bind only explicit deterministic or caller-delegated Hermes route drivers."""

    brokers: dict[str, ModelBroker] = {}
    unsupported: list[str] = []
    for profile in catalog.profiles:
        if profile.provider_driver == "reference-driver" and profile.provider == "reference":
            brokers[profile.profile_id] = ReferenceModelBroker()
        elif profile.provider_driver == "host-caller-driver-v1":
            brokers[profile.profile_id] = _CallerDelegatedModelBroker()
        else:
            unsupported.append(profile.profile_id)
    if unsupported:
        raise ProviderReadyStartupError(
            "standalone Hermes adapter has unsupported route drivers: "
            + ", ".join(unsupported)
        )
    return StaticModelBrokerRegistry(catalog, brokers=brokers)


def hermes_grant_set_factory(
    runtime_generation: int,
    readback: Any,
    capability_digest: str,
) -> WorkbenchGrantSet:
    """Project the installed host policy into one current-generation grant set."""

    intent = readback.profile.intent
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=runtime_generation,
        activation_generation=readback.authority.activation_generation,
        profile_id=intent.profile_id,
        profile_digest=readback.profile.profile_digest,
        activation_authority_digest=readback.authority.authority_digest,
        capability_digest=capability_digest,
        route_catalog_digest=intent.routes.catalog_digest,
        principal_ids=intent.grant_policy.principal_patterns,
        session_binding_policy="bind_exact_request_session",
        capabilities=intent.grant_policy.capabilities,
        budget_ceiling=intent.grant_policy.budget_ceiling,
        max_ttl_ms=intent.grant_policy.max_deadline_ms,
    )


def build_hermes_provider_ready_host(
    runtime_home: Path,
    route_catalog_path: Path,
    default_route_profile: str,
) -> tuple[ProviderReadyStartup, StaticModelBrokerRegistry, ModelRouteProfile]:
    """Build startup and model-route owners for the installed Hermes runtime."""

    catalog = load_hermes_route_catalog(route_catalog_path)
    profiles = {profile.profile_id: profile for profile in catalog.profiles}
    try:
        default_profile = profiles[default_route_profile]
    except KeyError as error:
        raise ProviderReadyStartupError(
            "default Hermes route profile is absent from the exact route catalog"
        ) from error
    registry = build_hermes_model_registry(catalog)
    startup = ProviderReadyStartup(
        runtime_home,
        ProviderReadyActivationCoordinator(
            ProviderReadyActivationStore(runtime_home / "authority")
        ),
        hermes_grant_set_factory,
    )
    return startup, registry, default_profile


__all__ = [
    "build_hermes_model_registry",
    "build_hermes_provider_ready_host",
    "hermes_grant_set_factory",
    "load_hermes_route_catalog",
]
