"""The narrow post-publication provider-ready startup seam.

The clean installer owns publication of immutable install evidence. This module
verifies that evidence and the loaded package factory member before runtime
construction, binds host/runtime configuration to the immutable profile,
projects exact current-generation factories, and delegates grant publication to
the existing provider-ready activation coordinator.
"""

from __future__ import annotations

import hashlib
import inspect
import os
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aar.canonical import canonical_sha256
from aar.provider_ready_package_factory import (
    PACKAGE_FACTORY_DECLARATIONS,
    PACKAGE_FACTORY_WHEEL_MEMBER,
    PackageFactoryDeclaration,
)
from aar.provider_ready_runtime_models import (
    BackendAvailability,
    IssuedWorkbenchGrant,
    WorkbenchGrantSet,
)
from aar.rlm_workbench_models import build_workbench_capability
from aar.runtime._install_evidence import PublishedInstallReadback, verify_published_install
from aar.runtime.model_broker import ModelBrokerRegistry
from aar.runtime.provider_ready_activation import (
    ProviderReadyActivationCoordinator,
    ProviderReadyActivationResult,
    SessionGrantDenied,
)
from aar.schemas import Budget, Grant, PrincipalRef

GrantSetFactory = Callable[[int, PublishedInstallReadback, str], WorkbenchGrantSet]


class ProviderReadyStartupError(RuntimeError):
    """The startup preflight, factory projection, or activation contract failed."""


@dataclass(frozen=True, slots=True)
class LoadedPackageFactoryBinding:
    """One verified package declaration bound to its loaded executable method."""

    declaration: PackageFactoryDeclaration
    implementation_digest: str
    implementation: Callable[..., Any]


_FACTORY_METHOD_NAMES = {
    "model.request": "model_request",
    "subagent.submit": "subagent_submit",
    "subagent.result": "subagent_result",
    "evidence.query": "evidence_query",
    "artifact.put": "artifact_put",
    "effect.propose": "effect_propose",
}


def _read_loaded_factory_member() -> tuple[bytes, type[Any]]:
    """Read the actual source member owning the already-loaded broker methods."""

    from aar.runtime import brokers as brokers_module
    from aar.runtime.brokers import BoundBrokerFacade

    source = inspect.getsourcefile(BoundBrokerFacade)
    module_file = brokers_module.__file__
    if source is None or module_file is None:
        raise ProviderReadyStartupError("loaded package factory has no source member")
    source_path = Path(source)
    if source_path.resolve() != Path(module_file).resolve():
        raise ProviderReadyStartupError("loaded package factory source differs from module origin")
    if tuple(source_path.parts[-3:]) != ("aar", "runtime", "brokers.py"):
        raise ProviderReadyStartupError("loaded package factory has an unexpected member path")

    flags = os.O_RDONLY | os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(source_path, flags)
    except OSError as error:
        raise ProviderReadyStartupError("loaded package factory member cannot be opened") from error
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise ProviderReadyStartupError("loaded package factory member is not regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        identity_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        if identity_before != identity_after:
            raise ProviderReadyStartupError("loaded package factory changed during readback")
        member_bytes = b"".join(chunks)
        if len(member_bytes) != before.st_size:
            raise ProviderReadyStartupError("loaded package factory readback is incomplete")
        return member_bytes, BoundBrokerFacade
    finally:
        os.close(fd)


def load_package_factory_bindings() -> tuple[LoadedPackageFactoryBinding, ...]:
    """Hash the loaded member and bind all six executable broker methods."""

    member_bytes, owner = _read_loaded_factory_member()
    implementation_digest = f"sha256:{hashlib.sha256(member_bytes).hexdigest()}"
    bindings: list[LoadedPackageFactoryBinding] = []
    for declaration in PACKAGE_FACTORY_DECLARATIONS:
        method_name = _FACTORY_METHOD_NAMES[declaration.method]
        implementation = getattr(owner, method_name, None)
        if not callable(implementation):
            raise ProviderReadyStartupError(
                f"loaded package factory omits executable method: {declaration.method}"
            )
        if (
            getattr(implementation, "__module__", None) != "aar.runtime.brokers"
            or getattr(implementation, "__qualname__", None) != f"BoundBrokerFacade.{method_name}"
        ):
            raise ProviderReadyStartupError(
                f"loaded package factory method owner drifted: {declaration.method}"
            )
        bindings.append(
            LoadedPackageFactoryBinding(
                declaration=declaration,
                implementation_digest=implementation_digest,
                implementation=implementation,
            )
        )
    return tuple(bindings)


def _verify_package_factory_bindings(
    readback: PublishedInstallReadback,
    loaded: tuple[LoadedPackageFactoryBinding, ...],
) -> None:
    adapters = readback.profile.intent.adapters
    expected_methods = tuple(item.method for item in PACKAGE_FACTORY_DECLARATIONS)
    if tuple(adapter.method for adapter in adapters) != expected_methods:
        raise ProviderReadyStartupError(
            "published adapters do not match package factory method order"
        )
    if tuple(binding.declaration for binding in loaded) != PACKAGE_FACTORY_DECLARATIONS:
        raise ProviderReadyStartupError("loaded package factory declaration inventory drifted")

    entries = {entry.factory_id: entry for entry in readback.receipt.factory_entries}
    if len(entries) != len(readback.receipt.factory_entries) or set(entries) != {
        item.factory_id for item in PACKAGE_FACTORY_DECLARATIONS
    }:
        raise ProviderReadyStartupError(
            "published receipt factory inventory differs from package declarations"
        )
    for binding, adapter in zip(loaded, adapters, strict=True):
        declaration = binding.declaration
        entry = entries[declaration.factory_id]
        observed_contract = (
            adapter.factory_id,
            adapter.contract_id,
            adapter.request_schema_digest,
            adapter.response_schema_digest,
        )
        expected_contract = (
            declaration.factory_id,
            declaration.contract_id,
            declaration.request_schema_digest,
            declaration.response_schema_digest,
        )
        if observed_contract != expected_contract:
            raise ProviderReadyStartupError(
                f"published adapter differs from package factory: {adapter.method}"
            )
        if entry.wheel_member != PACKAGE_FACTORY_WHEEL_MEMBER:
            raise ProviderReadyStartupError(
                f"published factory member differs from package owner: {adapter.method}"
            )
        if not (
            entry.implementation_digest == adapter.factory_digest == binding.implementation_digest
        ):
            raise ProviderReadyStartupError(
                f"loaded package factory digest differs from installed authority: {adapter.method}"
            )


def _project_backend_availability(
    readback: PublishedInstallReadback,
    loaded: tuple[LoadedPackageFactoryBinding, ...],
    runtime_generation: int,
) -> tuple[BackendAvailability, ...]:
    """Bind verified loaded factories to truthful current-generation rows."""

    _verify_package_factory_bindings(readback, loaded)
    rows: list[BackendAvailability] = []
    for binding, adapter in zip(loaded, readback.profile.intent.adapters, strict=True):
        if not callable(binding.implementation):  # pragma: no cover - dataclass invariant guard
            raise ProviderReadyStartupError(f"package factory is not executable: {adapter.method}")
        if adapter.backend_kind == "reference":
            adapter_id = None
            adapter_generation = None
            evidence_tier = "unknown"
        else:
            adapter_id = adapter.adapter_id
            adapter_generation = runtime_generation
            evidence_tier = adapter.evidence_tier
        rows.append(
            BackendAvailability(
                method=adapter.method,
                contract_id=adapter.contract_id,
                request_schema_digest=adapter.request_schema_digest,
                response_schema_digest=adapter.response_schema_digest,
                backend_kind=adapter.backend_kind,
                configured=True,
                reference_only=adapter.reference_only,
                adapter_id=adapter_id,
                adapter_generation=adapter_generation,
                evidence_tier=evidence_tier,
            )
        )
    return tuple(rows)


def _availability_mapping(
    rows: tuple[BackendAvailability, ...],
) -> dict[str, dict[str, Any]]:
    return {
        row.method: {
            "backend_kind": row.backend_kind,
            "configured": row.configured,
            "reference_only": row.reference_only,
            "adapter_id": row.adapter_id,
            "adapter_generation": row.adapter_generation,
            "evidence_tier": row.evidence_tier,
        }
        for row in rows
    }


def _budget_within(issued: IssuedWorkbenchGrant, requested: Budget) -> bool:
    return (
        requested.wall_time_ms <= issued.wall_time_ms
        and requested.model_requests <= issued.model_requests
        and requested.input_tokens <= issued.input_tokens
        and requested.output_tokens <= issued.output_tokens
        and requested.child_operations <= issued.child_operations
        and requested.artifact_bytes <= issued.artifact_bytes
    )


class ProviderReadyStartup:
    """Verify one published runtime and activate one fenced runtime generation.

    ``grant_set_factory`` receives the normal host generation, immutable startup
    readback, and the digest of the exact package-derived workbench capability.
    Route, principal, budget, and TTL policy remain explicit installed inputs and
    are validated byte-for-byte against the returned grant set.
    """

    def __init__(
        self,
        runtime_home: str | Path,
        coordinator: ProviderReadyActivationCoordinator,
        grant_set_factory: GrantSetFactory,
    ) -> None:
        path = Path(runtime_home)
        if not path.is_absolute():
            raise ValueError("provider-ready runtime_home must be absolute")
        if not callable(grant_set_factory):
            raise TypeError("grant_set_factory must be callable")
        expected_authority = (path / "authority").resolve()
        if coordinator.store.authority_root.resolve() != expected_authority:
            raise ProviderReadyStartupError(
                "provider-ready coordinator must own runtime_home/authority"
            )
        self.runtime_home = path
        self.coordinator = coordinator
        self.grant_set_factory = grant_set_factory
        self._lock = threading.RLock()
        self._preflight_readback: PublishedInstallReadback | None = None
        self._loaded_factories: tuple[LoadedPackageFactoryBinding, ...] | None = None
        self._host_factory_owner: object | None = None
        self._prepared_runtime_generation: int | None = None
        self._backend_rows: tuple[BackendAvailability, ...] | None = None
        self._capability_digest: str | None = None
        self._activation_result: ProviderReadyActivationResult | None = None
        self._grant_set_readback: WorkbenchGrantSet | None = None

    def preflight(self) -> PublishedInstallReadback:
        """Read immutable install evidence and loaded package bytes once without writes."""

        with self._lock:
            if self._preflight_readback is None:
                readback = verify_published_install(
                    self.runtime_home,
                    allow_runtime_state=True,
                )
                loaded = load_package_factory_bindings()
                _verify_package_factory_bindings(readback, loaded)
                self._preflight_readback = readback
                self._loaded_factories = loaded
            return self._preflight_readback

    def assert_database_path(self, database_path: str | Path) -> None:
        """Require the normal runtime database below this startup runtime home."""

        expected = (self.runtime_home / "reference.sqlite3").resolve()
        observed = Path(database_path).resolve()
        if observed != expected:
            raise ProviderReadyStartupError(
                "provider-ready startup database must be runtime_home/reference.sqlite3"
            )

    def validate_host_configuration(
        self,
        *,
        programmable_backend: str,
        model_broker_registry: ModelBrokerRegistry | None,
        default_model_route_profile: str | None,
    ) -> None:
        """Bind executable host choices to the immutable profile before generation allocation."""

        intent = self.preflight().profile.intent
        if intent.runtime.required_registry_version != 6:
            raise ProviderReadyStartupError("provider-ready startup requires registry v6")
        if programmable_backend != intent.runtime.programmable_backend:
            raise ProviderReadyStartupError(
                "host programmable backend differs from the installed runtime profile"
            )
        if model_broker_registry is None or default_model_route_profile is None:
            raise ProviderReadyStartupError(
                "provider-ready startup requires the installed model-route owner"
            )
        catalog = model_broker_registry.describe()
        policy = intent.routes
        if catalog.catalog_digest != policy.catalog_digest:
            raise ProviderReadyStartupError(
                "host model-route catalog differs from the installed route policy"
            )
        profiles = {profile.profile_id: profile for profile in catalog.profiles}
        if default_model_route_profile not in policy.allowed_profile_ids:
            raise ProviderReadyStartupError(
                "default model route is not allowed by the installed route policy"
            )
        missing = sorted(set(policy.allowed_profile_ids) - set(profiles))
        if missing:
            raise ProviderReadyStartupError(
                "installed route policy names profiles absent from the host catalog"
            )
        for profile_id in policy.allowed_profile_ids:
            profile = profiles[profile_id]
            if (
                profile.fallback_policy != policy.fallback_policy
                or profile.cache_policy != policy.cache_policy
            ):
                raise ProviderReadyStartupError(
                    f"host model route policy differs for profile: {profile_id}"
                )

    def bind_host_factory_owner(self, facade: object) -> None:
        """Bind capability projection to the host's actual loaded broker façade."""

        from aar.runtime.brokers import BoundBrokerFacade, TypedBrokerFacade

        with self._lock:
            self.preflight()
            if type(facade) is not TypedBrokerFacade:
                raise ProviderReadyStartupError(
                    "provider-ready host factory owner is not the loaded TypedBrokerFacade"
                )
            assert self._loaded_factories is not None
            for binding in self._loaded_factories:
                method_name = _FACTORY_METHOD_NAMES[binding.declaration.method]
                if binding.implementation is not getattr(BoundBrokerFacade, method_name):
                    raise ProviderReadyStartupError(
                        "loaded package factory implementation changed before host binding"
                    )
            if self._host_factory_owner is not None and self._host_factory_owner is not facade:
                raise ProviderReadyStartupError(
                    "provider-ready startup is already bound to a different host factory owner"
                )
            self._host_factory_owner = facade

    def backend_availability(self, runtime_generation: int) -> Mapping[str, Mapping[str, Any]]:
        """Project and cache exact loaded factories for one runtime generation."""

        with self._lock:
            if self._host_factory_owner is None:
                raise ProviderReadyStartupError(
                    "provider-ready backend availability requires the bound host factory owner"
                )
            prepared = self._prepared_runtime_generation
            if prepared is not None and prepared != runtime_generation:
                raise ProviderReadyStartupError(
                    "provider-ready startup is already prepared for a different generation"
                )
            if self._backend_rows is None:
                readback = self.preflight()
                assert self._loaded_factories is not None
                rows = _project_backend_availability(
                    readback,
                    self._loaded_factories,
                    runtime_generation,
                )
                capability = build_workbench_capability(_availability_mapping(rows))
                self._backend_rows = rows
                self._capability_digest = canonical_sha256(capability.root)
                self._prepared_runtime_generation = runtime_generation
            return _availability_mapping(self._backend_rows)

    def activate(self, runtime_generation: int) -> ProviderReadyActivationResult:
        """Activate one generation, accepting an exact same-generation replay."""

        with self._lock:
            existing = self._activation_result
            if existing is not None:
                if existing.runtime_generation != runtime_generation:
                    raise ProviderReadyStartupError(
                        "provider-ready startup is already bound to a different runtime generation"
                    )
                return existing

            readback = self.preflight()
            self.backend_availability(runtime_generation)
            assert self._capability_digest is not None
            grant_set = self.grant_set_factory(
                runtime_generation,
                readback,
                self._capability_digest,
            )
            if not isinstance(grant_set, WorkbenchGrantSet):
                raise TypeError("grant_set_factory must return WorkbenchGrantSet")
            intent = readback.profile.intent
            expected_binding = (
                runtime_generation,
                readback.authority.activation_generation,
                intent.profile_id,
                readback.profile.profile_digest,
                readback.authority.authority_digest,
                self._capability_digest,
                intent.routes.catalog_digest,
                tuple(intent.grant_policy.principal_patterns),
                "bind_exact_request_session",
                tuple(intent.grant_policy.capabilities),
                intent.grant_policy.budget_ceiling,
                intent.grant_policy.max_deadline_ms,
            )
            observed_binding = (
                grant_set.runtime_generation,
                grant_set.activation_generation,
                grant_set.profile_id,
                grant_set.profile_digest,
                grant_set.activation_authority_digest,
                grant_set.capability_digest,
                grant_set.route_catalog_digest,
                tuple(grant_set.principal_ids),
                grant_set.session_binding_policy,
                tuple(grant_set.capabilities),
                grant_set.budget_ceiling,
                grant_set.max_ttl_ms,
            )
            if observed_binding != expected_binding:
                raise ProviderReadyStartupError(
                    "grant set differs from current capability, route, policy, or install authority"
                )
            result = self.coordinator.activate(
                runtime_generation=runtime_generation,
                grant_set=grant_set,
                activation_generation=readback.authority.activation_generation,
                profile_id=intent.profile_id,
                profile_digest=readback.profile.profile_digest,
                capability_digest=self._capability_digest,
                activation_authority_digest=readback.authority.authority_digest,
                route_catalog_digest=intent.routes.catalog_digest,
            )
            persisted = self.coordinator.store.read(runtime_generation)
            if (
                result.runtime_generation != runtime_generation
                or result.grant_set != grant_set
                or persisted != result.grant_set
            ):
                raise ProviderReadyStartupError(
                    "provider-ready grant-set readback is not byte-identical to activation"
                )
            self._grant_set_readback = persisted
            self._activation_result = result
            return result

    def resolve_session_grants(
        self,
        grant_ids: tuple[str, ...],
        *,
        principal_id: str,
        session_id: str,
        runtime_generation: int,
        now_unix_ms: int,
        deadline_unix_ms: int,
        required_capability: str,
        required_capabilities: tuple[str, ...] = (),
        budget: Budget,
    ) -> tuple[Grant, ...]:
        """Resolve sorted current memory-only grants for MCP admission."""

        if tuple(sorted(set(grant_ids))) != grant_ids:
            raise SessionGrantDenied("session grant IDs must be sorted and unique")
        grant_set = self.grant_set
        resolved: list[IssuedWorkbenchGrant] = []
        resolved_capabilities: set[str] = set()
        for grant_id in grant_ids:
            grant = self.coordinator.accept_session_grant(
                grant_id,
                principal_id=principal_id,
                session_id=session_id,
                capability=None,
                runtime_generation=runtime_generation,
                now_unix_ms=now_unix_ms,
                activation_generation=grant_set.activation_generation,
                profile_id=grant_set.profile_id,
                profile_digest=grant_set.profile_digest,
                capability_digest=grant_set.capability_digest,
            )
            if grant.expires_at_unix_ms < deadline_unix_ms:
                raise SessionGrantDenied("session grant expires before the request deadline")
            if not _budget_within(grant, budget):
                raise SessionGrantDenied("request budget exceeds the issued session grant")
            if grant.capability in resolved_capabilities:
                raise SessionGrantDenied("session grants must not duplicate a capability")
            resolved_capabilities.add(grant.capability)
            resolved.append(grant)
        required = {required_capability, *required_capabilities}
        missing = sorted(required - resolved_capabilities)
        if missing:
            raise SessionGrantDenied(
                "current session grants omit required capability: " + ", ".join(missing)
            )
        principal = PrincipalRef(value=principal_id)
        return tuple(
            Grant(
                grant_id=grant.grant_id,
                capability=grant.capability,
                issued_to=principal,
                expires_at_unix_ms=grant.expires_at_unix_ms,
            )
            for grant in resolved
        )

    def __call__(self, runtime_generation: int) -> ProviderReadyActivationResult:
        return self.activate(runtime_generation)

    @property
    def preflight_readback(self) -> PublishedInstallReadback:
        with self._lock:
            if self._preflight_readback is None:
                raise ProviderReadyStartupError("provider-ready startup has not been preflighted")
            return self._preflight_readback

    @property
    def activation_result(self) -> ProviderReadyActivationResult:
        with self._lock:
            if self._activation_result is None:
                raise ProviderReadyStartupError("provider-ready startup is not active")
            return self._activation_result

    @property
    def grant_set(self) -> WorkbenchGrantSet:
        with self._lock:
            if self._grant_set_readback is None:
                raise ProviderReadyStartupError("provider-ready startup is not active")
            return self._grant_set_readback

    @property
    def backend_rows(self) -> tuple[BackendAvailability, ...]:
        with self._lock:
            if self._backend_rows is None:
                raise ProviderReadyStartupError("provider-ready factories are not prepared")
            return self._backend_rows

    @property
    def loaded_factories(self) -> tuple[LoadedPackageFactoryBinding, ...]:
        with self._lock:
            if self._loaded_factories is None:
                raise ProviderReadyStartupError("package factories have not been verified")
            return self._loaded_factories


__all__ = [
    "GrantSetFactory",
    "LoadedPackageFactoryBinding",
    "ProviderReadyStartup",
    "ProviderReadyStartupError",
    "load_package_factory_bindings",
]
