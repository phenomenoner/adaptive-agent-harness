from __future__ import annotations

import asyncio
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken

import aar.mcp.public_auth as public_auth
from aar.mcp.public_auth import OAuthIdentityProvider, OidcJwtTokenVerifier


class _SigningKey:
    def __init__(self, key) -> None:
        self.key = key


class _JwkClient:
    def __init__(self, key) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, _token: str) -> _SigningKey:
        return _SigningKey(self._key)


def _token(private_key, **overrides) -> str:
    claims = {
        "iss": "https://issuer.example",
        "aud": "https://mcp.example/mcp",
        "sub": "user-123",
        "azp": "codex-client",
        "scope": "aar:workspace profile",
        "exp": int(time.time()) + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": "test-key"})


def test_oidc_verifier_accepts_only_bound_asymmetric_scoped_tokens() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2_048)
    verifier = OidcJwtTokenVerifier(
        issuer="https://issuer.example",
        audience="https://mcp.example/mcp",
        jwks_url="https://issuer.example/jwks",
        required_scopes=("aar:workspace",),
        jwk_client=_JwkClient(private_key.public_key()),
    )

    accepted = asyncio.run(verifier.verify_token(_token(private_key)))
    assert accepted is not None
    assert accepted.subject == "user-123"
    assert accepted.client_id == "codex-client"
    assert accepted.resource == "https://mcp.example/mcp"
    assert accepted.scopes == ["aar:workspace", "profile"]

    assert asyncio.run(
        verifier.verify_token(_token(private_key, aud="https://other.example/mcp"))
    ) is None
    assert asyncio.run(verifier.verify_token(_token(private_key, scope="profile"))) is None
    assert asyncio.run(verifier.verify_token(_token(private_key, sub=""))) is None

    trailing_slash_verifier = OidcJwtTokenVerifier(
        issuer="https://issuer.example/",
        audience="https://mcp.example/mcp",
        jwks_url="https://issuer.example/jwks",
        required_scopes=("aar:workspace",),
        jwk_client=_JwkClient(private_key.public_key()),
    )
    assert asyncio.run(
        trailing_slash_verifier.verify_token(
            _token(private_key, iss="https://issuer.example/")
        )
    ) is not None


def test_oidc_verifier_rejects_symmetric_algorithm_configuration() -> None:
    try:
        OidcJwtTokenVerifier(
            issuer="https://issuer.example",
            audience="https://mcp.example/mcp",
            jwks_url="https://issuer.example/jwks",
            required_scopes=("aar:workspace",),
            algorithms=("HS256",),
        )
    except ValueError as error:
        assert "asymmetric" in str(error)
    else:  # pragma: no cover
        raise AssertionError("symmetric JWT algorithms must fail closed")


def test_oauth_identity_is_opaque_stable_and_scope_bound(monkeypatch) -> None:
    access_token = AccessToken(
        token="secret-token",
        client_id="client",
        scopes=["profile", "aar:workspace"],
        expires_at=int(time.time()) + 300,
        resource="https://mcp.example/mcp",
        subject="customer@example.com",
    )
    monkeypatch.setattr(public_auth, "get_access_token", lambda: access_token)
    provider = OAuthIdentityProvider(
        issuer="https://issuer.example/",
        required_scopes=("aar:workspace",),
    )

    first = provider.current_identity()
    second = provider.current_identity()
    assert first == second
    assert len(first.tenant_key) == 32
    assert "customer" not in first.tenant_key
    assert "customer" not in first.principal_id
    assert "customer" not in first.session_id
    assert first.scopes == ("aar:workspace", "profile")

    monkeypatch.setattr(
        public_auth,
        "get_access_token",
        lambda: AccessToken(
            token="secret-token",
            client_id="client",
            scopes=["profile"],
            subject="customer@example.com",
        ),
    )
    try:
        provider.current_identity()
    except public_auth.PublicAuthenticationError:
        pass
    else:  # pragma: no cover
        raise AssertionError("missing required scope must fail closed")
