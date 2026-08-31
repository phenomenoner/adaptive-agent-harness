from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from aar.caller_driver_models import CallerDriverReadyEnvelope
from aar.canonical import canonical_json_bytes
from aar.integrations.caller_driver import (
    CallerDriverBindingError,
    CallerDriverContractError,
    CallerDriverMarkReceiptError,
    CallerDriverReplayBlocked,
    CallerDriverSendGuard,
    CallerDriverSendOutcomeUnknown,
)
from aar.rlm_workbench_models import CallerWorkTicket

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_FIXTURES = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2" / "fixtures"
RELAY_FIXTURE = ROOT / "tests" / "fixtures" / "caller_driver_ready_relay.py"
SCHEMA_PATH = ROOT / "integration" / "caller-driver" / "aar-caller-driver-ready-v1.schema.json"
LAUNCH_NONCE = "launch-nonce-001"


def _ticket(name: str) -> CallerWorkTicket:
    return CallerWorkTicket.model_validate_json(
        (WORKBENCH_FIXTURES / name).read_bytes(), strict=True
    )


def _relay_line(
    ticket: CallerWorkTicket,
    *,
    mode: str = "v1",
    base_url: str | None = None,
) -> bytes:
    root = ticket.root
    claimant = root["claimant"]
    physical = root["physical_attempt"]
    assert isinstance(claimant, dict)
    assert isinstance(physical, dict)
    command = [
        sys.executable,
        str(RELAY_FIXTURE),
        "--mode",
        mode,
        "--launch-nonce",
        LAUNCH_NONCE,
        "--adapter-id",
        claimant["adapter_id"],
        "--adapter-generation",
        str(claimant["adapter_generation"]),
        "--ticket-id",
        root["ticket_id"],
        "--ticket-digest",
        root["ticket_digest"],
        "--request-digest",
        root["request_digest"],
        "--physical-attempt-id",
        physical["physical_attempt_id"],
        "--base-url",
        base_url or "http://127.0.0.1:43123",
    ]
    environment = dict(os.environ)
    source_root = str(ROOT / "src")
    environment["PYTHONPATH"] = source_root + os.pathsep + environment.get("PYTHONPATH", "")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        timeout=5,
    )
    assert completed.stderr == b""
    return completed.stdout


def test_reference_relay_and_guard_cross_exact_subprocess_boundary() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = _ticket("valid-ticket-send-started.json")
    line = _relay_line(reserved)

    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=line,
        expected_launch_nonce=LAUNCH_NONCE,
    )
    events: list[str] = []

    def mark(ticket: CallerWorkTicket, ready: CallerDriverReadyEnvelope) -> CallerWorkTicket:
        assert ticket is not reserved
        assert ticket.root == reserved.root
        assert ready.base_url == "http://127.0.0.1:43123"
        events.append("mark")
        return started

    def send(ready: CallerDriverReadyEnvelope, ticket: CallerWorkTicket) -> str:
        assert ready.physical_attempt_id == ticket.root["physical_attempt"]["physical_attempt_id"]
        events.append("send")
        return "provider-result"

    assert guard.send_once(mark_send_started=mark, physical_send=send) == "provider-result"
    assert events == ["mark", "send"]
    assert guard.phase == "send_observed"


def test_legacy_ready_host_port_shape_is_rejected_before_mark_or_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    line = _relay_line(reserved, mode="legacy")

    with pytest.raises(CallerDriverContractError, match="ready envelope"):
        CallerDriverSendGuard.prepare(
            reserved_ticket=reserved,
            ready_line=line,
            expected_launch_nonce=LAUNCH_NONCE,
        )


def test_noncanonical_or_multiline_frame_is_rejected() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    canonical = _relay_line(reserved)
    payload = json.loads(canonical)
    noncanonical = json.dumps(payload, indent=2).encode() + b"\n"
    body = canonical[:-1]
    duplicate_key = b'{"event":"ready",' + body[1:] + b"\n"
    float_generation = body.replace(b'"adapter_generation":1', b'"adapter_generation":1.0')

    for frame in (
        canonical.rstrip(b"\n"),
        noncanonical,
        canonical + b"{}\n",
        duplicate_key,
        float_generation + b"\n",
        b"\xef\xbb\xbf" + canonical,
        b'{"value":"\xff"}\n',
        b"[]\n",
        b"{}{}\n",
        body + b"\r\n",
    ):
        with pytest.raises(CallerDriverContractError):
            CallerDriverSendGuard.prepare(
                reserved_ticket=reserved,
                ready_line=frame,
                expected_launch_nonce=LAUNCH_NONCE,
            )


@pytest.mark.parametrize(
    "base_url",
    (
        "https://127.0.0.1:43123",
        "http://example.com:43123",
        "http://user@127.0.0.1:43123",
        "http://127.0.0.1:43123/path",
        "http://127.0.0.1:43123?token=forbidden",
        "http://127.0.0.1",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:0001",
        "http://127.999.999.999:43123",
        "http://127.0.0.01:43123",
        "http://[0:0:0:0:0:0:0:1]:43123",
    ),
)
def test_ready_envelope_rejects_nonlocal_or_ambiguous_base_url(base_url: str) -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    payload = json.loads(_relay_line(reserved))
    payload["base_url"] = base_url
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(payload) is False
    with pytest.raises(subprocess.CalledProcessError):
        _relay_line(reserved, base_url=base_url)


@pytest.mark.parametrize(
    ("field_name", "replacement"),
    (
        ("launch_nonce", "wrong-launch-nonce"),
        ("adapter_id", "wrong-adapter"),
        ("adapter_generation", 2),
        ("ticket_id", "wrong-ticket"),
        ("ticket_digest", "sha256:" + "a" * 64),
        ("request_digest", "sha256:" + "b" * 64),
        ("physical_attempt_id", "wrong-physical-attempt"),
    ),
)
def test_each_ready_binding_mismatch_is_rejected_before_mark_or_send(
    field_name: str,
    replacement: str | int,
) -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    line = _relay_line(reserved)
    payload = json.loads(line)
    payload[field_name] = replacement

    mismatched = canonical_json_bytes(payload) + b"\n"
    with pytest.raises(CallerDriverBindingError, match=field_name):
        CallerDriverSendGuard.prepare(
            reserved_ticket=reserved,
            ready_line=mismatched,
            expected_launch_nonce=LAUNCH_NONCE,
        )


@pytest.mark.parametrize(
    "base_url",
    (
        "http://localhost:1",
        "http://127.255.255.254:65535/",
        "http://[::1]:43123",
    ),
)
def test_ready_envelope_accepts_explicit_loopback_variants(base_url: str) -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    ready_line = _relay_line(reserved, base_url=base_url)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert Draft202012Validator(schema).is_valid(json.loads(ready_line)) is True
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=ready_line,
        expected_launch_nonce=LAUNCH_NONCE,
    )
    assert guard.ready.base_url == base_url


def test_mark_failure_never_calls_physical_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    events: list[str] = []

    def mark(_ticket: CallerWorkTicket, _ready: CallerDriverReadyEnvelope) -> Any:
        events.append("mark")
        raise RuntimeError("certain structured rejection")

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        events.append("send")

    with pytest.raises(RuntimeError, match="structured rejection"):
        guard.send_once(mark_send_started=mark, physical_send=send)
    assert events == ["mark"]
    assert guard.phase == "mark_unconfirmed"


def test_invalid_mark_receipt_never_calls_physical_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    calls = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(CallerDriverMarkReceiptError, match="send_started"):
        guard.send_once(mark_send_started=lambda _ticket, _ready: reserved, physical_send=send)
    assert calls == 0
    assert guard.phase == "mark_unconfirmed"


def test_mark_successor_accepts_capabilities_declared_at_send_start() -> None:
    reserved_payload = copy.deepcopy(_ticket("valid-ticket-send-reserved.json").root)
    reserved_payload["physical_attempt"]["lookup_supported"] = False
    reserved_payload["physical_attempt"]["cancel_supported"] = False
    reserved = CallerWorkTicket.model_validate(reserved_payload, strict=True)
    started = copy.deepcopy(_ticket("valid-ticket-send-started.json").root)
    started["physical_attempt"]["lookup_supported"] = True
    started["physical_attempt"]["cancel_supported"] = True
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    sends = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> str:
        nonlocal sends
        sends += 1
        return "ok"

    assert (
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
        == "ok"
    )
    assert sends == 1


def test_mark_successor_rejects_ticket_digest_drift_before_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = copy.deepcopy(_ticket("valid-ticket-send-started.json").root)
    started["ticket_digest"] = "sha256:" + "f" * 64
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    sends = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        nonlocal sends
        sends += 1

    with pytest.raises(CallerDriverMarkReceiptError, match="ticket_digest"):
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
    assert sends == 0


def test_mutated_reserved_ticket_instance_is_revalidated() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    ready_line = _relay_line(reserved)
    reserved.root["state"] = "not-a-ticket-state"

    with pytest.raises(CallerDriverBindingError, match="invalid"):
        CallerDriverSendGuard.prepare(
            reserved_ticket=reserved,
            ready_line=ready_line,
            expected_launch_nonce=LAUNCH_NONCE,
        )


def test_mutated_mark_ticket_instance_is_revalidated_before_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = _ticket("valid-ticket-send-started.json")
    started.root["state"] = "not-a-ticket-state"
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    sends = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        nonlocal sends
        sends += 1

    with pytest.raises(CallerDriverMarkReceiptError, match="caller-work ticket"):
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
    assert sends == 0


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        ("revision", "revision"),
        ("ticket_id", "ticket_id"),
        ("physical_attempt_id", "physical_attempt_id"),
    ),
)
def test_each_mark_successor_binding_failure_blocks_physical_send(
    mutation: str,
    message: str,
) -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = copy.deepcopy(_ticket("valid-ticket-send-started.json").root)
    if mutation == "revision":
        started["revision"] += 1
    elif mutation == "ticket_id":
        started["ticket_id"] = "wrong-ticket"
    else:
        started["physical_attempt"]["physical_attempt_id"] = "wrong-attempt"
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    calls = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        nonlocal calls
        calls += 1

    with pytest.raises(CallerDriverMarkReceiptError, match=message):
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
    assert calls == 0


def test_send_failure_is_unknown_and_guard_blocks_replay() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = _ticket("valid-ticket-send-started.json")
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    sends = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> None:
        nonlocal sends
        sends += 1
        raise TimeoutError("response not observed")

    with pytest.raises(CallerDriverSendOutcomeUnknown, match="may have been sent"):
        guard.send_once(mark_send_started=lambda _ticket, _ready: started, physical_send=send)
    assert sends == 1
    assert guard.phase == "outcome_unknown"

    with pytest.raises(CallerDriverReplayBlocked):
        guard.send_once(mark_send_started=lambda _ticket, _ready: started, physical_send=send)
    assert sends == 1


def test_successful_guard_blocks_second_send() -> None:
    reserved = _ticket("valid-ticket-send-reserved.json")
    started = _ticket("valid-ticket-send-started.json")
    guard = CallerDriverSendGuard.prepare(
        reserved_ticket=reserved,
        ready_line=_relay_line(reserved),
        expected_launch_nonce=LAUNCH_NONCE,
    )
    sends = 0

    def send(_ready: CallerDriverReadyEnvelope, _ticket: CallerWorkTicket) -> str:
        nonlocal sends
        sends += 1
        return "ok"

    assert (
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
        == "ok"
    )
    with pytest.raises(CallerDriverReplayBlocked):
        guard.send_once(
            mark_send_started=lambda _ticket, _ready: started,
            physical_send=send,
        )
    assert sends == 1


def test_checked_in_ready_schema_matches_strict_model() -> None:
    checked = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    projected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "aar.caller-driver-ready.v1",
        **CallerDriverReadyEnvelope.model_json_schema(),
    }
    assert checked == projected
    Draft202012Validator.check_schema(checked)
    assert checked["additionalProperties"] is False
    assert set(checked["required"]) == set(checked["properties"])
    assert checked["properties"]["base_url"]["pattern"].startswith("^http://")
