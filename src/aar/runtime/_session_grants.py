"""Memory-only session-grant ownership for provider-ready activation.

The owner receives current-grant and persisted-verification callbacks. It has no
filesystem path, descriptor, or durable-store implementation knowledge, so a
process restart necessarily starts with an empty live-grant map.
"""

from __future__ import annotations

import threading
import uuid
from typing import Final, Protocol

from aar.provider_ready_runtime_models import IssuedWorkbenchGrant, WorkbenchGrantSet

from ._activation_store import ActivationBindingMismatch, ProviderReadyActivationError

_MAX_COUNTER: Final = 9_223_372_036_854_775_807


class CurrentGrantSetProvider(Protocol):
    def __call__(self) -> WorkbenchGrantSet | None: ...


class PersistedGrantSetVerifier(Protocol):
    def __call__(self, current_set: WorkbenchGrantSet) -> None: ...


class GrantDenied(ProviderReadyActivationError):
    """A memory-only session grant is absent, stale, revoked, or unauthorized."""

    code = "GRANT_DENIED"


class SessionGrantOwner:
    """Own issue, accept, and revoke for grants that never persist to disk."""

    def __init__(
        self,
        *,
        current_grant_set: CurrentGrantSetProvider,
        verify_persisted: PersistedGrantSetVerifier,
        lock: object | None = None,
    ) -> None:
        self._current_grant_set_provider = current_grant_set
        self._verify_persisted = verify_persisted
        self._session_grants: dict[str, IssuedWorkbenchGrant] = {}
        self._observed_runtime_generation: int | None = None
        self._lock = lock if lock is not None else threading.RLock()

    def observe_activation(
        self,
        grant_set: WorkbenchGrantSet,
        *,
        replayed: bool,
    ) -> None:
        """Reset live grants only when a new durable activation is published."""

        with self._lock:
            if (
                not replayed
                and self._observed_runtime_generation != grant_set.runtime_generation
            ):
                self._session_grants.clear()
            self._observed_runtime_generation = grant_set.runtime_generation

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
        """Issue one current-generation, memory-only grant after explicit approval."""

        if policy_approved is not True:
            raise GrantDenied("session grant issuance requires explicit policy approval")
        issued_at = _validate_unix_ms(issued_at_unix_ms, "issued_at_unix_ms")
        if (expires_at_unix_ms is None) == (ttl_ms is None):
            raise GrantDenied("provide exactly one of ttl_ms or expires_at_unix_ms")

        with self._lock:
            current_set = self._require_current_grant_set()
            self._verify_current_persisted(current_set)
            if principal_id not in current_set.principal_ids:
                raise GrantDenied("principal is not present in the current grant set")
            if capability not in current_set.capabilities:
                raise GrantDenied("capability is not present in the current grant set")

            if ttl_ms is not None:
                ttl = _validate_positive_int(ttl_ms, "ttl_ms")
                if ttl > current_set.max_ttl_ms:
                    raise GrantDenied("requested session-grant TTL exceeds policy")
                expires = issued_at + ttl
            else:
                assert expires_at_unix_ms is not None
                expires = _validate_unix_ms(expires_at_unix_ms, "expires_at_unix_ms")
                if expires <= issued_at:
                    raise GrantDenied("session-grant expiry must follow issuance")
                if expires - issued_at > current_set.max_ttl_ms:
                    raise GrantDenied("requested session-grant TTL exceeds policy")
            if expires > _MAX_COUNTER:
                raise GrantDenied("session-grant expiry exceeds the model counter domain")

            actual_grant_id = grant_id if grant_id is not None else f"grant-{uuid.uuid4().hex}"
            if actual_grant_id in self._session_grants:
                raise GrantDenied("grant_id is already live in this activation")
            try:
                grant = IssuedWorkbenchGrant(
                    grant_id=actual_grant_id,
                    grant_set_digest=current_set.grant_set_digest,
                    capability=capability,
                    principal_id=principal_id,
                    session_id=session_id,
                    runtime_generation=current_set.runtime_generation,
                    activation_generation=current_set.activation_generation,
                    profile_digest=current_set.profile_digest,
                    activation_authority_digest=current_set.activation_authority_digest,
                    capability_digest=current_set.capability_digest,
                    route_catalog_digest=current_set.route_catalog_digest,
                    wall_time_ms=current_set.budget_ceiling.wall_time_ms,
                    model_requests=current_set.budget_ceiling.model_requests,
                    input_tokens=current_set.budget_ceiling.input_tokens,
                    output_tokens=current_set.budget_ceiling.output_tokens,
                    child_operations=current_set.budget_ceiling.child_operations,
                    artifact_bytes=current_set.budget_ceiling.artifact_bytes,
                    issued_at_unix_ms=issued_at,
                    expires_at_unix_ms=expires,
                    revoked=False,
                )
            except (TypeError, ValueError) as error:
                raise GrantDenied(f"session-grant request is not valid: {error}") from error
            self._session_grants[grant.grant_id] = grant
            return grant


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
        """Accept one grant only while every current binding and time fence holds."""

        generation = _validate_generation(runtime_generation)
        now = _validate_unix_ms(now_unix_ms, "now_unix_ms")
        with self._lock:
            stored = self._resolve_session_grant(grant)
            if stored.revoked:
                raise GrantDenied("session grant is revoked")
            current_set = self._require_current_grant_set()
            self._verify_current_persisted(current_set)
            if generation != current_set.runtime_generation:
                raise GrantDenied("session grant runtime generation is stale")
            if stored.runtime_generation != generation:
                raise GrantDenied("session grant belongs to a different runtime generation")
            if stored.grant_set_digest != current_set.grant_set_digest:
                raise GrantDenied("session grant digest is not current")
            if stored.activation_generation != current_set.activation_generation:
                raise GrantDenied("session grant activation generation is stale")
            if stored.profile_digest != current_set.profile_digest:
                raise GrantDenied("session grant profile binding is stale")
            if stored.activation_authority_digest != current_set.activation_authority_digest:
                raise GrantDenied("session grant activation authority is stale")
            if stored.capability_digest != current_set.capability_digest:
                raise GrantDenied("session grant capability binding is stale")
            if stored.route_catalog_digest != current_set.route_catalog_digest:
                raise GrantDenied("session grant route binding is stale")
            if stored.principal_id != principal_id:
                raise GrantDenied("session grant principal binding does not match")
            if stored.session_id != session_id:
                raise GrantDenied("session grant session binding does not match")
            if stored.capability != capability or capability not in current_set.capabilities:
                raise GrantDenied("session grant capability binding does not match")
            if (
                activation_generation is not None
                and activation_generation != current_set.activation_generation
            ):
                raise GrantDenied("activation generation context does not match")
            if profile_id is not None and profile_id != current_set.profile_id:
                raise GrantDenied("profile context does not match")
            if profile_digest is not None and profile_digest != current_set.profile_digest:
                raise GrantDenied("profile digest context does not match")
            if capability_digest is not None and capability_digest != current_set.capability_digest:
                raise GrantDenied("capability digest context does not match")
            if now < stored.issued_at_unix_ms or now >= stored.expires_at_unix_ms:
                raise GrantDenied("session grant is expired or not yet valid")
            return stored


    def revoke_session_grant(self, grant: IssuedWorkbenchGrant | str) -> IssuedWorkbenchGrant:
        """Revoke one known in-memory grant without touching the filesystem."""

        with self._lock:
            stored = self._resolve_session_grant(grant)
            revoked = stored.model_copy(update={"revoked": True})
            self._session_grants[stored.grant_id] = revoked
            return revoked

    revoke_grant = revoke_session_grant


    def _require_current_grant_set(self) -> WorkbenchGrantSet:
        current_set = self._current_grant_set_provider()
        if current_set is None:
            raise GrantDenied("no current post-generation activation exists")
        if self._observed_runtime_generation != current_set.runtime_generation:
            self._session_grants.clear()
            self._observed_runtime_generation = current_set.runtime_generation
        return current_set


    def _verify_current_persisted(self, current_set: WorkbenchGrantSet) -> None:
        try:
            self._verify_persisted(current_set)
        except ProviderReadyActivationError as error:
            refreshed = self._current_grant_set_provider()
            if (
                refreshed is not None
                and refreshed.runtime_generation != current_set.runtime_generation
            ):
                self._session_grants.clear()
                self._observed_runtime_generation = refreshed.runtime_generation
            raise GrantDenied("current grant-set authority cannot be read back") from error

    def _resolve_session_grant(
        self,
        grant: IssuedWorkbenchGrant | str,
    ) -> IssuedWorkbenchGrant:
        grant_id = grant.grant_id if isinstance(grant, IssuedWorkbenchGrant) else grant
        if not isinstance(grant_id, str):
            raise GrantDenied("session grant identifier is invalid")
        stored = self._session_grants.get(grant_id)
        if stored is None:
            raise GrantDenied("session grant is not owned by this activation process")
        if isinstance(grant, IssuedWorkbenchGrant) and stored != grant:
            raise GrantDenied("session grant bytes are not the owned immutable grant")
        return stored


    validate_session_grant = accept_session_grant
    accept_grant = accept_session_grant
    revoke_grant = revoke_session_grant


def _validate_generation(value: int) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_COUNTER:
        raise ActivationBindingMismatch("runtime_generation must be a positive strict integer")
    return value


def _validate_unix_ms(value: int, field_name: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_COUNTER:
        raise GrantDenied(f"{field_name} is outside the strict Unix-ms domain")
    return value


def _validate_positive_int(value: int, field_name: str) -> int:
    if type(value) is not int or not 1 <= value <= _MAX_COUNTER:
        raise GrantDenied(f"{field_name} must be a positive strict integer")
    return value


__all__ = [
    "CurrentGrantSetProvider",
    "GrantDenied",
    "PersistedGrantSetVerifier",
    "SessionGrantOwner",
]
