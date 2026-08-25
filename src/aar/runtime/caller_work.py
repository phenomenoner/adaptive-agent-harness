"""Durable caller-delegated broker work lifecycle authority.

The repository projects the reviewed ``aar.caller-work.v1`` contracts directly onto
registry schema v6.  A successful send-start mark is a may-have-sent boundary: resume
may look up or reconcile that exact physical attempt, but it never dispatches again.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from aar.broker_models import ModelResponse, ModelRouteBinding
from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import (
    CallerWorkCancelBeforeSendInput,
    CallerWorkClaimInput,
    CallerWorkCommitInput,
    CallerWorkMarkSendStartedInput,
    CallerWorkReconcileInput,
    CallerWorkTicket,
    CandidateReceipt,
)
from aar.runtime.model_broker import ModelBrokerError, ModelExecutionJournal
from aar.runtime.sqlite_repository import SQLiteConnectionFactory
from aar.schemas import OperationRef


class CallerWorkError(RuntimeError):
    """Base class for durable caller-work lifecycle failures."""


class CallerWorkConflict(CallerWorkError):
    """The mutation does not bind the currently durable ticket authority."""


class CallerWorkIdempotencyConflict(CallerWorkConflict):
    """An operation-scoped command key was reused with different command bytes."""


class StaleTicketRevision(CallerWorkConflict):
    """The mutation lost the ticket revision compare-and-swap."""


class InvalidTicketTransition(CallerWorkConflict):
    """The requested state transition is not allowed from the durable state."""


class ExpiredCallerClaim(CallerWorkConflict):
    """The caller claim is no longer live at the exact expiry boundary."""


class CallerWorkLookupAdapter(Protocol):
    def lookup(self, *, idempotency_key: str) -> Mapping[str, Any] | None: ...


@dataclass(frozen=True)
class CandidateAppendResult:
    receipt: CandidateReceipt
    replayed: bool


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        _json_ready(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _canonical_sha256(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_reconcile_fence(
    *,
    ticket_id: str,
    expected_revision: int,
    physical_attempt_id: str,
    candidate_receipt_digest: str | None,
    reconciler_id: str,
    reconciler_generation: int,
    reconciliation_action: str,
) -> str:
    """Bind one reconciler identity to one exact ticket authority epoch."""

    return _canonical_sha256(
        {
            "kind": "caller_work_reconcile",
            "ticket_id": ticket_id,
            "expected_revision": expected_revision,
            "physical_attempt_id": physical_attempt_id,
            "candidate_receipt_digest": candidate_receipt_digest,
            "reconciler_id": reconciler_id,
            "reconciler_generation": reconciler_generation,
            "reconciliation_action": reconciliation_action,
        }
    )


def _candidate_receipt_digest(receipt: CandidateReceipt) -> str:
    payload = dict(receipt.root)
    payload.pop("receipt_digest")
    return _canonical_sha256(payload)


def _identifier(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class CallerWorkRepository:
    """SQLite authority for one registry's caller-work tickets and receipts."""

    def __init__(
        self,
        database_path: Path,
        *,
        now_ms: Callable[[], int],
        model_executions: ModelExecutionJournal | None = None,
    ) -> None:
        self._factory = SQLiteConnectionFactory(database_path)
        self._now_ms = now_ms
        self._model_executions = model_executions
        self._settlement_handler: Callable[[CallerWorkTicket], None] | None = None

    @property
    def database_path(self) -> Path:
        return self._factory.database_path

    def close(self) -> None:
        self._settlement_handler = None
        self._factory.close()

    def bind_settlement_handler(self, handler: Callable[[CallerWorkTicket], None] | None) -> None:
        if (
            self._settlement_handler is not None
            and handler is not None
            and self._settlement_handler is not handler
        ):
            raise CallerWorkConflict("caller settlement handler is already bound")
        self._settlement_handler = handler

    def _notify_settlement(self, ticket: CallerWorkTicket) -> None:
        handler = self._settlement_handler
        if handler is not None:
            handler(ticket)

    def _record_model_response(
        self,
        connection: Any,
        row: Any,
        observation: Mapping[str, Any],
        model_response_value: Mapping[str, Any],
    ) -> None:
        if self._model_executions is None:
            raise CallerWorkConflict("model execution journal is unavailable")
        response = ModelResponse.model_validate_json(
            json.dumps(dict(model_response_value), sort_keys=True, separators=(",", ":")),
            strict=True,
        )
        request_document = json.loads(str(row["request_json"]))
        binding = ModelRouteBinding.model_validate(
            request_document["route_binding"], strict=True
        )
        self._require_response_matches_observation(response, observation)
        try:
            self._model_executions.record_caller_result_in_transaction(
                connection,
                operation=OperationRef(value=str(row["operation_id"])),
                ticket_id=str(row["ticket_id"]),
                physical_attempt_id=str(row["physical_attempt_id"]),
                idempotency_key=str(row["external_idempotency_key"]),
                request_digest=str(row["request_digest"]),
                request_document=request_document,
                binding=binding,
                response=response,
            )
        except ModelBrokerError as error:
            raise CallerWorkConflict(str(error)) from error

    @staticmethod
    def _require_response_matches_observation(
        response: ModelResponse,
        observation: Mapping[str, Any],
    ) -> None:
        if (
            response.output_text != observation["output_text"]
            or "sha256:"
            + hashlib.sha256(response.output_text.encode("utf-8")).hexdigest()
            != observation["output_digest"]
            or response.route_receipt.receipt_digest
            != observation["route_receipt_digest"]
            or canonical_sha256(response.usage) != observation["usage_receipt_digest"]
            or canonical_sha256(response) != observation["host_receipt_digest"]
        ):
            raise CallerWorkConflict("model response evidence does not match observation")

    def _require_model_settlement_authority(
        self,
        connection: Any,
        row: Any,
        observation: Mapping[str, Any],
    ) -> None:
        if self._model_executions is None:
            raise CallerWorkConflict("model execution journal is unavailable")
        request_document = json.loads(str(row["request_json"]))
        binding = ModelRouteBinding.model_validate(
            request_document["route_binding"], strict=True
        )
        try:
            response = self._model_executions.require_caller_result_in_transaction(
                connection,
                operation=OperationRef(value=str(row["operation_id"])),
                ticket_id=str(row["ticket_id"]),
                physical_attempt_id=str(row["physical_attempt_id"]),
                idempotency_key=str(row["external_idempotency_key"]),
                request_digest=str(row["request_digest"]),
                request_document=request_document,
                binding=binding,
            )
        except ModelBrokerError as error:
            raise CallerWorkConflict(str(error)) from error
        self._require_response_matches_observation(response, observation)

    def create_ticket(self, ticket: CallerWorkTicket) -> CallerWorkTicket:
        with self._factory.transaction(write=True) as connection:
            return self.create_ticket_in_transaction(connection, ticket)

    def create_ticket_in_transaction(
        self, connection: Any, ticket: CallerWorkTicket
    ) -> CallerWorkTicket:
        """Create a ticket inside the coordinator-owned suspension transaction."""
        document = dict(ticket.root)
        request = document["request"]
        operation = document["operation"]
        now = self._now_ms()
        existing = connection.execute(
            "SELECT * FROM caller_work_tickets WHERE ticket_id = ?",
            (document["ticket_id"],),
        ).fetchone()
        if existing is not None:
            projected = self._ticket_from_row(existing)
            if projected.root != document:
                raise CallerWorkConflict("ticket id already binds different ticket bytes")
            return projected
        connection.execute(
            """
            INSERT INTO caller_work_tickets(
                ticket_id, operation_id, suspension_revision, revision,
                ticket_digest, request_json, request_digest, method, contract_id,
                logical_owner_json, state, deadline_unix_ms,
                claimant_principal_id, claimant_session_id, adapter_id,
                adapter_generation, claim_id, claim_fence,
                claim_expires_at_unix_ms, physical_attempt_id,
                external_idempotency_key, lookup_supported, cancel_supported,
                send_started_at_unix_ms, sent_request_digest, sent_at_unix_ms,
                provider_or_child_request_id, settled_receipt_digest,
                settled_at_unix_ms, created_at_unix_ms, updated_at_unix_ms
            ) VALUES(
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL,
                NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?, ?
            )
            """,
            (
                document["ticket_id"],
                operation["value"],
                document["suspension_revision"],
                document["revision"],
                document["ticket_digest"],
                _canonical_json(request),
                document["request_digest"],
                request["method"],
                request["contract_id"],
                _canonical_json(document["owner"]),
                document["state"],
                document["deadline_unix_ms"],
                document["settled_receipt_digest"],
                document["settled_at_unix_ms"],
                now,
                now,
            ),
        )
        return self._get(connection, str(document["ticket_id"]))

    def get(self, ticket_id: str) -> CallerWorkTicket:
        with self._factory.transaction(write=False) as connection:
            return self._get(connection, ticket_id)

    def claim(self, value: Mapping[str, Any]) -> CallerWorkTicket:
        command = dict(CallerWorkClaimInput.model_validate(dict(value), strict=True).root)
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(command["ticket_id"]))
            replay = self._replay_command(connection, row, command_kind="claim", command=command)
            if replay is not None:
                return replay
            row = self._checked_row(connection, command)
            if row["state"] != "pending":
                raise InvalidTicketTransition(f"cannot claim ticket from {row['state']}")
            now = self._now_ms()
            if now >= int(row["deadline_unix_ms"]):
                raise InvalidTicketTransition("cannot claim caller work at or beyond deadline")
            context = command["context"]
            claim_id = _identifier("claim")
            attempt_id = _identifier("attempt")
            external_key = "caller-" + str(row["ticket_digest"]).removeprefix("sha256:")[:48]
            claim_fence = _canonical_sha256(
                {
                    "ticket_id": row["ticket_id"],
                    "revision": int(row["revision"]) + 1,
                    "claim_id": claim_id,
                    "physical_attempt_id": attempt_id,
                    "adapter_id": command["adapter_id"],
                    "adapter_generation": command["adapter_generation"],
                }
            )
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'send_reserved',
                    claimant_principal_id = ?, claimant_session_id = ?,
                    adapter_id = ?, adapter_generation = ?, claim_id = ?,
                    claim_fence = ?, claim_expires_at_unix_ms = ?,
                    physical_attempt_id = ?, external_idempotency_key = ?,
                    lookup_supported = 0, cancel_supported = 0,
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (
                    context["principal_id"],
                    context["session_id"],
                    command["adapter_id"],
                    command["adapter_generation"],
                    claim_id,
                    claim_fence,
                    now + int(command["claim_lease_ms"]),
                    attempt_id,
                    external_key,
                    now,
                    row["ticket_id"],
                    row["revision"],
                ),
            )
            result = self._get(connection, str(row["ticket_id"]))
            self._record_command(
                connection,
                row,
                command_kind="claim",
                command=command,
                result=result,
            )
            return result

    def reclaim_expired(
        self,
        *,
        ticket_id: str,
        expected_revision: int,
        expected_claim_id: str,
        expected_claim_fence: str,
    ) -> CallerWorkTicket:
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, ticket_id)
            self._require_revision(row, expected_revision)
            if row["state"] != "send_reserved":
                raise InvalidTicketTransition(f"cannot reclaim ticket from {row['state']}")
            if row["claim_id"] != expected_claim_id or row["claim_fence"] != expected_claim_fence:
                raise CallerWorkConflict("expired claim identity mismatch")
            expires = int(row["claim_expires_at_unix_ms"])
            if self._now_ms() < expires:
                raise InvalidTicketTransition("caller claim has not expired")
            now = self._now_ms()
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'pending',
                    claimant_principal_id = NULL, claimant_session_id = NULL,
                    adapter_id = NULL, adapter_generation = NULL,
                    claim_id = NULL, claim_fence = NULL,
                    claim_expires_at_unix_ms = NULL, physical_attempt_id = NULL,
                    external_idempotency_key = NULL, lookup_supported = NULL,
                    cancel_supported = NULL, send_started_at_unix_ms = NULL,
                    sent_request_digest = NULL, sent_at_unix_ms = NULL,
                    provider_or_child_request_id = NULL,
                    settled_receipt_digest = NULL, settled_at_unix_ms = NULL,
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (now, ticket_id, expected_revision),
            )
            return self._get(connection, ticket_id)

    def mark_send_started(self, value: Mapping[str, Any]) -> CallerWorkTicket:
        command = dict(CallerWorkMarkSendStartedInput.model_validate(dict(value), strict=True).root)
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(command["ticket_id"]))
            replay = self._replay_command(
                connection,
                row,
                command_kind="mark_send_started",
                command=command,
            )
            if replay is not None:
                return replay
            row = self._checked_row(connection, command)
            if row["state"] != "send_reserved":
                raise InvalidTicketTransition(f"cannot mark send from {row['state']}")
            self._require_claim(row, command)
            now = self._now_ms()
            if now >= int(row["deadline_unix_ms"]):
                raise ExpiredCallerClaim("caller work deadline expired before send-start CAS")
            expires = int(row["claim_expires_at_unix_ms"])
            if now >= expires:
                raise ExpiredCallerClaim("caller claim expired before send-start CAS")
            if command["expected_claim_expires_at_unix_ms"] != expires:
                raise CallerWorkConflict("claim expiry boundary mismatch")
            if command["provider_or_child_idempotency_key"] != row["external_idempotency_key"]:
                raise CallerWorkConflict("provider idempotency key mismatch")
            if command["sent_request_digest"] != row["request_digest"]:
                raise CallerWorkConflict("sent request digest mismatch")
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'send_started',
                    lookup_supported = ?, cancel_supported = ?,
                    send_started_at_unix_ms = ?, sent_request_digest = ?,
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (
                    int(bool(command["lookup_supported"])),
                    int(bool(command["cancel_supported"])),
                    now,
                    command["sent_request_digest"],
                    now,
                    row["ticket_id"],
                    row["revision"],
                ),
            )
            result = self._get(connection, str(row["ticket_id"]))
            self._record_command(
                connection,
                row,
                command_kind="mark_send_started",
                command=command,
                result=result,
            )
            return result

    def cancel_before_send(self, value: Mapping[str, Any]) -> CallerWorkTicket:
        command = dict(
            CallerWorkCancelBeforeSendInput.model_validate(dict(value), strict=True).root
        )
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(command["ticket_id"]))
            replay = self._replay_command(
                connection,
                row,
                command_kind="cancel_before_send",
                command=command,
            )
            if replay is not None:
                return replay
            row = self._checked_row(connection, command)
            expected_state = command["expected_pre_send_state"]
            if row["state"] != expected_state or expected_state not in {"pending", "send_reserved"}:
                raise InvalidTicketTransition(f"cannot cancel before send from {row['state']}")
            if expected_state == "send_reserved":
                self._require_claim(row, command)
                if command["expected_claim_expires_at_unix_ms"] != row["claim_expires_at_unix_ms"]:
                    raise CallerWorkConflict("claim expiry boundary mismatch")
            now = self._now_ms()
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'cancelled_before_send',
                    settled_receipt_digest = ?, settled_at_unix_ms = ?,
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (
                    command["settled_receipt_digest"],
                    command["settled_at_unix_ms"],
                    now,
                    row["ticket_id"],
                    row["revision"],
                ),
            )
            self._project_terminal(
                connection,
                row,
                state="cancelled_before_send",
                settlement_digest=str(command["settled_receipt_digest"]),
                projected_at_unix_ms=int(command["settled_at_unix_ms"]),
            )
            result = self._get(connection, str(row["ticket_id"]))
            self._record_command(
                connection,
                row,
                command_kind="cancel_before_send",
                command=command,
                result=result,
            )
            return result

    def request_cancel(
        self,
        *,
        ticket_id: str,
        expected_revision: int,
        reason: str,
        idempotency_key: str,
    ) -> CallerWorkTicket:
        del reason, idempotency_key
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, ticket_id)
            self._require_revision(row, expected_revision)
            if row["state"] not in {"send_started", "outcome_unknown"}:
                raise InvalidTicketTransition(f"cannot request cancellation from {row['state']}")
            now = self._now_ms()
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'cancel_requested',
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (now, ticket_id, expected_revision),
            )
            return self._get(connection, ticket_id)

    def mark_outcome_unknown(self, *, ticket_id: str, expected_revision: int) -> CallerWorkTicket:
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, ticket_id)
            self._require_revision(row, expected_revision)
            if row["state"] == "outcome_unknown":
                return self._ticket_from_row(row)
            if row["state"] != "send_started":
                raise InvalidTicketTransition(f"cannot mark outcome unknown from {row['state']}")
            now = self._now_ms()
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'outcome_unknown',
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (now, ticket_id, expected_revision),
            )
            return self._get(connection, ticket_id)

    def commit(self, value: Mapping[str, Any]) -> CallerWorkTicket:
        command = dict(CallerWorkCommitInput.model_validate(dict(value), strict=True).root)
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(command["ticket_id"]))
            observation = command["observation"]
            model_response_value = command["model_response"]
            if observation["kind"] == "model" and observation["outcome"] == "succeeded":
                self._record_model_response(
                    connection,
                    row,
                    observation,
                    model_response_value,
                )
            replay = self._replay_command(connection, row, command_kind="commit", command=command)
            if replay is not None:
                result = replay
            else:
                row = self._checked_row(connection, command)
                if row["state"] not in {"send_started", "cancel_requested", "outcome_unknown"}:
                    raise InvalidTicketTransition(f"cannot commit caller work from {row['state']}")
                self._require_claim(row, command)
                if command["sent_request_digest"] != row["sent_request_digest"]:
                    raise CallerWorkConflict("committed request digest mismatch")
                receipt = self._candidate_from_commit(row, command)
                self._insert_candidate(connection, receipt, source_kind="claimant_callback")
                result = self._apply_candidate_settlement(connection, row, receipt)
                self._record_command(
                    connection,
                    row,
                    command_kind="commit",
                    command=command,
                    result=result,
                )
        self._notify_settlement(result)
        return result

    def reconcile(self, value: Mapping[str, Any]) -> CallerWorkTicket:
        model = CallerWorkReconcileInput.model_validate(dict(value), strict=True)
        command = dict(model.root)
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(command["ticket_id"]))
            replay = self._replay_command(
                connection, row, command_kind="reconcile", command=command
            )
            if replay is not None:
                return replay
            row = self._checked_row(connection, command)
            if row["physical_attempt_id"] != command["physical_attempt_id"]:
                raise CallerWorkConflict("reconciliation physical attempt mismatch")
            self._require_reconciler(row, command)

            action = str(command["reconciliation_action"])
            if action == "retry_if_certain_no_send":
                if row["state"] != "send_reserved" or row["send_started_at_unix_ms"] is not None:
                    raise InvalidTicketTransition(
                        "retry_if_certain_no_send requires an unstarted send reservation"
                    )
                now = self._now_ms()
                connection.execute(
                    """
                    UPDATE caller_work_tickets
                    SET revision = revision + 1, state = 'pending',
                        claimant_principal_id = NULL, claimant_session_id = NULL,
                        adapter_id = NULL, adapter_generation = NULL,
                        claim_id = NULL, claim_fence = NULL,
                        claim_expires_at_unix_ms = NULL, physical_attempt_id = NULL,
                        external_idempotency_key = NULL, lookup_supported = NULL,
                        cancel_supported = NULL, updated_at_unix_ms = ?
                    WHERE ticket_id = ? AND revision = ?
                    """,
                    (now, row["ticket_id"], row["revision"]),
                )
                return self._complete_command(
                    connection,
                    row,
                    command_kind="reconcile",
                    command=command,
                    result=self._get(connection, str(row["ticket_id"])),
                )

            if action == "lookup":
                if row["state"] not in {
                    "send_started",
                    "outcome_unknown",
                    "cancel_requested",
                }:
                    raise InvalidTicketTransition(
                        "lookup reconciliation requires a may-have-sent ticket"
                    )
                return self._complete_command(
                    connection,
                    row,
                    command_kind="reconcile",
                    command=command,
                    result=self._get(connection, str(row["ticket_id"])),
                )

            if row["state"] not in {
                "send_started",
                "outcome_unknown",
                "cancel_requested",
            }:
                raise InvalidTicketTransition(f"cannot {action} reconciliation from {row['state']}")
            receipt_digest = command["candidate_receipt_digest"]
            receipt_row = connection.execute(
                """
                SELECT receipt_json FROM caller_work_candidate_receipts
                WHERE ticket_id = ? AND physical_attempt_id = ? AND receipt_digest = ?
                """,
                (row["ticket_id"], row["physical_attempt_id"], receipt_digest),
            ).fetchone()
            if receipt_row is None:
                raise CallerWorkConflict("candidate receipt is absent")
            if action == "settle":
                receipt = CandidateReceipt.model_validate_json(
                    str(receipt_row["receipt_json"]), strict=True
                )
                return self._complete_command(
                    connection,
                    row,
                    command_kind="reconcile",
                    command=command,
                    result=self._apply_candidate_settlement(connection, row, receipt),
                )
            if action != "quarantine":
                raise CallerWorkConflict(f"unsupported reconciliation action {action!r}")

            now = self._now_ms()
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'quarantined',
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (now, row["ticket_id"], row["revision"]),
            )
            self._project_terminal(
                connection,
                row,
                state="quarantined",
                settlement_digest=None,
                projected_at_unix_ms=now,
            )
            return self._complete_command(
                connection,
                row,
                command_kind="reconcile",
                command=command,
                result=self._get(connection, str(row["ticket_id"])),
            )

    def append_candidate_receipt(
        self,
        value: Mapping[str, Any],
        *,
        source_kind: str = "claimant_callback",
    ) -> CandidateAppendResult:
        receipt = CandidateReceipt.model_validate(dict(value), strict=True)
        payload = dict(receipt.root)
        digest = payload.pop("receipt_digest")
        if digest != _canonical_sha256(payload):
            raise CallerWorkConflict("candidate receipt digest mismatch")
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(receipt.ticket_id))
            if receipt.ticket_digest != row["ticket_digest"]:
                raise CallerWorkConflict("candidate ticket digest mismatch")
            if receipt.physical_attempt_id != row["physical_attempt_id"]:
                raise CallerWorkConflict("candidate physical attempt mismatch")
            if row["send_started_at_unix_ms"] is None:
                raise CallerWorkConflict("candidate receipt has no durable send-start mark")
            if receipt.sent_request_digest != row["request_digest"]:
                raise CallerWorkConflict("candidate request digest mismatch")
            inserted = self._insert_candidate(connection, receipt, source_kind=source_kind)
            return CandidateAppendResult(receipt=receipt, replayed=not inserted)

    def append_model_candidate_receipt(
        self,
        value: Mapping[str, Any],
        model_response: Mapping[str, Any],
        *,
        source_kind: str = "claimant_callback",
    ) -> CandidateAppendResult:
        """Seal typed model evidence and its candidate receipt in one transaction."""

        receipt = CandidateReceipt.model_validate(dict(value), strict=True)
        payload = dict(receipt.root)
        digest = payload.pop("receipt_digest")
        if digest != _canonical_sha256(payload):
            raise CallerWorkConflict("candidate receipt digest mismatch")
        observation = receipt.root["observation"]
        if observation["kind"] != "model" or observation["outcome"] != "succeeded":
            raise CallerWorkConflict("sealed model callback requires succeeded model evidence")
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, str(receipt.ticket_id))
            if receipt.ticket_digest != row["ticket_digest"]:
                raise CallerWorkConflict("candidate ticket digest mismatch")
            if receipt.physical_attempt_id != row["physical_attempt_id"]:
                raise CallerWorkConflict("candidate physical attempt mismatch")
            if row["send_started_at_unix_ms"] is None:
                raise CallerWorkConflict("candidate receipt has no durable send-start mark")
            if receipt.sent_request_digest != row["request_digest"]:
                raise CallerWorkConflict("candidate request digest mismatch")
            self._record_model_response(
                connection,
                row,
                observation,
                model_response,
            )
            inserted = self._insert_candidate(connection, receipt, source_kind=source_kind)
            return CandidateAppendResult(receipt=receipt, replayed=not inserted)

    def candidate_receipts(self, ticket_id: str) -> tuple[CandidateReceipt, ...]:
        with self._factory.transaction(write=False) as connection:
            rows = connection.execute(
                """
                SELECT receipt_json FROM caller_work_candidate_receipts
                WHERE ticket_id = ? ORDER BY observed_at_unix_ms, receipt_digest
                """,
                (ticket_id,),
            ).fetchall()
            return tuple(
                CandidateReceipt.model_validate_json(str(row["receipt_json"]), strict=True)
                for row in rows
            )

    def settle_candidate(
        self,
        *,
        ticket_id: str,
        expected_revision: int,
        candidate_receipt_digest: str,
        reconciler_id: str,
        reconciler_generation: int,
        reconcile_fence: str,
        idempotency_key: str,
    ) -> CallerWorkTicket:
        command = {
            "ticket_id": ticket_id,
            "expected_revision": expected_revision,
            "candidate_receipt_digest": candidate_receipt_digest,
            "reconciler_id": reconciler_id,
            "reconciler_generation": reconciler_generation,
            "reconcile_fence": reconcile_fence,
            "reconciliation_action": "settle",
            "idempotency_key": idempotency_key,
        }
        with self._factory.transaction(write=True) as connection:
            row = self._row(connection, ticket_id)
            replay = self._replay_command(
                connection,
                row,
                command_kind="reconcile",
                command=command,
            )
            if replay is not None:
                result = replay
            else:
                self._require_revision(row, expected_revision)
                if row["state"] not in {"send_started", "outcome_unknown", "cancel_requested"}:
                    raise InvalidTicketTransition(f"cannot reconcile ticket from {row['state']}")
                physical_attempt_id = str(row["physical_attempt_id"])
                self._require_internal_reconciler(row, command)
                self._require_reconcile_fence(
                    command,
                    physical_attempt_id=physical_attempt_id,
                )
                receipt_row = connection.execute(
                    """
                    SELECT receipt_json FROM caller_work_candidate_receipts
                    WHERE ticket_id = ? AND physical_attempt_id = ? AND receipt_digest = ?
                    """,
                    (ticket_id, physical_attempt_id, candidate_receipt_digest),
                ).fetchone()
                if receipt_row is None:
                    raise CallerWorkConflict("candidate receipt is not bound to current attempt")
                receipt = CandidateReceipt.model_validate_json(
                    str(receipt_row["receipt_json"]), strict=True
                )
                result = self._apply_candidate_settlement(connection, row, receipt)
                self._record_command(
                    connection,
                    row,
                    command_kind="reconcile",
                    command=command,
                    result=result,
                )
        self._notify_settlement(result)
        return result

    def send_started_count(self, ticket_id: str) -> int:
        with self._factory.transaction(write=False) as connection:
            row = self._row(connection, ticket_id)
            return int(row["send_started_at_unix_ms"] is not None)

    def _candidate_from_commit(self, row: Any, command: Mapping[str, Any]) -> CandidateReceipt:
        payload: dict[str, Any] = {
            "schema_version": "aar.caller-work-candidate-receipt.v1",
            "ticket_id": row["ticket_id"],
            "ticket_digest": row["ticket_digest"],
            "physical_attempt_id": row["physical_attempt_id"],
            "sent_request_digest": command["sent_request_digest"],
            "sent_at_unix_ms": command["sent_at_unix_ms"],
            "provider_or_child_request_id": command["provider_or_child_request_id"],
            "observation": command["observation"],
            "callback_principal_id": row["claimant_principal_id"],
            "callback_session_id": row["claimant_session_id"],
            "callback_adapter_id": row["adapter_id"],
            "callback_adapter_generation": row["adapter_generation"],
            "observed_at_unix_ms": self._now_ms(),
            "signature_digest": _canonical_sha256(
                {
                    "kind": "claimant_commit",
                    "ticket_id": row["ticket_id"],
                    "physical_attempt_id": row["physical_attempt_id"],
                    "provider_or_child_request_id": command["provider_or_child_request_id"],
                    "observation": command["observation"],
                }
            ),
        }
        return CandidateReceipt.model_validate(
            {**payload, "receipt_digest": _canonical_sha256(payload)}, strict=True
        )

    @staticmethod
    def _insert_candidate(connection: Any, receipt: CandidateReceipt, *, source_kind: str) -> bool:
        if receipt.receipt_digest != _candidate_receipt_digest(receipt):
            raise CallerWorkConflict("candidate receipt digest does not bind its canonical payload")
        cursor = connection.execute(
            """
            INSERT OR IGNORE INTO caller_work_candidate_receipts(
                ticket_id, physical_attempt_id, receipt_digest, receipt_json,
                source_kind, observed_at_unix_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                receipt.ticket_id,
                receipt.physical_attempt_id,
                receipt.receipt_digest,
                _canonical_json(receipt.root),
                source_kind,
                receipt.observed_at_unix_ms,
            ),
        )
        return cursor.rowcount == 1

    def _apply_candidate_settlement(
        self,
        connection: Any,
        row: Any,
        selected: CandidateReceipt,
    ) -> CallerWorkTicket:
        receipt_rows = connection.execute(
            """
            SELECT receipt_json FROM caller_work_candidate_receipts
            WHERE ticket_id = ? AND physical_attempt_id = ?
            ORDER BY receipt_digest
            """,
            (row["ticket_id"], row["physical_attempt_id"]),
        ).fetchall()
        receipt_digests = {
            _candidate_receipt_digest(
                CandidateReceipt.model_validate_json(str(item["receipt_json"]), strict=True)
            )
            for item in receipt_rows
        }
        now = self._now_ms()
        if len(receipt_digests) > 1:
            connection.execute(
                """
                UPDATE caller_work_tickets
                SET revision = revision + 1, state = 'quarantined',
                    updated_at_unix_ms = ?
                WHERE ticket_id = ? AND revision = ?
                """,
                (now, row["ticket_id"], row["revision"]),
            )
            self._project_terminal(
                connection,
                row,
                state="quarantined",
                settlement_digest=None,
                projected_at_unix_ms=now,
            )
            return self._get(connection, str(row["ticket_id"]))

        outcome = str(selected.observation["outcome"])
        if selected.observation["kind"] == "model" and outcome == "succeeded":
            self._require_model_settlement_authority(
                connection,
                row,
                selected.observation,
            )
        if outcome == "succeeded":
            state = "settled_success"
        elif outcome in {"cancelled", "canceled"}:
            state = "cancelled_certain"
        else:
            state = "settled_failure"
        connection.execute(
            """
            UPDATE caller_work_tickets
            SET revision = revision + 1, state = ?, sent_at_unix_ms = ?,
                provider_or_child_request_id = ?, settled_receipt_digest = ?,
                settled_at_unix_ms = ?, updated_at_unix_ms = ?
            WHERE ticket_id = ? AND revision = ?
            """,
            (
                state,
                selected.sent_at_unix_ms,
                selected.provider_or_child_request_id,
                selected.receipt_digest,
                now,
                now,
                row["ticket_id"],
                row["revision"],
            ),
        )
        self._project_terminal(
            connection,
            row,
            state=state,
            settlement_digest=str(selected.receipt_digest),
            projected_at_unix_ms=now,
        )
        return self._get(connection, str(row["ticket_id"]))

    @staticmethod
    def _project_terminal(
        connection: Any,
        row: Any,
        *,
        state: str,
        settlement_digest: str | None,
        projected_at_unix_ms: int,
    ) -> None:
        suspension_state = {
            "settled_success": "settled",
            "settled_failure": "settled",
            "cancelled_before_send": "cancelled",
            "cancelled_certain": "cancelled",
            "quarantined": "parked",
        }.get(state)
        if suspension_state is None:
            raise CallerWorkConflict(f"state {state!r} is not terminal")
        cursor = connection.execute(
            """
            UPDATE rlm_workbench_suspensions
            SET state = ?, settled_at_unix_ms = ?
            WHERE operation_id = ? AND suspension_revision = ? AND state = 'pending'
            """,
            (
                suspension_state,
                projected_at_unix_ms,
                row["operation_id"],
                row["suspension_revision"],
            ),
        )
        if cursor.rowcount != 1:
            raise CallerWorkConflict("caller work suspension terminal projection lost authority")
        if state not in {"settled_success", "settled_failure", "cancelled_certain"}:
            return
        if settlement_digest is None:
            raise CallerWorkConflict("successor outbox requires a settlement digest")
        outer = connection.execute(
            "SELECT state FROM operations WHERE operation_id = ?",
            (row["operation_id"],),
        ).fetchone()
        if outer is None:
            raise CallerWorkConflict("caller settlement outer operation is absent")
        if str(outer["state"]) != "accepted":
            # Late certain evidence remains on the ticket, but a terminal or
            # indeterminate outer operation cannot gain a new continuation.
            return
        job = connection.execute(
            """
            SELECT phase, control_revision, cancellation_requested,
                   cumulative_deadline_unix_ms
            FROM rlm_workbench_jobs WHERE operation_id = ?
            """,
            (row["operation_id"],),
        ).fetchone()
        control = connection.execute(
            "SELECT control_revision, cancellation_requested FROM operation_controls "
            "WHERE operation_id = ?",
            (row["operation_id"],),
        ).fetchone()
        if job is None or control is None:
            raise CallerWorkConflict("caller settlement lost workbench control authority")
        if (
            str(row["state"]) == "outcome_unknown"
            and projected_at_unix_ms >= int(job["cumulative_deadline_unix_ms"])
        ):
            # Startup already classified the physical send as reconcile-only.
            # Late certain evidence is retained, but it cannot revive a
            # continuation after the cumulative deadline.
            return
        outbox_payload = {
            "operation_id": row["operation_id"],
            "suspension_revision": row["suspension_revision"],
            "settlement_digest": settlement_digest,
        }
        connection.execute(
            """
            INSERT INTO rlm_workbench_successor_outbox(
                operation_id, suspension_revision, settlement_digest, outbox_digest,
                state, rebind_generation, created_at_unix_ms
            ) VALUES (?, ?, ?, ?, 'pending', 0, ?)
            """,
            (
                row["operation_id"],
                row["suspension_revision"],
                settlement_digest,
                _canonical_sha256({"kind": "caller_work_settlement", **outbox_payload}),
                projected_at_unix_ms,
            ),
        )
        if (
            str(job["phase"]) != "waiting_external"
            or int(job["control_revision"]) != int(control["control_revision"])
            or bool(control["cancellation_requested"])
        ):
            raise CallerWorkConflict("caller settlement lost workbench control authority")
        prior_control_revision = int(job["control_revision"])
        next_control_revision = prior_control_revision + 1
        connection.execute(
            """
            UPDATE rlm_workbench_jobs
            SET phase = 'accepted', control_revision = ?, updated_at_unix_ms = ?
            WHERE operation_id = ? AND phase = 'waiting_external'
              AND control_revision = ?
            """,
            (
                next_control_revision,
                projected_at_unix_ms,
                row["operation_id"],
                prior_control_revision,
            ),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise CallerWorkConflict("caller settlement lost workbench phase authority")
        connection.execute(
            """
            UPDATE operation_controls
            SET control_revision = ?
            WHERE operation_id = ? AND control_revision = ?
              AND cancellation_requested = 0
            """,
            (next_control_revision, row["operation_id"], prior_control_revision),
        )
        if connection.execute("SELECT changes()").fetchone()[0] != 1:
            raise CallerWorkConflict("caller settlement lost operation control authority")

    def _replay_command(
        self,
        connection: Any,
        row: Any,
        *,
        command_kind: str,
        command: Mapping[str, Any],
    ) -> CallerWorkTicket | None:
        command_digest = _canonical_sha256({"command_kind": command_kind, "command": command})
        receipt = connection.execute(
            """
            SELECT ticket_id, command_kind, command_digest, result_revision,
                   result_ticket_json, result_digest
            FROM caller_work_command_receipts
            WHERE operation_id = ? AND idempotency_key = ?
            """,
            (row["operation_id"], command["idempotency_key"]),
        ).fetchone()
        if receipt is None:
            return None
        if (
            receipt["ticket_id"] != row["ticket_id"]
            or receipt["command_kind"] != command_kind
            or receipt["command_digest"] != command_digest
        ):
            raise CallerWorkIdempotencyConflict(
                "caller command key already binds different command bytes"
            )
        raw_result = str(receipt["result_ticket_json"])
        try:
            result = CallerWorkTicket.model_validate_json(raw_result, strict=True)
        except ValueError as error:
            raise CallerWorkConflict("caller command replay ticket is invalid") from error
        if raw_result != _canonical_json(result.root):
            raise CallerWorkConflict("caller command replay ticket is not canonical JSON")
        if (
            result.ticket_id != row["ticket_id"]
            or result.operation["value"] != row["operation_id"]
            or result.revision != receipt["result_revision"]
            or _canonical_sha256(result.root) != receipt["result_digest"]
        ):
            raise CallerWorkConflict("caller command replay authority is corrupt")
        return result

    def _record_command(
        self,
        connection: Any,
        row: Any,
        *,
        command_kind: str,
        command: Mapping[str, Any],
        result: CallerWorkTicket,
    ) -> None:
        result_json = _canonical_json(result.root)
        connection.execute(
            """
            INSERT INTO caller_work_command_receipts(
                operation_id, ticket_id, command_kind, idempotency_key,
                command_digest, result_revision, result_ticket_json,
                result_digest, created_at_unix_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["operation_id"],
                row["ticket_id"],
                command_kind,
                command["idempotency_key"],
                _canonical_sha256({"command_kind": command_kind, "command": command}),
                result.revision,
                result_json,
                _canonical_sha256(result.root),
                self._now_ms(),
            ),
        )

    def _complete_command(
        self,
        connection: Any,
        row: Any,
        *,
        command_kind: str,
        command: Mapping[str, Any],
        result: CallerWorkTicket,
    ) -> CallerWorkTicket:
        self._record_command(
            connection,
            row,
            command_kind=command_kind,
            command=command,
            result=result,
        )
        return result

    def _checked_row(self, connection: Any, command: Mapping[str, Any]) -> Any:
        row = self._row(connection, str(command["ticket_id"]))
        self._require_revision(row, int(command["expected_revision"]))
        if command["ticket_digest"] != row["ticket_digest"]:
            raise CallerWorkConflict("ticket digest mismatch")
        if command["operation"]["value"] != row["operation_id"]:
            raise CallerWorkConflict("operation identity mismatch")
        if command["expected_suspension_revision"] != row["suspension_revision"]:
            raise CallerWorkConflict("suspension revision mismatch")
        if command["expected_cumulative_deadline_unix_ms"] != row["deadline_unix_ms"]:
            raise CallerWorkConflict("cumulative deadline mismatch")
        control = connection.execute(
            """
            SELECT control_revision, cancellation_requested
            FROM operation_controls WHERE operation_id = ?
            """,
            (row["operation_id"],),
        ).fetchone()
        if control is None:
            raise CallerWorkConflict("caller work operation has no authoritative control row")
        if command["expected_control_revision"] != control["control_revision"]:
            raise CallerWorkConflict("control revision mismatch")
        if bool(control["cancellation_requested"]):
            raise CallerWorkConflict("authoritative cancellation is already requested")
        job = connection.execute(
            """
            SELECT control_revision, cancellation_revision, cancellation_requested,
                   cumulative_deadline_unix_ms
            FROM rlm_workbench_jobs WHERE operation_id = ?
            """,
            (row["operation_id"],),
        ).fetchone()
        if job is None:
            raise CallerWorkConflict("caller work operation has no workbench job")
        if command["expected_control_revision"] != job["control_revision"]:
            raise CallerWorkConflict("workbench control revision mismatch")
        if command["expected_cancellation_revision"] != job["cancellation_revision"]:
            raise CallerWorkConflict("cancellation revision mismatch")
        if bool(job["cancellation_requested"]):
            raise CallerWorkConflict("workbench cancellation mirror is already requested")
        if command["expected_cumulative_deadline_unix_ms"] != job["cumulative_deadline_unix_ms"]:
            raise CallerWorkConflict("workbench deadline mismatch")
        suspension = connection.execute(
            """
            SELECT control_revision, state FROM rlm_workbench_suspensions
            WHERE operation_id = ? AND suspension_revision = ?
            """,
            (row["operation_id"], row["suspension_revision"]),
        ).fetchone()
        if suspension is None:
            raise CallerWorkConflict("caller work suspension is absent")
        if command["expected_control_revision"] != suspension["control_revision"]:
            raise CallerWorkConflict("suspension control revision mismatch")
        if suspension["state"] != "pending":
            raise CallerWorkConflict("caller work suspension is no longer pending")
        return row

    @staticmethod
    def _require_reconciler(row: Any, command: Mapping[str, Any]) -> None:
        context = command["context"]
        if (
            context["principal_id"] != row["claimant_principal_id"]
            or context["session_id"] != row["claimant_session_id"]
        ):
            raise CallerWorkConflict("reconciler context is not the current claimant authority")
        CallerWorkRepository._require_internal_reconciler(row, command)
        CallerWorkRepository._require_reconcile_fence(
            command,
            physical_attempt_id=str(command["physical_attempt_id"]),
        )

    @staticmethod
    def _require_internal_reconciler(row: Any, command: Mapping[str, Any]) -> None:
        if command["reconciler_id"] != row["adapter_id"]:
            raise CallerWorkConflict("reconciler identity is not the current ticket adapter")
        if command["reconciler_generation"] != row["adapter_generation"]:
            raise CallerWorkConflict("reconciler generation is not the current adapter generation")

    @staticmethod
    def _require_reconcile_fence(
        command: Mapping[str, Any],
        *,
        physical_attempt_id: str,
    ) -> None:
        expected = build_reconcile_fence(
            ticket_id=str(command["ticket_id"]),
            expected_revision=int(command["expected_revision"]),
            physical_attempt_id=physical_attempt_id,
            candidate_receipt_digest=command.get("candidate_receipt_digest"),
            reconciler_id=str(command["reconciler_id"]),
            reconciler_generation=int(command["reconciler_generation"]),
            reconciliation_action=str(command["reconciliation_action"]),
        )
        if command["reconcile_fence"] != expected:
            raise CallerWorkConflict("reconciler fence does not bind current ticket authority")

    @staticmethod
    def _require_revision(row: Any, expected_revision: int) -> None:
        if int(row["revision"]) != expected_revision:
            raise StaleTicketRevision(
                f"ticket revision {row['revision']} does not match {expected_revision}"
            )

    @staticmethod
    def _require_claim(row: Any, command: Mapping[str, Any]) -> None:
        if command["claim_id"] != row["claim_id"]:
            raise CallerWorkConflict("claim id mismatch")
        if command["claim_fence"] != row["claim_fence"]:
            raise CallerWorkConflict("claim fence mismatch")
        if command["physical_attempt_id"] != row["physical_attempt_id"]:
            raise CallerWorkConflict("physical attempt mismatch")

    @staticmethod
    def _row(connection: Any, ticket_id: str) -> Any:
        row = connection.execute(
            "SELECT * FROM caller_work_tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
        if row is None:
            raise KeyError(ticket_id)
        return row

    def _get(self, connection: Any, ticket_id: str) -> CallerWorkTicket:
        return self._ticket_from_row(self._row(connection, ticket_id))

    @staticmethod
    def _ticket_from_row(row: Any) -> CallerWorkTicket:
        claimant = None
        if row["claimant_principal_id"] is not None:
            claimant = {
                "principal_id": row["claimant_principal_id"],
                "session_id": row["claimant_session_id"],
                "adapter_id": row["adapter_id"],
                "adapter_generation": row["adapter_generation"],
                "claim_id": row["claim_id"],
                "claim_fence": row["claim_fence"],
                "claim_expires_at_unix_ms": row["claim_expires_at_unix_ms"],
            }
        physical_attempt = None
        if row["physical_attempt_id"] is not None:
            physical_attempt = {
                "physical_attempt_id": row["physical_attempt_id"],
                "provider_or_child_idempotency_key": row["external_idempotency_key"],
                "lookup_supported": bool(row["lookup_supported"]),
                "cancel_supported": bool(row["cancel_supported"]),
                "send_started_at_unix_ms": row["send_started_at_unix_ms"],
                "sent_request_digest": row["sent_request_digest"],
                "sent_at_unix_ms": row["sent_at_unix_ms"],
                "provider_or_child_request_id": row["provider_or_child_request_id"],
            }
        document = {
            "schema_version": "aar.caller-work-ticket.v1",
            "operation": {"type": "operation", "value": row["operation_id"]},
            "suspension_revision": row["suspension_revision"],
            "ticket_id": row["ticket_id"],
            "revision": row["revision"],
            "owner": json.loads(str(row["logical_owner_json"])),
            "request": json.loads(str(row["request_json"])),
            "request_digest": row["request_digest"],
            "ticket_digest": row["ticket_digest"],
            "state": row["state"],
            "claimant": claimant,
            "physical_attempt": physical_attempt,
            "settled_receipt_digest": row["settled_receipt_digest"],
            "settled_at_unix_ms": row["settled_at_unix_ms"],
            "deadline_unix_ms": row["deadline_unix_ms"],
        }
        return CallerWorkTicket.model_validate(document, strict=True)


class CallerWorkDispatcher:
    """Resume a may-have-sent ticket without ever issuing a blind redispatch."""

    def __init__(self, repository: CallerWorkRepository, adapter: CallerWorkLookupAdapter) -> None:
        self._repository = repository
        self._adapter = adapter

    def resume(self, ticket_id: str) -> CallerWorkTicket:
        ticket = self._repository.get(ticket_id)
        if ticket.state not in {"send_started", "outcome_unknown", "cancel_requested"}:
            raise InvalidTicketTransition(f"cannot resume caller work from {ticket.state}")
        attempt = ticket.physical_attempt
        if attempt is None:
            raise CallerWorkConflict("may-have-sent ticket has no physical attempt")
        result = self._adapter.lookup(idempotency_key=attempt.provider_or_child_idempotency_key)
        if result is not None:
            receipt = CandidateReceipt.model_validate(dict(result), strict=True)
            method = str(ticket.request["method"])
            self._repository.append_candidate_receipt(
                receipt.root,
                source_kind=(
                    "child_lookup" if method.startswith("subagent.") else "provider_lookup"
                ),
            )
            current = self._repository.get(ticket_id)
            if (
                receipt.observation["kind"] == "model"
                and receipt.observation["outcome"] == "succeeded"
            ):
                if current.state == "send_started":
                    return self._repository.mark_outcome_unknown(
                        ticket_id=ticket_id,
                        expected_revision=current.revision,
                    )
                return current
            return self._repository.settle_candidate(
                ticket_id=ticket_id,
                expected_revision=current.revision,
                candidate_receipt_digest=str(receipt.receipt_digest),
                reconciler_id=str(current.claimant.adapter_id),
                reconciler_generation=current.claimant.adapter_generation,
                reconcile_fence=build_reconcile_fence(
                    ticket_id=ticket_id,
                    expected_revision=current.revision,
                    physical_attempt_id=attempt.physical_attempt_id,
                    candidate_receipt_digest=str(receipt.receipt_digest),
                    reconciler_id=str(current.claimant.adapter_id),
                    reconciler_generation=current.claimant.adapter_generation,
                    reconciliation_action="settle",
                ),
                idempotency_key=f"lookup-settle-{str(receipt.receipt_digest)[7:]}",
            )
        if ticket.state == "send_started":
            return self._repository.mark_outcome_unknown(
                ticket_id=ticket_id, expected_revision=ticket.revision
            )
        return ticket

    def close(self) -> None:
        return None
