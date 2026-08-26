"""Strict private supervisor protocol models for the AR-LT2 attachment boundary.

This module contains only contracts and deterministic parsing helpers.  It does not open,
close, or kill processes and it never stores an attachment credential in a model.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from typing import Annotated, Any, Literal, Self, cast

from pydantic import AliasChoices, Field, StringConstraints, TypeAdapter, model_validator

from aar.canonical import canonical_sha256
from aar.runtime.process_identity import (
    DEFAULT_PROTOCOL_VERSION,
    DISCOVERY_SCHEMA_VERSION,
    DiscoveryRecord,
    ProcessIdentity,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
)
from aar.schemas import Digest, OpaqueToken, PositiveCounter, StrictModel

SUPERVISOR_SCHEMA_VERSION = "aar.supervisor.v1"
SUPERVISOR_PROTOCOL_VERSION = DEFAULT_PROTOCOL_VERSION
PRIVATE_PROTOCOL_VERSION = SUPERVISOR_PROTOCOL_VERSION
SUPERVISOR_PRIVATE_PROTOCOL_VERSION = SUPERVISOR_PROTOCOL_VERSION
SUPERVISOR_DISCOVERY_SCHEMA_VERSION = DISCOVERY_SCHEMA_VERSION
SUPERVISOR_GRANT_CONTROL_SCHEMA_VERSION = "aar.supervisor.grant-control.v1"
MAX_PRIVATE_PAYLOAD_BYTES = 8 * 1024 * 1024
MAX_PAYLOAD_BYTES = MAX_PRIVATE_PAYLOAD_BYTES
MAX_MCP_JSON_BYTES = MAX_PRIVATE_PAYLOAD_BYTES
SUPERVISOR_PROTOCOL_DIGEST = canonical_sha256(
    {
        "schema_version": SUPERVISOR_SCHEMA_VERSION,
        "protocol_version": SUPERVISOR_PROTOCOL_VERSION,
        "message_kinds": (
            "attach",
            "attached",
            "error",
            "grant_issue",
            "grant_issued",
            "grant_revoke",
            "grant_revoked",
            "mcp_request",
            "mcp_response",
        ),
        "max_private_payload_bytes": MAX_PRIVATE_PAYLOAD_BYTES,
    }
)

MessageKind = Literal[
    "attach",
    "attached",
    "grant_issue",
    "grant_issued",
    "grant_revoke",
    "grant_revoked",
    "mcp_request",
    "mcp_response",
    "error",
]
LifecycleState = Literal[
    "starting",
    "migrating",
    "recovering",
    "ready",
    "degraded",
    "reconcile-required",
    "draining",
    "stopped",
]
ProtocolText = Annotated[
    str,
    StringConstraints(min_length=1, max_length=512, strict=True),
]


class SupervisorProtocolError(ValueError):
    """A private protocol value failed an exact contract check."""


class PrivateFrame(StrictModel):
    """Authenticated metadata and bounded base64 payload for one private message."""

    schema_version: Literal["aar.supervisor.v1"] = SUPERVISOR_SCHEMA_VERSION
    protocol_version: Literal["aar.supervisor.protocol.v1"] = Field(
        SUPERVISOR_PROTOCOL_VERSION,
        validation_alias=AliasChoices("protocol_version", "protocol"),
    )
    kind: MessageKind = Field(validation_alias=AliasChoices("kind", "message_kind"))
    request_id: OpaqueToken
    trace_id: OpaqueToken
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    authority_digest: Digest
    payload_length: Annotated[
        int,
        Field(ge=0, le=MAX_PRIVATE_PAYLOAD_BYTES, strict=True),
    ]
    payload_digest: Digest
    deadline_unix_ms: PositiveCounter
    attachment_digest: Digest = Field(
        validation_alias=AliasChoices("attachment_digest", "attachment_credential_digest")
    )
    payload_base64: Annotated[
        str,
        StringConstraints(
            min_length=0,
            max_length=((MAX_PRIVATE_PAYLOAD_BYTES + 2) // 3) * 4,
            strict=True,
        ),
    ] = Field(validation_alias=AliasChoices("payload_base64", "payload_b64", "payload"))

    @classmethod
    def issue(
        cls,
        *,
        kind: MessageKind,
        request_id: str,
        trace_id: str,
        runtime_generation: int,
        dispatcher_generation: int,
        authority_digest: str,
        deadline_unix_ms: int,
        attachment_digest: str | None = None,
        attachment_credential_digest: str | None = None,
        payload: bytes = b"",
        protocol_version: str = SUPERVISOR_PROTOCOL_VERSION,
    ) -> PrivateFrame:
        if not isinstance(payload, bytes):
            raise SupervisorProtocolError("private frame payload must be bytes")
        if len(payload) > MAX_PRIVATE_PAYLOAD_BYTES:
            raise SupervisorProtocolError(
                f"private frame payload exceeds {MAX_PRIVATE_PAYLOAD_BYTES} bytes"
            )
        resolved_attachment = _resolve_attachment_digest(
            attachment_digest, attachment_credential_digest
        )
        encoded = base64.b64encode(payload).decode("ascii")
        return cls(
            protocol_version=cast(Literal["aar.supervisor.protocol.v1"], protocol_version),
            kind=kind,
            request_id=request_id,
            trace_id=trace_id,
            runtime_generation=runtime_generation,
            dispatcher_generation=dispatcher_generation,
            authority_digest=authority_digest,
            payload_length=len(payload),
            payload_digest=_bytes_digest(payload),
            deadline_unix_ms=deadline_unix_ms,
            attachment_digest=resolved_attachment,
            payload_base64=encoded,
        )

    @model_validator(mode="after")
    def payload_is_exact_and_bounded(self) -> Self:
        try:
            payload = _decode_base64_payload(self.payload_base64)
        except SupervisorProtocolError:
            raise
        except (ValueError, binascii.Error) as error:
            raise ValueError("private frame payload base64 is invalid") from error
        if len(payload) > MAX_PRIVATE_PAYLOAD_BYTES:
            raise ValueError(
                f"private frame payload exceeds {MAX_PRIVATE_PAYLOAD_BYTES} bytes"
            )
        if self.payload_length != len(payload):
            raise ValueError("private frame payload length does not match decoded payload")
        if self.payload_digest != _bytes_digest(payload):
            raise ValueError("private frame payload digest does not match decoded payload")
        return self

    def decoded_payload(self) -> bytes:
        """Return the already-validated payload bytes."""

        return _decode_base64_payload(self.payload_base64)

    def decode_payload(self) -> bytes:
        """Compatibility spelling for ``decoded_payload``."""

        return self.decoded_payload()

    @property
    def payload(self) -> bytes:
        """Return the validated raw payload without retaining a second copy in the model."""

        return self.decoded_payload()

    def __repr__(self) -> str:
        # Do not include payload_base64: a frame repr is safe even when the payload is sensitive.
        return (
            "PrivateFrame("
            f"protocol_version={self.protocol_version!r}, kind={self.kind!r}, "
            f"request_id={self.request_id!r}, trace_id={self.trace_id!r}, "
            f"runtime_generation={self.runtime_generation!r}, "
            f"dispatcher_generation={self.dispatcher_generation!r}, "
            f"authority_digest={self.authority_digest!r}, "
            f"payload_length={self.payload_length!r}, payload_digest={self.payload_digest!r}, "
            f"deadline_unix_ms={self.deadline_unix_ms!r}, "
            f"attachment_digest={self.attachment_digest!r})"
        )


class SupervisorAttachPayload(StrictModel):
    """Payload carried by an ``attach`` frame; only credential digest is retained."""

    schema_version: Literal["aar.supervisor.v1"] = SUPERVISOR_SCHEMA_VERSION
    protocol_version: Literal["aar.supervisor.protocol.v1"] = Field(
        SUPERVISOR_PROTOCOL_VERSION,
        validation_alias=AliasChoices("protocol_version", "protocol"),
    )
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    authority_digest: Digest
    deadline_unix_ms: PositiveCounter
    attachment_digest: Digest = Field(
        validation_alias=AliasChoices("attachment_digest", "attachment_credential_digest")
    )
    capability_digest: Digest | None = None
    runtime_home_digest: Digest | None = None
    client_id: OpaqueToken | None = None
    adapter_version: ProtocolText | None = None


class SupervisorAttachAck(StrictModel):
    """Supervisor response proving the exact generation and process that accepted an attach."""

    schema_version: Literal["aar.supervisor.v1"] = SUPERVISOR_SCHEMA_VERSION
    protocol_version: Literal["aar.supervisor.protocol.v1"] = Field(
        SUPERVISOR_PROTOCOL_VERSION,
        validation_alias=AliasChoices("protocol_version", "protocol"),
    )
    accepted: bool = Field(validation_alias=AliasChoices("accepted", "ok"))
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    authority_digest: Digest
    deadline_unix_ms: PositiveCounter
    attachment_digest: Digest = Field(
        validation_alias=AliasChoices("attachment_digest", "attachment_credential_digest")
    )
    capability_digest: Digest
    runtime_home_digest: Digest
    process_identity: ProcessStartIdentity
    supervisor_version: ProtocolText
    ready_at_unix_ms: PositiveCounter
    reason_code: ProtocolText | None = Field(
        default=None,
        validation_alias=AliasChoices("reason_code", "reason"),
    )

    @model_validator(mode="after")
    def rejection_is_explained(self) -> Self:
        if not self.accepted and self.reason_code is None:
            raise ValueError("rejected supervisor attach requires reason_code")
        return self


class SupervisorGrantIssueRequest(StrictModel):
    """One explicit, owner-authenticated request for a memory-only session grant."""

    schema_version: Literal["aar.supervisor.grant-control.v1"] = (
        SUPERVISOR_GRANT_CONTROL_SCHEMA_VERSION
    )
    principal_id: OpaqueToken
    session_id: OpaqueToken
    capability: OpaqueToken
    ttl_ms: PositiveCounter
    grant_id: OpaqueToken


class SupervisorGrantRevokeRequest(StrictModel):
    """One explicit request to revoke a grant owned by the current supervisor process."""

    schema_version: Literal["aar.supervisor.grant-control.v1"] = (
        SUPERVISOR_GRANT_CONTROL_SCHEMA_VERSION
    )
    grant_id: OpaqueToken


class SupervisorLifecycleReceipt(StrictModel):
    """Digest-bound lifecycle observation for a supervisor process."""

    schema_version: Literal["aar.supervisor.v1"] = SUPERVISOR_SCHEMA_VERSION
    protocol_version: Literal["aar.supervisor.protocol.v1"] = Field(
        SUPERVISOR_PROTOCOL_VERSION,
        validation_alias=AliasChoices("protocol_version", "protocol"),
    )
    state: LifecycleState = Field(
        validation_alias=AliasChoices("state", "lifecycle", "lifecycle_state")
    )
    process_identity: ProcessStartIdentity
    runtime_generation: PositiveCounter
    dispatcher_generation: PositiveCounter
    capability_digest: Digest
    runtime_home_digest: Digest
    at_unix_ms: PositiveCounter = Field(
        validation_alias=AliasChoices("at_unix_ms", "recorded_at_unix_ms", "ready_at_unix_ms")
    )
    supervisor_version: ProtocolText
    reason: ProtocolText | None = None
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        state: LifecycleState,
        process_identity: ProcessStartIdentity,
        runtime_generation: int,
        dispatcher_generation: int,
        capability_digest: str,
        runtime_home_digest: str,
        at_unix_ms: int,
        supervisor_version: str,
        protocol_version: str = SUPERVISOR_PROTOCOL_VERSION,
        reason: str | None = None,
    ) -> SupervisorLifecycleReceipt:
        payload = {
            "schema_version": SUPERVISOR_SCHEMA_VERSION,
            "protocol_version": protocol_version,
            "state": state,
            "process_identity": process_identity.model_dump(mode="json"),
            "runtime_generation": runtime_generation,
            "dispatcher_generation": dispatcher_generation,
            "capability_digest": capability_digest,
            "runtime_home_digest": runtime_home_digest,
            "at_unix_ms": at_unix_ms,
            "supervisor_version": supervisor_version,
            "reason": reason,
        }
        return cls(**payload, receipt_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def receipt_is_self_digest_bound(self) -> Self:
        if self.process_identity.pid <= 0:
            raise ValueError("lifecycle receipt process identity must have a positive PID")
        payload = self.model_dump(mode="json", exclude={"receipt_digest"})
        if self.receipt_digest != canonical_sha256(payload):
            raise ValueError("lifecycle receipt digest does not match canonical receipt bytes")
        return self

    def canonical_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_digest"})

    def validate_receipt_digest(self) -> SupervisorLifecycleReceipt:
        if self.receipt_digest != canonical_sha256(self.canonical_payload()):
            raise ValueError("lifecycle receipt digest does not match canonical receipt bytes")
        return self


class McpAuthorityDeadline(StrictModel):
    """The two private-frame bindings extracted from one raw MCP JSON-RPC request."""

    authority_digest: Digest
    deadline_unix_ms: PositiveCounter


_MISSING = object()
_AUTHORITY_PATHS: tuple[tuple[str, ...], ...] = (
    ("authority_digest",),
    ("params", "authority_digest"),
    ("params", "arguments", "authority_digest"),
    ("params", "arguments", "context", "authority_digest"),
    ("params", "context", "authority_digest"),
    ("params", "arguments", "authority", "digest"),
)
_DEADLINE_PATHS: tuple[tuple[str, ...], ...] = (
    ("deadline_unix_ms",),
    ("params", "deadline_unix_ms"),
    ("params", "arguments", "deadline_unix_ms"),
    ("params", "arguments", "context", "deadline_unix_ms"),
    ("params", "context", "deadline_unix_ms"),
)
_AUTHORITY_CONTEXT_PATHS: tuple[tuple[str, ...], ...] = (
    ("params", "arguments", "context"),
    ("params", "context"),
    ("params", "arguments"),
)
_AUTHORITY_KEYS = (
    "host",
    "host_id",
    "principal",
    "principal_id",
    "lane",
    "lane_id",
    "session",
    "session_id",
    "capability_digest",
    "grant_id",
    "grant_ids",
)


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _resolve_attachment_digest(
    attachment_digest: str | None,
    attachment_credential_digest: str | None,
) -> str:
    if attachment_digest is not None and attachment_credential_digest is not None:
        if attachment_digest != attachment_credential_digest:
            raise SupervisorProtocolError(
                "attachment_digest and attachment_credential_digest must match"
            )
        return attachment_digest
    resolved = attachment_digest or attachment_credential_digest
    if resolved is None:
        raise SupervisorProtocolError("attachment credential digest is required")
    return resolved


def _decode_base64_payload(encoded: str) -> bytes:
    try:
        raw = encoded.encode("ascii")
        payload = base64.b64decode(raw, validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as error:
        raise SupervisorProtocolError("private frame payload base64 is invalid") from error
    if base64.b64encode(payload) != raw:
        raise SupervisorProtocolError("private frame payload base64 is not canonical")
    return payload


def _duplicate_rejecting_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SupervisorProtocolError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _parse_mcp_json(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise SupervisorProtocolError("raw MCP JSON-RPC input must be bytes")
    if len(raw) > MAX_MCP_JSON_BYTES:
        raise SupervisorProtocolError(f"raw MCP JSON-RPC input exceeds {MAX_MCP_JSON_BYTES} bytes")
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_duplicate_rejecting_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError, SupervisorProtocolError) as error:
        raise SupervisorProtocolError("raw MCP JSON-RPC input is not valid JSON") from error
    if not isinstance(document, dict):
        raise SupervisorProtocolError("raw MCP JSON-RPC input must be a JSON object")
    jsonrpc = document.get("jsonrpc", _MISSING)
    if jsonrpc is not _MISSING and jsonrpc != "2.0":
        raise SupervisorProtocolError("raw MCP JSON-RPC input must use jsonrpc 2.0")
    return document


def _lookup(document: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = document
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return _MISSING
        current = current[key]
    return current


def _consistent_values(
    document: Mapping[str, Any], paths: tuple[tuple[str, ...], ...], label: str
) -> list[Any]:
    values = [_lookup(document, path) for path in paths]
    present = [value for value in values if value is not _MISSING]
    if len(present) > 1 and any(value != present[0] for value in present[1:]):
        raise SupervisorProtocolError(f"conflicting MCP {label} values")
    return present


def _strict_digest(value: Any, label: str) -> str:
    try:
        return TypeAdapter(Digest).validate_python(value, strict=True)
    except ValueError as error:
        raise SupervisorProtocolError(f"MCP {label} must be a SHA-256 digest") from error


def _strict_deadline(value: Any) -> int:
    try:
        return TypeAdapter(PositiveCounter).validate_python(value, strict=True)
    except ValueError as error:
        raise SupervisorProtocolError("MCP deadline_unix_ms must be a positive integer") from error


def _derived_authority(document: Mapping[str, Any]) -> str | None:
    for path in _AUTHORITY_CONTEXT_PATHS:
        context = _lookup(document, path)
        if not isinstance(context, Mapping):
            continue
        material = {key: context[key] for key in _AUTHORITY_KEYS if key in context}
        if material:
            return canonical_sha256(material)
    return None


def extract_mcp_binding(raw: bytes) -> McpAuthorityDeadline:
    """Extract authority and deadline deterministically from raw MCP JSON-RPC bytes.

    Explicit ``authority_digest``/``deadline_unix_ms`` values may appear at the request,
    params, arguments, or flat context level.  Conflicting copies fail closed.  If an
    authority digest is omitted, it is derived only from the fixed allow-list of authority
    context keys; the deadline must always be explicit.
    """

    document = _parse_mcp_json(raw)
    authority_values = _consistent_values(document, _AUTHORITY_PATHS, "authority_digest")
    authority = (
        _strict_digest(authority_values[0], "authority_digest")
        if authority_values
        else _derived_authority(document)
    )
    if authority is None:
        raise SupervisorProtocolError("MCP authority_digest is missing")
    deadline_values = _consistent_values(document, _DEADLINE_PATHS, "deadline_unix_ms")
    if not deadline_values:
        raise SupervisorProtocolError("MCP deadline_unix_ms is missing")
    deadline = _strict_deadline(deadline_values[0])
    return McpAuthorityDeadline(authority_digest=authority, deadline_unix_ms=deadline)


def extract_mcp_authority_and_deadline(raw: bytes) -> tuple[str, int]:
    """Return ``(authority_digest, deadline_unix_ms)`` from raw MCP JSON-RPC bytes."""

    binding = extract_mcp_binding(raw)
    return binding.authority_digest, binding.deadline_unix_ms


def extract_mcp_authority_deadline(raw: bytes) -> tuple[str, int]:
    """Compatibility spelling for ``extract_mcp_authority_and_deadline``."""

    return extract_mcp_authority_and_deadline(raw)


def extract_mcp_authority(raw: bytes) -> str:
    """Extract only the deterministic authority digest from raw MCP JSON-RPC bytes."""

    return extract_mcp_binding(raw).authority_digest


def extract_mcp_deadline(raw: bytes) -> int:
    """Extract only the positive deadline from raw MCP JSON-RPC bytes."""

    return extract_mcp_binding(raw).deadline_unix_ms


# Names used by callers that prefer shorter contract labels.
Frame = PrivateFrame
PrivateFrameV1 = PrivateFrame
AttachPayload = SupervisorAttachPayload
AttachRequest = SupervisorAttachPayload
AttachPayloadV1 = SupervisorAttachPayload
AttachAck = SupervisorAttachAck
AttachedAck = SupervisorAttachAck
AttachAcknowledgement = SupervisorAttachAck
LifecycleReceipt = SupervisorLifecycleReceipt
SupervisorReadyReceipt = SupervisorLifecycleReceipt
SupervisorTerminalReceipt = SupervisorLifecycleReceipt
SupervisorFrame = PrivateFrame
SupervisorAttachRequest = SupervisorAttachPayload
SupervisorAttached = SupervisorAttachAck
SupervisorReceipt = SupervisorLifecycleReceipt
McpRequestBinding = McpAuthorityDeadline
extract_authority_and_deadline = extract_mcp_authority_and_deadline

SUPERVISOR_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "supervisor_discovery_record": SupervisorDiscoveryRecord,
    "private_frame": PrivateFrame,
    "supervisor_attach_payload": SupervisorAttachPayload,
    "supervisor_attach_ack": SupervisorAttachAck,
    "supervisor_grant_issue_request": SupervisorGrantIssueRequest,
    "supervisor_grant_revoke_request": SupervisorGrantRevokeRequest,
    "supervisor_lifecycle_receipt": SupervisorLifecycleReceipt,
    "mcp_authority_deadline": McpAuthorityDeadline,
}

__all__ = [
    "DISCOVERY_SCHEMA_VERSION",
    "MAX_MCP_JSON_BYTES",
    "MAX_PAYLOAD_BYTES",
    "MAX_PRIVATE_PAYLOAD_BYTES",
    "PRIVATE_PROTOCOL_VERSION",
    "SUPERVISOR_DISCOVERY_SCHEMA_VERSION",
    "SUPERVISOR_GRANT_CONTROL_SCHEMA_VERSION",
    "SUPERVISOR_PRIVATE_PROTOCOL_VERSION",
    "SUPERVISOR_PROTOCOL_VERSION",
    "SUPERVISOR_SCHEMA_MODELS",
    "SUPERVISOR_SCHEMA_VERSION",
    "AttachAck",
    "AttachAcknowledgement",
    "AttachPayload",
    "AttachPayloadV1",
    "AttachRequest",
    "AttachedAck",
    "DiscoveryRecord",
    "Frame",
    "LifecycleReceipt",
    "LifecycleState",
    "McpAuthorityDeadline",
    "McpRequestBinding",
    "MessageKind",
    "PrivateFrame",
    "PrivateFrameV1",
    "ProcessIdentity",
    "ProcessIdentityUnavailable",
    "ProcessStartIdentity",
    "SupervisorAttachAck",
    "SupervisorAttachPayload",
    "SupervisorAttachRequest",
    "SupervisorAttached",
    "SupervisorDiscoveryRecord",
    "SupervisorFrame",
    "SupervisorGrantIssueRequest",
    "SupervisorGrantRevokeRequest",
    "SupervisorLifecycleReceipt",
    "SupervisorProtocolError",
    "SupervisorReadyReceipt",
    "SupervisorReceipt",
    "SupervisorTerminalReceipt",
    "extract_authority_and_deadline",
    "extract_mcp_authority",
    "extract_mcp_authority_and_deadline",
    "extract_mcp_authority_deadline",
    "extract_mcp_binding",
    "extract_mcp_deadline",
]
