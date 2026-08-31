"""Reference sequencing guard for host-owned caller-driver sends.

This module owns no provider credentials, transport, retry policy, or durable
state.  It validates one canonical local-relay readiness frame against an
already-reserved AAR caller-work ticket, then keeps the host's
``mark_send_started`` call adjacent to one physical send callback.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Generic, Literal, TypeVar

from pydantic import ValidationError

from aar.caller_driver_models import CallerDriverReadyEnvelope
from aar.canonical import canonical_json_bytes
from aar.rlm_workbench_models import CallerWorkTicket

_ResultT = TypeVar("_ResultT")
_MAX_READY_FRAME_BYTES = 4_096

CallerDriverGuardPhase = Literal[
    "prepared",
    "mark_in_progress",
    "mark_unconfirmed",
    "send_started",
    "send_observed",
    "outcome_unknown",
]


class CallerDriverError(RuntimeError):
    """Base class for public caller-driver conformance failures."""


class CallerDriverContractError(CallerDriverError):
    """The local relay did not emit the exact v1 readiness frame."""


class CallerDriverBindingError(CallerDriverError):
    """Readiness does not bind the exact reserved ticket or launch."""


class CallerDriverMarkReceiptError(CallerDriverError):
    """A mark-send callback did not return the exact send-started successor."""


class CallerDriverReplayBlocked(CallerDriverError):
    """The one-shot guard was already consumed or crossed its send boundary."""


class CallerDriverSendOutcomeUnknown(CallerDriverError):
    """The physical send callback failed after the durable send-start mark."""


def parse_caller_driver_ready_line(ready_line: bytes) -> CallerDriverReadyEnvelope:
    """Parse one exact canonical JSON line without echoing rejected input."""

    if type(ready_line) is not bytes:
        raise CallerDriverContractError("ready envelope frame must be exact bytes")
    if not ready_line or len(ready_line) > _MAX_READY_FRAME_BYTES:
        raise CallerDriverContractError("ready envelope frame has an invalid byte length")
    if not ready_line.endswith(b"\n") or ready_line.count(b"\n") != 1:
        raise CallerDriverContractError("ready envelope must be one newline-terminated frame")
    body = ready_line[:-1]
    if not body or b"\r" in body:
        raise CallerDriverContractError("ready envelope uses invalid line framing")
    try:
        envelope = CallerDriverReadyEnvelope.model_validate_json(body, strict=True)
    except (ValidationError, ValueError) as error:
        raise CallerDriverContractError("invalid caller-driver ready envelope") from error
    if canonical_json_bytes(envelope.model_dump(mode="json")) != body:
        raise CallerDriverContractError("ready envelope must use canonical JSON bytes")
    return envelope


def _caller_work_ticket(value: CallerWorkTicket | Mapping[str, Any]) -> CallerWorkTicket:
    candidate: CallerWorkTicket | Mapping[str, Any]
    candidate = value.model_dump(mode="python") if isinstance(value, CallerWorkTicket) else value
    try:
        return CallerWorkTicket.model_validate(candidate, strict=True)
    except (ValidationError, ValueError, TypeError) as error:
        raise CallerDriverMarkReceiptError(
            "mark-send result is not a caller-work ticket"
        ) from error


def _require_ready_binding(
    reserved: CallerWorkTicket,
    ready: CallerDriverReadyEnvelope,
    *,
    expected_launch_nonce: str,
) -> None:
    root = reserved.root
    if root["state"] != "send_reserved":
        raise CallerDriverBindingError("caller-work ticket must be send_reserved")
    claimant = root["claimant"]
    physical = root["physical_attempt"]
    if not isinstance(claimant, dict) or not isinstance(physical, dict):
        raise CallerDriverBindingError("send_reserved ticket must carry claimant and attempt")
    expected = {
        "launch_nonce": expected_launch_nonce,
        "adapter_id": claimant["adapter_id"],
        "adapter_generation": claimant["adapter_generation"],
        "ticket_id": root["ticket_id"],
        "ticket_digest": root["ticket_digest"],
        "request_digest": root["request_digest"],
        "physical_attempt_id": physical["physical_attempt_id"],
    }
    for field_name, expected_value in expected.items():
        if getattr(ready, field_name) != expected_value:
            raise CallerDriverBindingError(
                f"ready envelope {field_name} does not match the reserved ticket"
            )


def _require_send_started_successor(
    reserved: CallerWorkTicket,
    marked: CallerWorkTicket,
) -> None:
    before = reserved.root
    after = marked.root
    if after["state"] != "send_started":
        raise CallerDriverMarkReceiptError("mark-send result must be send_started")
    stable_fields = (
        "operation",
        "owner",
        "suspension_revision",
        "ticket_id",
        "ticket_digest",
        "request",
        "request_digest",
        "deadline_unix_ms",
        "claimant",
    )
    for field_name in stable_fields:
        if after[field_name] != before[field_name]:
            raise CallerDriverMarkReceiptError(f"mark-send result changed bound field {field_name}")
    if after["revision"] != before["revision"] + 1:
        raise CallerDriverMarkReceiptError("mark-send result must advance exactly one revision")
    before_attempt = before["physical_attempt"]
    after_attempt = after["physical_attempt"]
    if not isinstance(before_attempt, dict) or not isinstance(after_attempt, dict):
        raise CallerDriverMarkReceiptError("mark-send result must retain the physical attempt")
    stable_attempt_fields = (
        "physical_attempt_id",
        "provider_or_child_idempotency_key",
    )
    for field_name in stable_attempt_fields:
        if after_attempt[field_name] != before_attempt[field_name]:
            raise CallerDriverMarkReceiptError(
                f"mark-send result changed attempt field {field_name}"
            )
    if after_attempt["sent_request_digest"] != before["request_digest"]:
        raise CallerDriverMarkReceiptError("mark-send result must bind the request digest")
    if after_attempt["send_started_at_unix_ms"] is None:
        raise CallerDriverMarkReceiptError("mark-send result must record send-start time")
    if (
        after_attempt["sent_at_unix_ms"] is not None
        or after_attempt["provider_or_child_request_id"] is not None
    ):
        raise CallerDriverMarkReceiptError("mark-send result cannot claim a provider response")
    if after["settled_receipt_digest"] is not None or after["settled_at_unix_ms"] is not None:
        raise CallerDriverMarkReceiptError("mark-send result cannot be terminal")


class CallerDriverSendGuard(Generic[_ResultT]):
    """One-process guard for one ready/mark/send sequence.

    All fallible local setup, including executable-path resolution, relay
    startup, readiness parsing, client construction, and payload preparation,
    must finish before ``send_once`` is called.  This guard deliberately has no
    restart-replay contract: after ``mark_send_started`` succeeds, a lost
    process or response is reconciled as possibly sent rather than recreated.
    """

    def __init__(
        self,
        *,
        reserved_ticket: CallerWorkTicket,
        ready: CallerDriverReadyEnvelope,
    ) -> None:
        self._reserved_ticket = reserved_ticket
        self._ready = ready
        self._phase: CallerDriverGuardPhase = "prepared"

    @classmethod
    def prepare(
        cls,
        *,
        reserved_ticket: CallerWorkTicket | Mapping[str, Any],
        ready_line: bytes,
        expected_launch_nonce: str,
    ) -> CallerDriverSendGuard[Any]:
        """Validate relay readiness while the durable ticket is still pre-send."""

        try:
            candidate: CallerWorkTicket | Mapping[str, Any]
            candidate = (
                reserved_ticket.model_dump(mode="python")
                if isinstance(reserved_ticket, CallerWorkTicket)
                else reserved_ticket
            )
            reserved = CallerWorkTicket.model_validate(candidate, strict=True)
        except (ValidationError, ValueError, TypeError) as error:
            raise CallerDriverBindingError("reserved caller-work ticket is invalid") from error
        ready = parse_caller_driver_ready_line(ready_line)
        _require_ready_binding(
            reserved,
            ready,
            expected_launch_nonce=expected_launch_nonce,
        )
        return cls(reserved_ticket=reserved, ready=ready)

    @property
    def phase(self) -> CallerDriverGuardPhase:
        return self._phase

    @property
    def ready(self) -> CallerDriverReadyEnvelope:
        return self._ready

    def send_once(
        self,
        *,
        mark_send_started: Callable[
            [CallerWorkTicket, CallerDriverReadyEnvelope],
            CallerWorkTicket | Mapping[str, Any],
        ],
        physical_send: Callable[[CallerDriverReadyEnvelope, CallerWorkTicket], _ResultT],
    ) -> _ResultT:
        """Mark the durable boundary, then invoke exactly one physical send callback."""

        if self._phase != "prepared":
            raise CallerDriverReplayBlocked(
                f"caller-driver send guard cannot run from phase {self._phase}"
            )
        self._phase = "mark_in_progress"
        try:
            marked_value = mark_send_started(self._reserved_ticket, self._ready)
        except Exception:
            self._phase = "mark_unconfirmed"
            raise
        try:
            marked = _caller_work_ticket(marked_value)
            _require_send_started_successor(self._reserved_ticket, marked)
        except CallerDriverMarkReceiptError:
            self._phase = "mark_unconfirmed"
            raise
        self._phase = "send_started"
        try:
            result = physical_send(self._ready, marked)
        except Exception:
            self._phase = "outcome_unknown"
            raise CallerDriverSendOutcomeUnknown(
                "physical request may have been sent; reconcile and do not replay"
            ) from None
        self._phase = "send_observed"
        return result
