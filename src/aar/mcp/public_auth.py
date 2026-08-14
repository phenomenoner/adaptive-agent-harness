"""OAuth identity and token verification for the public AAR MCP surface."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Protocol

import jwt
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken


class PublicAuthenticationError(RuntimeError):
    """The current request has no usable authenticated public identity."""


@dataclass(frozen=True, slots=True)
class PublicIdentity:
    """Non-secret, path-safe identity derived from a verified OAuth subject."""

    tenant_key: str
    principal_id: str
    session_id: str
    scopes: tuple[str, ...]


class PublicIdentityProvider(Protocol):
    def current_identity(self) -> PublicIdentity: ...


class OAuthIdentityProvider:
    """Resolve the current MCP bearer token into a stable opaque tenant identity."""

    def __init__(self, *, issuer: str, required_scopes: tuple[str, ...]) -> None:
        self._issuer = issuer
        self._required_scopes = frozenset(required_scopes)

    def current_identity(self) -> PublicIdentity:
        access_token = get_access_token()
        if access_token is None or not access_token.subject:
            raise PublicAuthenticationError("authenticated OAuth subject is required")
        scopes = frozenset(access_token.scopes)
        missing = sorted(self._required_scopes - scopes)
        if missing:
            raise PublicAuthenticationError(
                "authenticated token is missing required AAR scopes"
            )
        digest = hashlib.sha256(
            f"{self._issuer}\0{access_token.subject}".encode()
        ).hexdigest()
        tenant_key = digest[:32]
        return PublicIdentity(
            tenant_key=tenant_key,
            principal_id=f"oauth-principal-{tenant_key}",
            session_id=f"oauth-session-{tenant_key}",
            scopes=tuple(sorted(scopes)),
        )


class _SigningKey(Protocol):
    key: Any


class _JwkClient(Protocol):
    def get_signing_key_from_jwt(self, token: str) -> _SigningKey: ...


class OidcJwtTokenVerifier:
    """Verify asymmetric OIDC access tokens without persisting credentials or claims."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        required_scopes: tuple[str, ...],
        algorithms: tuple[str, ...] = ("RS256", "ES256"),
        leeway_seconds: int = 30,
        jwk_client: _JwkClient | None = None,
    ) -> None:
        if not algorithms or any(
            algorithm not in {"RS256", "RS384", "RS512", "ES256", "ES384", "EdDSA"}
            for algorithm in algorithms
        ):
            raise ValueError("only explicit asymmetric JWT algorithms are allowed")
        if leeway_seconds < 0 or leeway_seconds > 300:
            raise ValueError("JWT leeway must be between 0 and 300 seconds")
        self._issuer = issuer
        self._audience = audience
        self._required_scopes = frozenset(required_scopes)
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds
        self._jwk_client: _JwkClient = jwk_client or jwt.PyJWKClient(jwks_url)

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await asyncio.to_thread(
                self._jwk_client.get_signing_key_from_jwt, token
            )
            claims = await asyncio.to_thread(
                jwt.decode,
                token,
                signing_key.key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": ["exp", "iss", "aud", "sub"]},
            )
            subject = claims.get("sub")
            expires_at = claims.get("exp")
            if not isinstance(subject, str) or not subject:
                return None
            if isinstance(expires_at, bool) or not isinstance(expires_at, int):
                return None
            scopes = _token_scopes(claims)
            if not self._required_scopes.issubset(scopes):
                return None
            client_id = claims.get("azp", claims.get("client_id", "unknown-client"))
            if not isinstance(client_id, str) or not client_id:
                client_id = "unknown-client"
            return AccessToken(
                token=token,
                client_id=client_id,
                scopes=sorted(scopes),
                expires_at=expires_at,
                resource=self._audience,
                subject=subject,
            )
        except (jwt.PyJWTError, OSError, RuntimeError, TypeError, ValueError):
            return None


def _token_scopes(claims: dict[str, Any]) -> frozenset[str]:
    scopes: set[str] = set()
    scope_claim = claims.get("scope")
    if isinstance(scope_claim, str):
        scopes.update(item for item in scope_claim.split() if item)
    scp_claim = claims.get("scp")
    if isinstance(scp_claim, list) and all(isinstance(item, str) for item in scp_claim):
        scopes.update(item for item in scp_claim if item)
    return frozenset(scopes)


__all__ = [
    "OAuthIdentityProvider",
    "OidcJwtTokenVerifier",
    "PublicAuthenticationError",
    "PublicIdentity",
    "PublicIdentityProvider",
]
