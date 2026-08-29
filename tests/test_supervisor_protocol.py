from __future__ import annotations

import base64
import json

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.runtime.process_identity import current_process_identity
from aar.runtime.supervisor_protocol import (
    MAX_PRIVATE_PAYLOAD_BYTES,
    AttachAck,
    AttachPayload,
    LifecycleReceipt,
    McpAuthorityDeadline,
    PrivateFrame,
    SupervisorAttachAck,
    SupervisorAttachPayload,
    SupervisorGrantIssueRequest,
    SupervisorGrantRevokeRequest,
    SupervisorLifecycleReceipt,
    SupervisorProtocolError,
    extract_mcp_authority,
    extract_mcp_authority_and_deadline,
    extract_mcp_authority_deadline,
    extract_mcp_binding,
    extract_mcp_deadline,
)

DIGEST = canonical_sha256({"fixture": "supervisor-protocol"})
OTHER_DIGEST = canonical_sha256({"fixture": "different"})
IDENTITY = current_process_identity()


def _frame_document() -> dict[str, object]:
    frame = PrivateFrame.issue(
        kind="mcp_request",
        request_id="request-1",
        trace_id="trace-1",
        runtime_generation=2,
        dispatcher_generation=4,
        authority_digest=DIGEST,
        deadline_unix_ms=10_000,
        attachment_digest=DIGEST,
        payload=b'{"jsonrpc":"2.0","method":"tools/call"}',
    )
    return frame.model_dump(mode="json")


def test_private_frame_issue_decode_round_trip_and_safe_repr() -> None:
    secret = b"attachment-secret-must-not-appear-in-repr"
    frame = PrivateFrame.issue(
        kind="attach",
        request_id="request-attach",
        trace_id="trace-attach",
        runtime_generation=1,
        dispatcher_generation=1,
        authority_digest=DIGEST,
        deadline_unix_ms=123,
        attachment_credential_digest=DIGEST,
        payload=secret,
    )

    restored = PrivateFrame.model_validate_json(frame.model_dump_json(), strict=True)
    assert restored == frame
    assert restored.decoded_payload() == secret
    assert restored.decode_payload() == secret
    assert restored.payload == secret
    assert secret not in repr(restored).encode("utf-8")
    assert restored.payload_length == len(secret)
    assert restored.payload_digest.startswith("sha256:")


def test_private_frame_rejects_wrong_digest_length_base64_generation_and_deadline() -> None:
    wrong_digest = _frame_document()
    wrong_digest["payload_digest"] = OTHER_DIGEST
    with pytest.raises(ValidationError, match="payload digest"):
        PrivateFrame.model_validate(wrong_digest, strict=True)

    wrong_length = _frame_document()
    wrong_length["payload_length"] = 0
    with pytest.raises(ValidationError, match="payload length"):
        PrivateFrame.model_validate(wrong_length, strict=True)

    wrong_base64 = _frame_document()
    wrong_base64["payload_base64"] = "not-base64***"
    with pytest.raises(ValidationError, match="base64"):
        PrivateFrame.model_validate(wrong_base64, strict=True)

    wrong_generation = _frame_document()
    wrong_generation["runtime_generation"] = 0
    with pytest.raises(ValidationError):
        PrivateFrame.model_validate(wrong_generation, strict=True)

    wrong_deadline = _frame_document()
    wrong_deadline["deadline_unix_ms"] = 0
    with pytest.raises(ValidationError):
        PrivateFrame.model_validate(wrong_deadline, strict=True)

    with pytest.raises(SupervisorProtocolError, match="exceeds"):
        PrivateFrame.issue(
            kind="mcp_request",
            request_id="request-large",
            trace_id="trace-large",
            runtime_generation=1,
            dispatcher_generation=1,
            authority_digest=DIGEST,
            deadline_unix_ms=1,
            attachment_digest=DIGEST,
            payload=b"x" * (MAX_PRIVATE_PAYLOAD_BYTES + 1),
        )


def test_private_frame_accepts_attachment_digest_alias_only() -> None:
    document = _frame_document()
    document.pop("attachment_digest")
    document["attachment_credential_digest"] = DIGEST
    frame = PrivateFrame.model_validate(document, strict=True)
    assert frame.attachment_digest == DIGEST


def test_attach_ack_and_lifecycle_receipt_round_trip() -> None:
    payload = SupervisorAttachPayload(
        runtime_generation=2,
        dispatcher_generation=3,
        authority_digest=DIGEST,
        deadline_unix_ms=100,
        protocol_version="aar.supervisor.protocol.v1",
        attachment_digest=DIGEST,
        capability_digest=DIGEST,
        runtime_home_digest=OTHER_DIGEST,
        client_id="adapter-1",
    )
    assert AttachPayload.model_validate_json(payload.model_dump_json(), strict=True) == payload

    ack = SupervisorAttachAck(
        protocol_version="aar.supervisor.protocol.v1",
        accepted=True,
        runtime_generation=2,
        dispatcher_generation=3,
        authority_digest=DIGEST,
        deadline_unix_ms=100,
        attachment_digest=DIGEST,
        capability_digest=DIGEST,
        runtime_home_digest=OTHER_DIGEST,
        process_identity=IDENTITY,
        supervisor_version="aar-supervisor-test",
        ready_at_unix_ms=99,
    )
    assert AttachAck.model_validate_json(ack.model_dump_json(), strict=True) == ack

    receipt = SupervisorLifecycleReceipt.issue(
        state="ready",
        process_identity=IDENTITY,
        runtime_generation=2,
        dispatcher_generation=3,
        capability_digest=DIGEST,
        runtime_home_digest=OTHER_DIGEST,
        at_unix_ms=99,
        supervisor_version="aar-supervisor-test",
    )
    assert LifecycleReceipt.model_validate_json(receipt.model_dump_json(), strict=True) == receipt
    assert receipt.validate_receipt_digest() == receipt


def test_mcp_authority_and_deadline_extraction_is_deterministic() -> None:
    raw = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {
                "name": "aar_capabilities",
                "arguments": {
                    "context": {
                        "authority_digest": DIGEST,
                        "deadline_unix_ms": 42,
                    }
                },
            },
        },
        separators=(",", ":"),
    ).encode()

    binding = extract_mcp_binding(raw)
    assert isinstance(binding, McpAuthorityDeadline)
    assert binding == McpAuthorityDeadline(authority_digest=DIGEST, deadline_unix_ms=42)
    assert extract_mcp_authority_and_deadline(raw) == (DIGEST, 42)
    assert extract_mcp_authority_deadline(raw) == (DIGEST, 42)
    assert extract_mcp_authority(raw) == DIGEST
    assert extract_mcp_deadline(raw) == 42


def test_mcp_authority_can_be_derived_from_fixed_context_keys() -> None:
    context = {
        "principal_id": "principal-1",
        "session_id": "session-1",
        "capability_digest": DIGEST,
        "deadline_unix_ms": 88,
    }
    raw = json.dumps({"jsonrpc": "2.0", "params": {"arguments": {"context": context}}}).encode()
    expected = canonical_sha256(
        {
            "principal_id": "principal-1",
            "session_id": "session-1",
            "capability_digest": DIGEST,
        }
    )
    assert extract_mcp_binding(raw) == McpAuthorityDeadline(
        authority_digest=expected,
        deadline_unix_ms=88,
    )


def test_mcp_extraction_rejects_invalid_or_conflicting_bindings() -> None:
    missing_deadline = json.dumps({"jsonrpc": "2.0", "authority_digest": DIGEST}).encode()
    with pytest.raises(SupervisorProtocolError, match="deadline"):
        extract_mcp_binding(missing_deadline)

    invalid_digest = json.dumps(
        {"jsonrpc": "2.0", "authority_digest": "not-a-digest", "deadline_unix_ms": 1}
    ).encode()
    with pytest.raises(SupervisorProtocolError, match="digest"):
        extract_mcp_binding(invalid_digest)

    conflicting = json.dumps(
        {
            "jsonrpc": "2.0",
            "authority_digest": DIGEST,
            "params": {"deadline_unix_ms": 1, "arguments": {"deadline_unix_ms": 2}},
        }
    ).encode()
    with pytest.raises(SupervisorProtocolError, match="conflicting"):
        extract_mcp_binding(conflicting)

    duplicate_key = b'{"jsonrpc":"2.0","deadline_unix_ms":1,"deadline_unix_ms":2}'
    with pytest.raises(SupervisorProtocolError, match="valid JSON"):
        extract_mcp_binding(duplicate_key)


def test_base64_payload_is_canonical() -> None:
    document = _frame_document()
    payload = b"x"
    document["payload_base64"] = base64.b64encode(payload).decode("ascii").rstrip("=")
    document["payload_length"] = len(payload)
    document["payload_digest"] = "sha256:"
    document["payload_digest"] += "0" * 64
    with pytest.raises(ValidationError, match="base64"):
        PrivateFrame.model_validate(document, strict=True)


def test_grant_control_requests_are_strict_and_frameable() -> None:
    issue = SupervisorGrantIssueRequest(
        principal_id="principal-local",
        session_id="session-local",
        capability="rlm.workbench.execute",
        ttl_ms=60_000,
        grant_id="grant-explicit",
    )
    frame = PrivateFrame.issue(
        kind="grant_issue",
        request_id="request-grant-issue",
        trace_id="trace-grant-issue",
        runtime_generation=2,
        dispatcher_generation=2,
        authority_digest=DIGEST,
        deadline_unix_ms=4_102_444_800_000,
        attachment_digest=DIGEST,
        payload=issue.model_dump_json().encode(),
    )
    restored = PrivateFrame.model_validate_json(frame.model_dump_json(), strict=True)
    assert restored.kind == "grant_issue"
    assert (
        SupervisorGrantIssueRequest.model_validate_json(restored.decoded_payload(), strict=True)
        == issue
    )
    assert SupervisorGrantRevokeRequest(grant_id=issue.grant_id).grant_id == issue.grant_id

    wrong_type = issue.model_dump(mode="python")
    wrong_type["ttl_ms"] = True
    with pytest.raises(ValidationError, match="ttl_ms"):
        SupervisorGrantIssueRequest.model_validate(wrong_type, strict=True)

    extra = issue.model_dump(mode="python")
    extra["policy_approved"] = True
    with pytest.raises(ValidationError, match="policy_approved"):
        SupervisorGrantIssueRequest.model_validate(extra, strict=True)
