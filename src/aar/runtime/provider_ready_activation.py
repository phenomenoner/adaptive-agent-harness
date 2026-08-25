"""Public façade for provider-ready activation and grant ownership."""

from __future__ import annotations

import os
import threading
from pathlib import Path

from aar.provider_ready_runtime_models import IssuedWorkbenchGrant, WorkbenchGrantSet

from ._activation_store import (
    ActivationBindingMismatch,
    ActivationGenerationConflict,
    ActivationGenerationStore,
    ActivationStoreError,
    ProviderReadyActivationError,
    ProviderReadyActivationResult,
)
from ._session_grants import GrantDenied, SessionGrantOwner

ProviderReadyActivationStoreError = ActivationStoreError
SessionGrantDenied = GrantDenied


class ProviderReadyActivationStore:
    """Compose durable generation authority with memory-only session grants."""

    def __init__(
        self,
        authority_root: str | os.PathLike[str],
        *,
        current_runtime_generation: int | None = None,
    ) -> None:
        lock = threading.RLock()
        self._durable = ActivationGenerationStore(
            authority_root,
            current_runtime_generation=current_runtime_generation,
            lock=lock,
        )
        self._sessions = SessionGrantOwner(
            current_grant_set=lambda: self._durable.current_grant_set,
            verify_persisted=self._durable.verify_persisted,
            lock=lock,
        )

    @property
    def authority_root(self) -> Path:
        return self._durable.authority_root

    @property
    def current_runtime_generation(self) -> int | None:
        return self._durable.current_runtime_generation

    @property
    def current_grant_set(self) -> WorkbenchGrantSet | None:
        return self._durable.current_grant_set

    def path_for_generation(self, runtime_generation: int) -> Path:
        return self._durable.path_for_generation(runtime_generation)

    grant_set_path = path_for_generation

    def publish(
        self,
        grant_set: WorkbenchGrantSet,
        *,
        runtime_generation: int | None = None,
        activation_generation: int | None = None,
        profile_id: str | None = None,
        profile_digest: str | None = None,
        capability_digest: str | None = None,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        result = self._durable.publish(
            grant_set,
            runtime_generation=runtime_generation,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )
        self._sessions.observe_activation(result.grant_set, replayed=result.replayed)
        return result

    publish_grant_set = publish

    def activate(
        self,
        *,
        runtime_generation: int,
        grant_set: WorkbenchGrantSet,
        activation_generation: int,
        profile_id: str,
        profile_digest: str,
        capability_digest: str,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        return self.publish(
            grant_set,
            runtime_generation=runtime_generation,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )

    def read(self, runtime_generation: int) -> WorkbenchGrantSet:
        return self._durable.read(runtime_generation)

    readback = read

    def issue_session_grant(
        self,
        *,
        principal_id: str,
        session_id: str,
        capability: str,
        issued_at_unix_ms: int,
        policy_approved: bool,
        expires_at_unix_ms: int | None = None,
        ttl_ms: int | None = None,
        grant_id: str | None = None,
    ) -> IssuedWorkbenchGrant:
        return self._sessions.issue_session_grant(
            principal_id=principal_id,
            session_id=session_id,
            capability=capability,
            issued_at_unix_ms=issued_at_unix_ms,
            policy_approved=policy_approved,
            expires_at_unix_ms=expires_at_unix_ms,
            ttl_ms=ttl_ms,
            grant_id=grant_id,
        )

    def accept_session_grant(
        self,
        grant: IssuedWorkbenchGrant | str,
        *,
        principal_id: str,
        session_id: str,
        capability: str,
        runtime_generation: int,
        now_unix_ms: int,
        activation_generation: int | None = None,
        profile_id: str | None = None,
        profile_digest: str | None = None,
        capability_digest: str | None = None,
    ) -> IssuedWorkbenchGrant:
        return self._sessions.accept_session_grant(
            grant,
            principal_id=principal_id,
            session_id=session_id,
            capability=capability,
            runtime_generation=runtime_generation,
            now_unix_ms=now_unix_ms,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
        )

    validate_session_grant = accept_session_grant
    accept_grant = accept_session_grant

    def revoke_session_grant(
        self, grant: IssuedWorkbenchGrant | str
    ) -> IssuedWorkbenchGrant:
        return self._sessions.revoke_session_grant(grant)

    revoke_grant = revoke_session_grant


def activate_provider_ready(
    store: ProviderReadyActivationStore,
    *,
    runtime_generation: int,
    grant_set: WorkbenchGrantSet,
    activation_generation: int,
    profile_id: str,
    profile_digest: str,
    capability_digest: str,
    activation_authority_digest: str | None = None,
    route_catalog_digest: str | None = None,
) -> ProviderReadyActivationResult:
    """Call the narrow post-generation activation seam."""

    return store.activate(
        runtime_generation=runtime_generation,
        grant_set=grant_set,
        activation_generation=activation_generation,
        profile_id=profile_id,
        profile_digest=profile_digest,
        capability_digest=capability_digest,
        activation_authority_digest=activation_authority_digest,
        route_catalog_digest=route_catalog_digest,
    )


class ProviderReadyActivationCoordinator:
    """Callable façade for the one explicit post-generation integration seam."""

    def __init__(self, store: ProviderReadyActivationStore) -> None:
        self._store = store

    @property
    def store(self) -> ProviderReadyActivationStore:
        return self._store

    def __call__(
        self,
        *,
        runtime_generation: int,
        grant_set: WorkbenchGrantSet,
        activation_generation: int,
        profile_id: str,
        profile_digest: str,
        capability_digest: str,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        return self.activate(
            runtime_generation=runtime_generation,
            grant_set=grant_set,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )

    def activate(
        self,
        *,
        runtime_generation: int,
        grant_set: WorkbenchGrantSet,
        activation_generation: int,
        profile_id: str,
        profile_digest: str,
        capability_digest: str,
        activation_authority_digest: str | None = None,
        route_catalog_digest: str | None = None,
    ) -> ProviderReadyActivationResult:
        return activate_provider_ready(
            self._store,
            runtime_generation=runtime_generation,
            grant_set=grant_set,
            activation_generation=activation_generation,
            profile_id=profile_id,
            profile_digest=profile_digest,
            capability_digest=capability_digest,
            activation_authority_digest=activation_authority_digest,
            route_catalog_digest=route_catalog_digest,
        )

    def issue_session_grant(self, **kwargs: object) -> IssuedWorkbenchGrant:
        return self._store.issue_session_grant(**kwargs)  # type: ignore[arg-type]

    def accept_session_grant(
        self, *args: object, **kwargs: object
    ) -> IssuedWorkbenchGrant:
        return self._store.accept_session_grant(*args, **kwargs)  # type: ignore[arg-type]

    def revoke_session_grant(
        self, *args: object, **kwargs: object
    ) -> IssuedWorkbenchGrant:
        return self._store.revoke_session_grant(*args, **kwargs)  # type: ignore[arg-type]


__all__ = [
    "ActivationBindingMismatch",
    "ActivationGenerationConflict",
    "ActivationStoreError",
    "GrantDenied",
    "ProviderReadyActivationCoordinator",
    "ProviderReadyActivationError",
    "ProviderReadyActivationResult",
    "ProviderReadyActivationStore",
    "ProviderReadyActivationStoreError",
    "SessionGrantDenied",
    "activate_provider_ready",
]
