from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

import pytest

from aar.broker_models import (
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_sha256
from aar.runtime.caller_work import build_reconcile_fence

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
FIXTURES = SDD / "fixtures"
BASELINE_SQL = FIXTURES / "registry-v5.sql"
MIGRATION_SQL = SDD / "migration-v6.sql"
FIXED_NOW_MS = 1_999_999_990_000
OPERATION_ID = "fixture-operation"
TICKET_ID = "fixture-ticket"


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: object) -> str:
    return digest_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def load_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def pending_ticket() -> dict[str, Any]:
    ticket = load_fixture("valid-ticket-cancelled-before-send-pending.json")
    ticket["revision"] = 0
    ticket["state"] = "pending"
    ticket["claimant"] = None
    ticket["physical_attempt"] = None
    ticket["settled_receipt_digest"] = None
    ticket["settled_at_unix_ms"] = None
    return ticket


def mutation_context() -> dict[str, Any]:
    execute = load_fixture("valid-workbench-execute.json")
    context = execute["context"]
    assert isinstance(context, dict)
    return copy.deepcopy(context)


def create_caller_work_registry(path: Path, ticket: dict[str, Any]) -> None:
    request = ticket["request"]
    owner = ticket["owner"]
    assert isinstance(request, dict)
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(BASELINE_SQL.read_text(encoding="utf-8"))
        connection.executescript(MIGRATION_SQL.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO operations(
                operation_id, host_value, principal_value, idempotency_key,
                input_digest, state, certainty, runtime_generation,
                record_revision, reconciliation_required, request_json,
                payload_json, created_at_unix_ms, updated_at_unix_ms
            ) VALUES(?, 'host', 'fixture-principal', 'idem-caller-work', ?,
                     'accepted', 'certain', 1, 0, 0, '{}', '{}', ?, ?)
            """,
            (OPERATION_ID, "sha256:" + "1" * 64, FIXED_NOW_MS, FIXED_NOW_MS),
        )
        connection.execute(
            """
            INSERT INTO operation_controls(
                operation_id, control_revision, cancellation_requested
            ) VALUES(?, 1, 0)
            """,
            (OPERATION_ID,),
        )
        connection.execute(
            """
            INSERT INTO rlm_workbench_jobs(
                operation_id, phase, control_revision, cancellation_revision,
                cancellation_requested, spec_json, spec_digest, context_digest,
                route_binding_digest, cumulative_deadline_unix_ms, certainty,
                created_at_unix_ms, updated_at_unix_ms
            ) VALUES(?, 'waiting_external', 1, 0, 0, '{}', ?, ?, ?, ?,
                     'certain', ?, ?)
            """,
            (
                OPERATION_ID,
                "sha256:" + "2" * 64,
                "sha256:" + "3" * 64,
                "sha256:" + "4" * 64,
                ticket["deadline_unix_ms"],
                FIXED_NOW_MS,
                FIXED_NOW_MS,
            ),
        )
        owner_json = json.dumps(owner, sort_keys=True, separators=(",", ":"))
        connection.execute(
            """
            INSERT INTO rlm_workbench_suspensions(
                operation_id, suspension_revision, control_revision,
                logical_owner_json, logical_owner_digest, broker_method,
                contract_id, request_digest, ticket_id, state,
                created_at_unix_ms
            ) VALUES(?, 1, 1, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                OPERATION_ID,
                owner_json,
                canonical_digest(owner),
                request["method"],
                request["contract_id"],
                ticket["request_digest"],
                TICKET_ID,
                FIXED_NOW_MS,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def common_input(ticket: Any, *, idempotency_key: str) -> dict[str, Any]:
    return {
        "context": mutation_context(),
        "operation": {"type": "operation", "value": OPERATION_ID},
        "expected_control_revision": 1,
        "expected_cancellation_revision": 0,
        "expected_suspension_revision": 1,
        "expected_cumulative_deadline_unix_ms": ticket.deadline_unix_ms,
        "ticket_id": ticket.ticket_id,
        "expected_revision": ticket.revision,
        "ticket_digest": ticket.ticket_digest,
        "idempotency_key": idempotency_key,
    }


def claim_input(ticket: Any, *, idempotency_key: str = "claim-1") -> dict[str, Any]:
    return {
        **common_input(ticket, idempotency_key=idempotency_key),
        "adapter_id": "fixture-adapter",
        "adapter_generation": 1,
        "claim_lease_ms": 5_000,
    }


def mark_send_started_input(ticket: Any, *, idempotency_key: str) -> dict[str, Any]:
    claimant = ticket.claimant
    physical = ticket.physical_attempt
    assert claimant is not None and physical is not None
    return {
        **common_input(ticket, idempotency_key=idempotency_key),
        "claim_id": claimant.claim_id,
        "claim_fence": claimant.claim_fence,
        "physical_attempt_id": physical.physical_attempt_id,
        "expected_claim_expires_at_unix_ms": claimant.claim_expires_at_unix_ms,
        "provider_or_child_idempotency_key": (physical.provider_or_child_idempotency_key),
        "sent_request_digest": ticket.request_digest,
        "lookup_supported": True,
        "cancel_supported": True,
    }


def cancel_before_send_input(
    ticket: Any,
    *,
    expected_state: str,
    idempotency_key: str,
) -> dict[str, Any]:
    claimant = ticket.claimant
    physical = ticket.physical_attempt
    return {
        **common_input(ticket, idempotency_key=idempotency_key),
        "expected_pre_send_state": expected_state,
        "claim_id": None if claimant is None else claimant.claim_id,
        "claim_fence": None if claimant is None else claimant.claim_fence,
        "physical_attempt_id": (None if physical is None else physical.physical_attempt_id),
        "expected_claim_expires_at_unix_ms": (
            None if claimant is None else claimant.claim_expires_at_unix_ms
        ),
        "settled_receipt_digest": "sha256:" + "a" * 64,
        "settled_at_unix_ms": FIXED_NOW_MS + 1,
        "reason": "user_requested",
    }


def commit_input(ticket: Any, *, idempotency_key: str) -> dict[str, Any]:
    claimant = ticket.claimant
    physical = ticket.physical_attempt
    assert claimant is not None and physical is not None
    binding = ModelRouteBinding.model_validate(
        ticket.root["request"]["route_binding"], strict=True
    )
    route_receipt = ModelRouteReceipt.issue(
        requested=binding,
        effective=EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=binding.provider,
            model=binding.model,
            reasoning_effort=binding.reasoning_effort,
        ),
        finish_reason="stop",
        provider_response_id="provider-request-1",
        lookup_supported=True,
    )
    usage = ModelUsageRecord(
        accounting_source="provider_reported",
        input_tokens=7,
        output_tokens=3,
        total_tokens=10,
    )
    response = ModelResponse(output_text="ok", route_receipt=route_receipt, usage=usage)
    return {
        **common_input(ticket, idempotency_key=idempotency_key),
        "claim_id": claimant.claim_id,
        "claim_fence": claimant.claim_fence,
        "physical_attempt_id": physical.physical_attempt_id,
        "sent_request_digest": ticket.request_digest,
        "sent_at_unix_ms": FIXED_NOW_MS + 2,
        "provider_or_child_request_id": "provider-request-1",
        "observation": {
            "kind": "model",
            "outcome": "succeeded",
            "output_text": "ok",
            "output_digest": digest_bytes(b"ok"),
            "route_receipt_digest": route_receipt.receipt_digest,
            "usage_receipt_digest": canonical_sha256(usage),
            "host_receipt_digest": canonical_sha256(response),
        },
        "model_response": response.model_dump(mode="json"),
    }


def reconcile_input(
    ticket: Any,
    *,
    action: str,
    idempotency_key: str,
    candidate_receipt_digest: str | None = None,
) -> dict[str, Any]:
    physical = ticket.physical_attempt
    assert physical is not None
    command = {
        **common_input(ticket, idempotency_key=idempotency_key),
        "physical_attempt_id": physical.physical_attempt_id,
        "candidate_receipt_digest": candidate_receipt_digest,
        "reconciler_id": "fixture-adapter",
        "reconciler_generation": 1,
        "reconciliation_action": action,
    }
    return {
        **command,
        "reconcile_fence": build_reconcile_fence(
            ticket_id=ticket.ticket_id,
            expected_revision=ticket.revision,
            physical_attempt_id=physical.physical_attempt_id,
            candidate_receipt_digest=candidate_receipt_digest,
            reconciler_id="fixture-adapter",
            reconciler_generation=1,
            reconciliation_action=action,
        ),
    }


def settle_candidate_input(
    ticket: Any,
    candidate_receipt_digest: str,
    *,
    idempotency_key: str,
    reconciler_id: str | None = None,
) -> dict[str, Any]:
    physical = ticket.physical_attempt
    claimant = ticket.claimant
    assert physical is not None and claimant is not None
    current_reconciler_id = reconciler_id or claimant.adapter_id
    return {
        "ticket_id": ticket.ticket_id,
        "expected_revision": ticket.revision,
        "candidate_receipt_digest": candidate_receipt_digest,
        "reconciler_id": current_reconciler_id,
        "reconciler_generation": claimant.adapter_generation,
        "reconcile_fence": build_reconcile_fence(
            ticket_id=ticket.ticket_id,
            expected_revision=ticket.revision,
            physical_attempt_id=physical.physical_attempt_id,
            candidate_receipt_digest=candidate_receipt_digest,
            reconciler_id=current_reconciler_id,
            reconciler_generation=claimant.adapter_generation,
            reconciliation_action="settle",
        ),
        "idempotency_key": idempotency_key,
    }


def candidate_receipt(
    ticket: Any,
    *,
    suffix: str,
    physical_attempt_id: str | None = None,
) -> dict[str, Any]:
    physical = ticket.physical_attempt
    assert physical is not None
    payload: dict[str, Any] = {
        "schema_version": "aar.caller-work-candidate-receipt.v1",
        "ticket_id": ticket.ticket_id,
        "ticket_digest": ticket.ticket_digest,
        "physical_attempt_id": physical_attempt_id or physical.physical_attempt_id,
        "sent_request_digest": ticket.request_digest,
        "sent_at_unix_ms": FIXED_NOW_MS + 2,
        "provider_or_child_request_id": f"provider-{suffix}",
        "observation": {
            "kind": "model",
            "outcome": "succeeded",
            "output_text": suffix,
            "output_digest": digest_bytes(suffix.encode()),
            "route_receipt_digest": "sha256:" + "b" * 64,
            "usage_receipt_digest": "sha256:" + "c" * 64,
            "host_receipt_digest": "sha256:" + "d" * 64,
        },
        "callback_principal_id": "fixture-principal",
        "callback_session_id": "fixture-session",
        "callback_adapter_id": "fixture-adapter",
        "callback_adapter_generation": 1,
        "observed_at_unix_ms": FIXED_NOW_MS + 3,
        "signature_digest": "sha256:" + "e" * 64,
    }
    return {**payload, "receipt_digest": canonical_digest(payload)}


def sealed_model_candidate(
    ticket: Any,
    *,
    suffix: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    command = commit_input(ticket, idempotency_key=f"sealed-{suffix}")
    payload: dict[str, Any] = {
        "schema_version": "aar.caller-work-candidate-receipt.v1",
        "ticket_id": ticket.ticket_id,
        "ticket_digest": ticket.ticket_digest,
        "physical_attempt_id": ticket.physical_attempt.physical_attempt_id,
        "sent_request_digest": ticket.request_digest,
        "sent_at_unix_ms": FIXED_NOW_MS + 2,
        "provider_or_child_request_id": f"provider-{suffix}",
        "observation": command["observation"],
        "callback_principal_id": "fixture-principal",
        "callback_session_id": "fixture-session",
        "callback_adapter_id": "fixture-adapter",
        "callback_adapter_generation": 1,
        "observed_at_unix_ms": FIXED_NOW_MS + 3,
        "signature_digest": "sha256:" + "e" * 64,
    }
    return (
        {**payload, "receipt_digest": canonical_digest(payload)},
        command["model_response"],
    )


def model_document(value: Any) -> dict[str, Any]:
    document = json.loads(value.model_dump_json())
    assert isinstance(document, dict)
    return document


def assert_command_journal(
    repo: Any,
    *,
    idempotency_key: str,
    command_kind: str,
    result: Any,
) -> None:
    document = model_document(result)
    result_json = json.dumps(
        document,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    with sqlite3.connect(repo.database_path) as connection:
        row = connection.execute(
            """
            SELECT command_kind, result_revision, result_ticket_json, result_digest
            FROM caller_work_command_receipts
            WHERE operation_id = ? AND idempotency_key = ?
            """,
            (OPERATION_ID, idempotency_key),
        ).fetchone()
        count = connection.execute(
            """
            SELECT COUNT(*) FROM caller_work_command_receipts
            WHERE operation_id = ? AND idempotency_key = ?
            """,
            (OPERATION_ID, idempotency_key),
        ).fetchone()
    assert row == (
        command_kind,
        result.revision,
        result_json,
        canonical_digest(document),
    )
    assert count == (1,)


class MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def new_repository(tmp_path: Path, clock: MutableClock) -> Any:
    from aar.rlm_workbench_models import CallerWorkTicket
    from aar.runtime.caller_work import CallerWorkRepository
    from aar.runtime.model_broker import ModelExecutionJournal

    database = tmp_path / "registry.sqlite"
    ticket_document = pending_ticket()
    create_caller_work_registry(database, ticket_document)
    journal = ModelExecutionJournal(database)

    class LifecycleRepository(CallerWorkRepository):
        def close(self) -> None:
            super().close()
            journal.close()

    repo = LifecycleRepository(database, now_ms=clock, model_executions=journal)
    repo.create_ticket(CallerWorkTicket.model_validate(ticket_document, strict=True))
    return repo


def test_pre_send_cancellation_of_pending_ticket_never_creates_send_mark(
    tmp_path: Path,
) -> None:
    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        pending = repo.get(TICKET_ID)
        cancelled = repo.cancel_before_send(
            cancel_before_send_input(
                pending,
                expected_state="pending",
                idempotency_key="cancel-pending",
            )
        )
        assert cancelled.state == "cancelled_before_send"
        assert cancelled.physical_attempt is None
        assert repo.send_started_count(TICKET_ID) == 0
    finally:
        repo.close()


def test_cancel_before_send_races_mark_send_started_by_single_revision_cas(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import StaleTicketRevision

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        barrier = Barrier(2)

        def mark() -> Any:
            barrier.wait()
            return repo.mark_send_started(
                mark_send_started_input(reserved, idempotency_key="mark-race")
            )

        def cancel() -> Any:
            barrier.wait()
            return repo.cancel_before_send(
                cancel_before_send_input(
                    reserved,
                    expected_state="send_reserved",
                    idempotency_key="cancel-race",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(mark), pool.submit(cancel))
            outcomes: list[Any] = []
            errors: list[BaseException] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except BaseException as error:
                    errors.append(error)

        assert len(outcomes) == 1
        assert len(errors) == 1 and isinstance(errors[0], StaleTicketRevision)
        assert repo.get(TICKET_ID).state in {"send_started", "cancelled_before_send"}
    finally:
        repo.close()


def test_claim_expiry_boundary_fences_old_claim_and_retry_reuses_provider_key(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import ExpiredCallerClaim

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        first = repo.claim(claim_input(repo.get(TICKET_ID)))
        assert first.claimant is not None and first.physical_attempt is not None
        first_attempt = first.physical_attempt
        clock.value = first.claimant.claim_expires_at_unix_ms

        with pytest.raises(ExpiredCallerClaim):
            repo.mark_send_started(mark_send_started_input(first, idempotency_key="expired-mark"))

        pending = repo.reclaim_expired(
            ticket_id=TICKET_ID,
            expected_revision=first.revision,
            expected_claim_id=first.claimant.claim_id,
            expected_claim_fence=first.claimant.claim_fence,
        )
        second = repo.claim(claim_input(pending, idempotency_key="claim-2"))
        assert second.physical_attempt is not None
        assert second.physical_attempt.physical_attempt_id != first_attempt.physical_attempt_id
        assert (
            second.physical_attempt.provider_or_child_idempotency_key
            == first_attempt.provider_or_child_idempotency_key
        )
    finally:
        repo.close()


def test_cumulative_deadline_fences_send_start_before_claim_expiry(tmp_path: Path) -> None:
    from aar.runtime.caller_work import ExpiredCallerClaim

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        pending = repo.get(TICKET_ID)
        reserved = repo.claim(
            {
                **claim_input(pending, idempotency_key="claim-past-ticket-deadline"),
                "claim_lease_ms": 20_000,
            }
        )
        assert reserved.claimant is not None
        assert reserved.claimant.claim_expires_at_unix_ms > reserved.deadline_unix_ms
        clock.value = reserved.deadline_unix_ms

        with pytest.raises(ExpiredCallerClaim, match="deadline expired"):
            repo.mark_send_started(
                mark_send_started_input(reserved, idempotency_key="mark-at-ticket-deadline")
            )

        unchanged = repo.get(TICKET_ID)
        assert unchanged.state == "send_reserved"
        assert unchanged.revision == reserved.revision
        assert repo.send_started_count(TICKET_ID) == 0
    finally:
        repo.close()


def test_reconcile_retry_only_when_physical_send_is_certainly_unstarted(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import InvalidTicketTransition

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        first = repo.claim(claim_input(repo.get(TICKET_ID)))
        assert first.physical_attempt is not None
        first_attempt = first.physical_attempt

        pending = repo.reconcile(
            reconcile_input(
                first,
                action="retry_if_certain_no_send",
                idempotency_key="reconcile-certain-no-send",
            )
        )
        assert pending.state == "pending"
        assert pending.physical_attempt is None

        second = repo.claim(claim_input(pending, idempotency_key="claim-after-reconcile"))
        assert second.physical_attempt is not None
        assert second.physical_attempt.physical_attempt_id != first_attempt.physical_attempt_id
        assert (
            second.physical_attempt.provider_or_child_idempotency_key
            == first_attempt.provider_or_child_idempotency_key
        )
        started = repo.mark_send_started(
            mark_send_started_input(second, idempotency_key="mark-after-reconcile")
        )
        with pytest.raises(InvalidTicketTransition, match="unstarted send reservation"):
            repo.reconcile(
                reconcile_input(
                    started,
                    action="retry_if_certain_no_send",
                    idempotency_key="reconcile-after-send-mark",
                )
            )
        unchanged = repo.get(TICKET_ID)
        assert unchanged.state == "send_started"
        assert unchanged.revision == started.revision
        assert unchanged.physical_attempt is not None
        assert started.physical_attempt is not None
        assert (
            unchanged.physical_attempt.physical_attempt_id
            == started.physical_attempt.physical_attempt_id
        )
        assert (
            unchanged.physical_attempt.provider_or_child_idempotency_key
            == started.physical_attempt.provider_or_child_idempotency_key
        )
    finally:
        repo.close()


def test_cancel_after_send_mark_never_claims_pre_send_cancellation(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import InvalidTicketTransition

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-before-cancel")
        )
        with pytest.raises(InvalidTicketTransition):
            repo.cancel_before_send(
                cancel_before_send_input(
                    started,
                    expected_state="send_reserved",
                    idempotency_key="too-late-cancel",
                )
            )

        cancel_requested = repo.request_cancel(
            ticket_id=TICKET_ID,
            expected_revision=started.revision,
            reason="user_requested",
            idempotency_key="request-cancel",
        )
        assert cancel_requested.state == "cancel_requested"
        assert cancel_requested.physical_attempt is not None
        assert cancel_requested.physical_attempt.send_started_at_unix_ms is not None
    finally:
        repo.close()


def test_stale_claimant_and_reconciler_cannot_both_win_same_ticket_revision(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import StaleTicketRevision

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-stale-race")
        )
        commit_command = commit_input(started, idempotency_key="commit-race")
        receipt, callback_response = sealed_model_candidate(started, suffix="race")
        repo.append_model_candidate_receipt(receipt, callback_response)
        barrier = Barrier(2)

        def claimant_commit() -> Any:
            barrier.wait()
            return repo.commit(commit_command)

        def reconcile() -> Any:
            barrier.wait()
            return repo.settle_candidate(
                **settle_candidate_input(
                    started,
                    receipt["receipt_digest"],
                    idempotency_key="reconcile-race",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(claimant_commit), pool.submit(reconcile))
            outcomes: list[Any] = []
            errors: list[BaseException] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except BaseException as error:
                    errors.append(error)

        assert len(outcomes) == 1
        assert len(errors) == 1 and isinstance(errors[0], StaleTicketRevision)
        assert repo.get(TICKET_ID).state in {"settled_success", "quarantined"}
    finally:
        repo.close()


def test_provider_acceptance_crash_does_not_redispatch_and_late_receipt_settles(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkDispatcher

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)

    class Adapter:
        def __init__(self) -> None:
            self.keys: list[str] = []
            self.calls = 0

        def send(self, *, idempotency_key: str, request: object) -> dict[str, Any]:
            del request
            self.keys.append(idempotency_key)
            self.calls += 1
            return {
                "provider_or_child_request_id": f"provider-{self.calls}",
                "observation": commit_input(
                    repo.get(TICKET_ID), idempotency_key="adapter-observation"
                )["observation"],
            }

        def lookup(self, *, idempotency_key: str) -> None:
            del idempotency_key
            return None

    adapter = Adapter()
    dispatcher = CallerWorkDispatcher(repo, adapter)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-crash")
        )
        first_attempt = started.physical_attempt
        assert first_attempt is not None

        # The provider accepted the request, but the caller crashed before the
        # observation could be committed.  The durable send-start mark is the
        # only authority available after restart.
        adapter.send(
            idempotency_key=first_attempt.provider_or_child_idempotency_key,
            request=started.request,
        )
        assert adapter.calls == 1

        resumed = dispatcher.resume(TICKET_ID)
        assert resumed.state == "outcome_unknown"
        assert adapter.calls == 1
        assert adapter.keys == [first_attempt.provider_or_child_idempotency_key]
        assert resumed.physical_attempt is not None
        assert resumed.physical_attempt.physical_attempt_id == first_attempt.physical_attempt_id
        assert (
            resumed.physical_attempt.provider_or_child_idempotency_key
            == first_attempt.provider_or_child_idempotency_key
        )

        late, late_response = sealed_model_candidate(resumed, suffix="late-first-attempt")
        repo.append_model_candidate_receipt(late, late_response)
        completed = repo.settle_candidate(
            **settle_candidate_input(
                resumed,
                late["receipt_digest"],
                idempotency_key="settle-late-first",
            )
        )
        assert completed.state == "settled_success"
        assert completed.settled_receipt_digest == late["receipt_digest"]
    finally:
        dispatcher.close()
        repo.close()


def test_restart_raw_model_lookup_stays_unknown_without_physical_redispatch(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkDispatcher

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)

    class LookupAdapter:
        def __init__(self) -> None:
            self.calls = 0
            self.result: dict[str, Any] | None = None

        def lookup(self, *, idempotency_key: str) -> dict[str, Any] | None:
            del idempotency_key
            self.calls += 1
            return self.result

    adapter = LookupAdapter()
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-lookup-result")
        )
        adapter.result = candidate_receipt(started, suffix="lookup-result")

        unresolved = CallerWorkDispatcher(repo, adapter).resume(TICKET_ID)
        assert unresolved.state == "outcome_unknown"
        assert adapter.calls == 1
        assert len(repo.candidate_receipts(TICKET_ID)) == 1
        with sqlite3.connect(repo.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM model_executions WHERE state = 'result_committed'"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox"
            ).fetchone() == (0,)
    finally:
        repo.close()


def test_restart_and_duplicate_late_callback_preserve_terminal_ticket(
    tmp_path: Path,
) -> None:
    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    database = repo.database_path
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-terminal")
        )
        settled = repo.commit(commit_input(started, idempotency_key="commit-terminal"))
        assert settled.state == "settled_success"
        duplicate = candidate_receipt(settled, suffix="duplicate")
    finally:
        repo.close()

    from aar.runtime.caller_work import CallerWorkRepository

    reopened = CallerWorkRepository(database, now_ms=clock)
    try:
        first = reopened.append_candidate_receipt(duplicate)
        replay = reopened.append_candidate_receipt(duplicate)
        assert first.replayed is False
        assert replay.replayed is True
        assert reopened.get(TICKET_ID).state == "settled_success"
        assert len(reopened.candidate_receipts(TICKET_ID)) == 2
    finally:
        reopened.close()


def test_cancellation_vs_late_success_race_keeps_single_state_and_all_evidence(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import StaleTicketRevision

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-cancel-success")
        )
        receipt, callback_response = sealed_model_candidate(started, suffix="late-success")
        repo.append_model_candidate_receipt(receipt, callback_response)
        barrier = Barrier(2)

        def cancel() -> Any:
            barrier.wait()
            return repo.request_cancel(
                ticket_id=TICKET_ID,
                expected_revision=started.revision,
                reason="user_requested",
                idempotency_key="cancel-race-terminal",
            )

        def settle() -> Any:
            barrier.wait()
            return repo.settle_candidate(
                **settle_candidate_input(
                    started,
                    receipt["receipt_digest"],
                    idempotency_key="settle-race-terminal",
                )
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(cancel), pool.submit(settle))
            outcomes: list[Any] = []
            errors: list[BaseException] = []
            for future in futures:
                try:
                    outcomes.append(future.result())
                except BaseException as error:
                    errors.append(error)

        assert len(outcomes) == 1
        assert len(errors) == 1 and isinstance(errors[0], StaleTicketRevision)
        assert repo.get(TICKET_ID).state in {"cancel_requested", "settled_success"}
        assert receipt["receipt_digest"] in {
            item.receipt_digest for item in repo.candidate_receipts(TICKET_ID)
        }
    finally:
        repo.close()


def test_authoritative_operation_cancellation_blocks_claim_without_ticket_mutation(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkConflict

    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        pending = repo.get(TICKET_ID)
        connection = sqlite3.connect(repo.database_path)
        try:
            connection.execute(
                "UPDATE operation_controls SET cancellation_requested=1, "
                "requested_at_unix_ms=? WHERE operation_id=?",
                (FIXED_NOW_MS, OPERATION_ID),
            )
            connection.commit()
        finally:
            connection.close()

        with pytest.raises(CallerWorkConflict, match="cancellation"):
            repo.claim(claim_input(pending, idempotency_key="claim-after-cancel"))
        assert repo.get(TICKET_ID) == pending
    finally:
        repo.close()


def test_success_settlement_projects_suspension_and_successor_outbox(
    tmp_path: Path,
) -> None:
    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-outbox")
        )
        settled = repo.commit(commit_input(started, idempotency_key="commit-outbox"))

        connection = sqlite3.connect(repo.database_path)
        connection.row_factory = sqlite3.Row
        try:
            suspension = connection.execute(
                "SELECT state, settled_at_unix_ms FROM rlm_workbench_suspensions "
                "WHERE operation_id=? AND suspension_revision=?",
                (OPERATION_ID, 1),
            ).fetchone()
            outbox = connection.execute(
                "SELECT state, settlement_digest, outbox_digest "
                "FROM rlm_workbench_successor_outbox "
                "WHERE operation_id=? AND suspension_revision=?",
                (OPERATION_ID, 1),
            ).fetchone()
        finally:
            connection.close()

        assert suspension is not None
        assert (suspension["state"], suspension["settled_at_unix_ms"]) == (
            "settled",
            settled.settled_at_unix_ms,
        )
        assert outbox is not None
        assert outbox["state"] == "pending"
        assert outbox["settlement_digest"] == settled.settled_receipt_digest
        assert outbox["outbox_digest"].startswith("sha256:")
    finally:
        repo.close()


def test_conflicting_candidate_receipts_quarantine_without_successor_outbox(
    tmp_path: Path,
) -> None:
    clock = MutableClock(FIXED_NOW_MS)
    repo = new_repository(tmp_path, clock)
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-conflict")
        )
        first = candidate_receipt(started, suffix="first-authority")
        second = candidate_receipt(started, suffix="second-authority")
        repo.append_candidate_receipt(first)
        repo.append_candidate_receipt(second)

        quarantined = repo.settle_candidate(
            **settle_candidate_input(
                started,
                first["receipt_digest"],
                idempotency_key="settle-conflict",
            )
        )
        assert quarantined.state == "quarantined"
        assert quarantined.settled_receipt_digest is None

        connection = sqlite3.connect(repo.database_path)
        try:
            suspension_state = connection.execute(
                "SELECT state FROM rlm_workbench_suspensions "
                "WHERE operation_id=? AND suspension_revision=?",
                (OPERATION_ID, 1),
            ).fetchone()
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox "
                "WHERE operation_id=? AND suspension_revision=?",
                (OPERATION_ID, 1),
            ).fetchone()
        finally:
            connection.close()
        assert suspension_state == ("parked",)
        assert outbox_count == (0,)
    finally:
        repo.close()


def test_claim_command_exact_replay_and_cross_command_key_conflict(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        command = claim_input(repo.get(TICKET_ID), idempotency_key="journal-claim")
        claimed = repo.claim(command)
        replayed = repo.claim(copy.deepcopy(command))
        assert model_document(replayed) == model_document(claimed)

        changed = copy.deepcopy(command)
        changed["adapter_generation"] = 2
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.claim(changed)

        cross_command = mark_send_started_input(claimed, idempotency_key="journal-claim")
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.mark_send_started(cross_command)

        current = repo.get(TICKET_ID)
        assert current.state == "send_reserved"
        assert current.revision == claimed.revision
        assert repo.send_started_count(TICKET_ID) == 0
        assert_command_journal(
            repo,
            idempotency_key="journal-claim",
            command_kind="claim",
            result=claimed,
        )
    finally:
        repo.close()


def test_mark_send_started_command_replay_conflicts_before_second_mark(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID), idempotency_key="setup-mark-claim"))
        command = mark_send_started_input(reserved, idempotency_key="journal-mark-send")
        started = repo.mark_send_started(command)
        replayed = repo.mark_send_started(copy.deepcopy(command))
        assert model_document(replayed) == model_document(started)

        changed = copy.deepcopy(command)
        changed["lookup_supported"] = False
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.mark_send_started(changed)

        current = repo.get(TICKET_ID)
        assert current.revision == started.revision
        assert current.state == "send_started"
        assert repo.send_started_count(TICKET_ID) == 1
        assert_command_journal(
            repo,
            idempotency_key="journal-mark-send",
            command_kind="mark_send_started",
            result=started,
        )
    finally:
        repo.close()


def test_cancel_command_replay_does_not_repeat_terminal_projection(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        command = cancel_before_send_input(
            repo.get(TICKET_ID),
            expected_state="pending",
            idempotency_key="journal-cancel",
        )
        cancelled = repo.cancel_before_send(command)
        replayed = repo.cancel_before_send(copy.deepcopy(command))
        assert model_document(replayed) == model_document(cancelled)

        changed = copy.deepcopy(command)
        changed["settled_receipt_digest"] = "sha256:" + "f" * 64
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.cancel_before_send(changed)

        current = repo.get(TICKET_ID)
        assert current.revision == cancelled.revision
        assert current.state == "cancelled_before_send"
        assert repo.send_started_count(TICKET_ID) == 0
        assert_command_journal(
            repo,
            idempotency_key="journal-cancel",
            command_kind="cancel_before_send",
            result=cancelled,
        )
    finally:
        repo.close()


def test_model_journal_and_commit_roll_back_if_candidate_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(
            claim_input(repo.get(TICKET_ID), idempotency_key="rollback-claim")
        )
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="rollback-mark")
        )
        command = commit_input(started, idempotency_key="rollback-commit")

        def fail_candidate_insert(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("injected failure after journal insert")

        monkeypatch.setattr(repo, "_insert_candidate", fail_candidate_insert)
        with pytest.raises(RuntimeError, match="injected failure after journal insert"):
            repo.commit(command)

        with sqlite3.connect(repo.database_path) as connection:
            durable = connection.execute(
                """
                SELECT ticket.state, ticket.revision,
                       (SELECT COUNT(*) FROM model_route_bindings
                        WHERE operation_id = ticket.operation_id),
                       (SELECT COUNT(*) FROM model_executions
                        WHERE operation_id = ticket.operation_id),
                       (SELECT COUNT(*) FROM caller_work_candidate_receipts
                        WHERE ticket_id = ticket.ticket_id),
                       (SELECT COUNT(*) FROM caller_work_command_receipts
                        WHERE operation_id = ticket.operation_id
                          AND command_kind = 'commit')
                FROM caller_work_tickets AS ticket WHERE ticket.ticket_id = ?
                """,
                (TICKET_ID,),
            ).fetchone()
        assert durable == ("send_started", started.revision, 0, 0, 0, 0)
    finally:
        repo.close()


def test_raw_model_candidate_cannot_settle_without_journal_authority(tmp_path: Path) -> None:
    from aar.runtime.caller_work import CallerWorkConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID), idempotency_key="raw-claim"))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="raw-mark")
        )
        receipt = candidate_receipt(started, suffix="raw-model")
        repo.append_candidate_receipt(receipt, source_kind="provider_lookup")

        with pytest.raises(CallerWorkConflict, match="journal is absent"):
            repo.settle_candidate(
                **settle_candidate_input(
                    started,
                    receipt["receipt_digest"],
                    idempotency_key="raw-settle",
                )
            )

        assert repo.get(TICKET_ID).state == "send_started"
        with sqlite3.connect(repo.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM model_executions WHERE state = 'result_committed'"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox"
            ).fetchone() == (0,)
    finally:
        repo.close()


def test_sealed_model_callback_replays_and_changed_evidence_conflicts(tmp_path: Path) -> None:
    from aar.runtime.caller_work import CallerWorkConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID), idempotency_key="sealed-claim"))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="sealed-mark")
        )
        receipt, response = sealed_model_candidate(started, suffix="sealed-replay")

        first = repo.append_model_candidate_receipt(receipt, response)
        replay = repo.append_model_candidate_receipt(
            copy.deepcopy(receipt),
            copy.deepcopy(response),
        )
        assert first.replayed is False
        assert replay.replayed is True

        changed = copy.deepcopy(response)
        changed["output_text"] = "changed"
        with pytest.raises(CallerWorkConflict, match="does not match observation"):
            repo.append_model_candidate_receipt(receipt, changed)

        settled = repo.settle_candidate(
            **settle_candidate_input(
                started,
                receipt["receipt_digest"],
                idempotency_key="sealed-settle",
            )
        )
        assert settled.state == "settled_success"
    finally:
        repo.close()


def test_sealed_model_callback_rolls_back_if_candidate_insert_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(
            claim_input(repo.get(TICKET_ID), idempotency_key="callback-rollback-claim")
        )
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="callback-rollback-mark")
        )
        receipt, response = sealed_model_candidate(started, suffix="callback-rollback")

        def fail_candidate_insert(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("injected callback candidate failure")

        monkeypatch.setattr(repo, "_insert_candidate", fail_candidate_insert)
        with pytest.raises(RuntimeError, match="injected callback candidate failure"):
            repo.append_model_candidate_receipt(receipt, response)

        with sqlite3.connect(repo.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM model_route_bindings"
            ).fetchone() == (0,)
            assert connection.execute("SELECT COUNT(*) FROM model_executions").fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts"
            ).fetchone() == (0,)
        assert repo.get(TICKET_ID).state == "send_started"
    finally:
        repo.close()


def test_commit_command_replay_does_not_duplicate_receipt_or_outbox(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(
            claim_input(repo.get(TICKET_ID), idempotency_key="setup-commit-claim")
        )
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="setup-commit-mark")
        )
        command = commit_input(started, idempotency_key="journal-commit")
        settled = repo.commit(command)
        replayed = repo.commit(copy.deepcopy(command))
        assert model_document(replayed) == model_document(settled)

        changed = copy.deepcopy(command)
        changed["provider_or_child_request_id"] = "provider-request-changed"
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.commit(changed)

        assert len(repo.candidate_receipts(TICKET_ID)) == 1
        with sqlite3.connect(repo.database_path) as connection:
            outbox_count = connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox "
                "WHERE operation_id=? AND suspension_revision=?",
                (OPERATION_ID, 1),
            ).fetchone()
        assert outbox_count == (1,)
        assert model_document(repo.get(TICKET_ID)) == model_document(settled)
        assert_command_journal(
            repo,
            idempotency_key="journal-commit",
            command_kind="commit",
            result=settled,
        )
    finally:
        repo.close()


def test_reconcile_command_replay_does_not_create_another_attempt(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(
            claim_input(repo.get(TICKET_ID), idempotency_key="setup-reconcile-claim")
        )
        command = reconcile_input(
            reserved,
            action="retry_if_certain_no_send",
            idempotency_key="journal-reconcile",
        )
        pending = repo.reconcile(command)
        replayed = repo.reconcile(copy.deepcopy(command))
        assert model_document(replayed) == model_document(pending)

        changed = copy.deepcopy(command)
        changed["reconciler_generation"] = 2
        with pytest.raises(CallerWorkIdempotencyConflict):
            repo.reconcile(changed)

        current = repo.get(TICKET_ID)
        assert current.state == "pending"
        assert current.revision == pending.revision
        assert current.physical_attempt is None
        assert_command_journal(
            repo,
            idempotency_key="journal-reconcile",
            command_kind="reconcile",
            result=pending,
        )
    finally:
        repo.close()


def test_concurrent_changed_payload_same_key_creates_one_reservation(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkIdempotencyConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        pending = repo.get(TICKET_ID)
        first = claim_input(pending, idempotency_key="journal-concurrent-claim")
        second = copy.deepcopy(first)
        second["adapter_generation"] = 2
        barrier = Barrier(2)

        def invoke(command: dict[str, Any]) -> Any:
            barrier.wait()
            return repo.claim(command)

        outcomes: list[Any] = []
        errors: list[BaseException] = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(invoke, first), pool.submit(invoke, second))
            for future in futures:
                try:
                    outcomes.append(future.result())
                except BaseException as error:
                    errors.append(error)

        assert len(outcomes) == 1
        assert len(errors) == 1
        assert isinstance(errors[0], CallerWorkIdempotencyConflict)
        winner = outcomes[0]
        current = repo.get(TICKET_ID)
        assert current.revision == winner.revision == 1
        assert current.state == "send_reserved"
        assert current.physical_attempt is not None
        assert repo.send_started_count(TICKET_ID) == 0
        assert_command_journal(
            repo,
            idempotency_key="journal-concurrent-claim",
            command_kind="claim",
            result=winner,
        )
    finally:
        repo.close()


def test_reconcile_rejects_stale_identity_generation_and_fence(tmp_path: Path) -> None:
    from aar.runtime.caller_work import CallerWorkConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-reconciler-authority")
        )
        physical = started.physical_attempt
        assert physical is not None
        base = reconcile_input(
            started,
            action="quarantine",
            idempotency_key="reconcile-authority-base",
            candidate_receipt_digest="sha256:" + "f" * 64,
        )
        variants: list[dict[str, Any]] = []
        for field, value in (
            ("reconciler_id", "other-principal"),
            ("reconciler_generation", 2),
        ):
            command = {**base, field: value, "idempotency_key": f"reconcile-{field}"}
            command["reconcile_fence"] = build_reconcile_fence(
                ticket_id=started.ticket_id,
                expected_revision=started.revision,
                physical_attempt_id=physical.physical_attempt_id,
                candidate_receipt_digest="sha256:" + "f" * 64,
                reconciler_id=command["reconciler_id"],
                reconciler_generation=command["reconciler_generation"],
                reconciliation_action="quarantine",
            )
            variants.append(command)
        variants.append(
            {
                **base,
                "idempotency_key": "reconcile-invalid-fence",
                "reconcile_fence": "sha256:" + "0" * 64,
            }
        )

        for command in variants:
            with pytest.raises(CallerWorkConflict):
                repo.reconcile(command)
        assert repo.get(TICKET_ID) == started
    finally:
        repo.close()


def test_settle_candidate_exact_retry_replays_terminal_truth(tmp_path: Path) -> None:
    from aar.runtime.caller_work import CallerWorkConflict

    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-settlement-replay")
        )
        receipt, callback_response = sealed_model_candidate(started, suffix="settlement-replay")
        repo.append_model_candidate_receipt(receipt, callback_response)
        command = settle_candidate_input(
            started,
            receipt["receipt_digest"],
            idempotency_key="settlement-replay",
        )

        first = repo.settle_candidate(**command)
        replay = repo.settle_candidate(**command)
        assert replay == first
        assert replay.state == "settled_success"

        changed = {**command, "reconciler_id": "other-reconciler"}
        with pytest.raises(CallerWorkConflict):
            repo.settle_candidate(**changed)
    finally:
        repo.close()


def test_full_candidate_receipt_digest_conflict_quarantines(tmp_path: Path) -> None:
    repo = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repo.claim(claim_input(repo.get(TICKET_ID)))
        started = repo.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="mark-full-digest-conflict")
        )
        first = candidate_receipt(started, suffix="same-authority")
        forged = {**first, "signature_digest": "sha256:" + "1" * 64}
        from aar.runtime.caller_work import CallerWorkConflict

        with pytest.raises(CallerWorkConflict):
            repo.append_candidate_receipt(forged)
        second = {**first, "signature_digest": "sha256:" + "0" * 64}
        second.pop("receipt_digest")
        second["receipt_digest"] = canonical_digest(second)
        repo.append_candidate_receipt(first)
        repo.append_candidate_receipt(second)

        result = repo.settle_candidate(
            **settle_candidate_input(
                started,
                first["receipt_digest"],
                idempotency_key="settle-full-digest-conflict",
            )
        )
        assert result.state == "quarantined"
        assert result.settled_receipt_digest is None
    finally:
        repo.close()
