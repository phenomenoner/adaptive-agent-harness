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
        "provider_or_child_idempotency_key": (
            physical.provider_or_child_idempotency_key
        ),
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
        "physical_attempt_id": (
            None if physical is None else physical.physical_attempt_id
        ),
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
            "route_receipt_digest": "sha256:" + "b" * 64,
            "usage_receipt_digest": "sha256:" + "c" * 64,
            "host_receipt_digest": "sha256:" + "d" * 64,
        },
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
        "schema_version": "aar.candidate-receipt.v1",
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


class MutableClock:
    def __init__(self, value: int) -> None:
        self.value = value

    def __call__(self) -> int:
        return self.value


def new_repository(tmp_path: Path, clock: MutableClock) -> Any:
    from aar.rlm_workbench_models import CallerWorkTicket
    from aar.runtime.caller_work import CallerWorkRepository

    database = tmp_path / "registry.sqlite"
    ticket_document = pending_ticket()
    create_caller_work_registry(database, ticket_document)
    repo = CallerWorkRepository(database, now_ms=clock)
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
            repo.mark_send_started(
                mark_send_started_input(first, idempotency_key="expired-mark")
            )

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
        receipt = candidate_receipt(started, suffix="race")
        repo.append_candidate_receipt(receipt)
        barrier = Barrier(2)

        def claimant_commit() -> Any:
            barrier.wait()
            return repo.commit(commit_input(started, idempotency_key="commit-race"))

        def reconcile() -> Any:
            barrier.wait()
            return repo.settle_candidate(
                ticket_id=TICKET_ID,
                expected_revision=started.revision,
                candidate_receipt_digest=receipt["receipt_digest"],
                reconciler_id="reconciler",
                reconciler_generation=1,
                reconcile_fence="sha256:" + "f" * 64,
                idempotency_key="reconcile-race",
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
        assert repo.get(TICKET_ID).state == "settled_success"
    finally:
        repo.close()


def test_provider_acceptance_crash_preserves_attempts_and_late_receipts(
    tmp_path: Path,
) -> None:
    from aar.runtime.caller_work import CallerWorkDispatcher, InjectedDispatchCrash

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
    dispatcher = CallerWorkDispatcher(
        repo,
        adapter,
        crash_after_provider_acceptance_once=True,
    )
    try:
        with pytest.raises(InjectedDispatchCrash):
            dispatcher.dispatch(TICKET_ID)

        after_crash = repo.get(TICKET_ID)
        assert after_crash.state == "send_started"
        assert len(repo.physical_attempt_history(TICKET_ID)) == 1

        completed = dispatcher.resume(TICKET_ID)
        assert completed.state == "settled_success"
        attempts = repo.physical_attempt_history(TICKET_ID)
        assert len(attempts) == 2
        assert attempts[0].physical_attempt_id != attempts[1].physical_attempt_id
        assert adapter.keys == [adapter.keys[0], adapter.keys[0]]

        late = candidate_receipt(
            completed,
            suffix="late-first-attempt",
            physical_attempt_id=attempts[0].physical_attempt_id,
        )
        repo.append_candidate_receipt(late)
        assert repo.get(TICKET_ID).state == "settled_success"
        assert {item.physical_attempt_id for item in repo.candidate_receipts(TICKET_ID)} >= {
            attempts[0].physical_attempt_id,
            attempts[1].physical_attempt_id,
        }
    finally:
        dispatcher.close()
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
        receipt = candidate_receipt(started, suffix="late-success")
        repo.append_candidate_receipt(receipt)
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
                ticket_id=TICKET_ID,
                expected_revision=started.revision,
                candidate_receipt_digest=receipt["receipt_digest"],
                reconciler_id="reconciler",
                reconciler_generation=1,
                reconcile_fence="sha256:" + "f" * 64,
                idempotency_key="settle-race-terminal",
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
