"""Strict host-caller readiness values for caller-delegated work.

The readiness envelope is intentionally provider-neutral and contains no
credential or provider request body.  It binds one already-reserved durable
caller-work ticket to one local relay process before AAR crosses the
conservative may-have-sent boundary.
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit

from pydantic import StringConstraints, field_validator

from aar.schemas import Digest, OpaqueToken, PositiveCounter, StrictModel

CALLER_DRIVER_READY_SCHEMA_VERSION = "aar.caller-driver-ready.v1"

_IPV4_OCTET_PATTERN = r"(?:25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
_LOOPBACK_HOST_PATTERN = rf"(?:localhost|127(?:\.{_IPV4_OCTET_PATTERN}){{3}}|\[::1\])"
_EXPLICIT_PORT_PATTERN = (
    r"(?:[1-9][0-9]{0,3}|[1-5][0-9]{4}|6[0-4][0-9]{3}|"
    r"65[0-4][0-9]{2}|655[0-2][0-9]|6553[0-5])"
)
_LOOPBACK_HTTP_BASE_URL_PATTERN = rf"^http://{_LOOPBACK_HOST_PATTERN}:{_EXPLICIT_PORT_PATTERN}/?$"

LoopbackHttpBaseUrl = Annotated[
    str,
    StringConstraints(
        min_length=14,
        max_length=255,
        pattern=_LOOPBACK_HTTP_BASE_URL_PATTERN,
        strict=True,
    ),
]


class CallerDriverReadyEnvelope(StrictModel):
    """Canonical newline-framed readiness for one local caller relay."""

    schema_version: Literal["aar.caller-driver-ready.v1"]
    event: Literal["ready"]
    launch_nonce: OpaqueToken
    adapter_id: OpaqueToken
    adapter_generation: PositiveCounter
    ticket_id: OpaqueToken
    ticket_digest: Digest
    request_digest: Digest
    physical_attempt_id: OpaqueToken
    base_url: LoopbackHttpBaseUrl

    @field_validator("base_url")
    @classmethod
    def base_url_is_explicit_loopback_http(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise ValueError("base_url must contain a valid explicit port") from error
        if parsed.scheme != "http":
            raise ValueError("base_url must use http")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain user information")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain a query or fragment")
        if parsed.path not in ("", "/"):
            raise ValueError("base_url must not contain an endpoint path")
        if parsed.hostname is None or port is None:
            raise ValueError("base_url must contain an explicit host and port")
        hostname = parsed.hostname.lower()
        if hostname != "localhost":
            try:
                address = ipaddress.ip_address(hostname)
            except ValueError as error:
                raise ValueError("base_url host must be loopback") from error
            if not address.is_loopback:
                raise ValueError("base_url host must be loopback")
        return value

    @classmethod
    def issue(
        cls,
        *,
        launch_nonce: str,
        adapter_id: str,
        adapter_generation: int,
        ticket_id: str,
        ticket_digest: str,
        request_digest: str,
        physical_attempt_id: str,
        base_url: str,
    ) -> Self:
        """Construct one explicit v1 readiness envelope."""

        return cls(
            schema_version=CALLER_DRIVER_READY_SCHEMA_VERSION,
            event="ready",
            launch_nonce=launch_nonce,
            adapter_id=adapter_id,
            adapter_generation=adapter_generation,
            ticket_id=ticket_id,
            ticket_digest=ticket_digest,
            request_digest=request_digest,
            physical_attempt_id=physical_attempt_id,
            base_url=base_url,
        )
