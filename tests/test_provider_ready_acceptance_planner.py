from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from test_caller_work_lifecycle import (
    OPERATION_ID,
    TICKET_ID,
    MutableClock,
    claim_input,
    commit_input,
    mark_send_started_input,
    new_repository,
    reconcile_input,
)
from test_provider_ready_planner_runtime import (
    FIXED_NOW_MS,
    _FailIfCalledPlanner,
    _FakeIPythonWorkspaceBackend,
    _model_commit_evidence,
    caller_common_input,
    digest_bytes,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.reference_host as reference_host_module
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import RecoveryPlannerInput
from aar.runtime.caller_work import CallerWorkDispatcher
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.registry import InvalidTransition, PlannerOwner, StaleAttemptFence
from aar.runtime.rlm_workbench import (
    RlmWorkbenchConflict,
    _RecoveryContinuation,
)
from aar.runtime.sqlite_repository import SQLiteConnectionFactory

# These are cell-bound authority/rebind projections.  A root planner may read
# the ordinary cell projection for context, but it must not own these tables.
_CELL_BOUND_AUTHORITY_TABLES = frozenset(
    {
        "rlm_workbench_attempt_authority",
        "rlm_workbench_rebind_transfers",
        "rlm_workbench_artifact_stages",
        "rlm_workbench_cell_manifests",
        "rlm_workbench_finalization_manifests",
    }
)
_WRITE_ACTIONS = frozenset(
    {
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_DELETE,
    }
)


def _claim(host: Any, owner: str) -> tuple[Any, AttemptFence]:
    claim = host.registry.claim_next(
        runtime_generation=host.runtime_generation,
        dispatcher_generation=host.runtime_generation,
        owner_digest=canonical_sha256(owner),
        lease_duration_ms=30_000,
    )
    assert claim is not None
    return claim, AttemptFence(
        dispatcher_generation=claim.dispatcher_generation,
        lease_epoch=claim.lease_epoch,
        owner_digest=claim.owner_digest,
    )


def _open_caller_delegated_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Any, Any, Any]:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _FakeIPythonWorkspaceBackend,
    )
    host = reference_host_module.ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=_FailIfCalledPlanner(),
        enable_durable_dispatch=False,
        dispatcher_concurrency=1,
    )
    request = workbench_request(
        host,
        require_named_artifacts=False,
        execution_mode="caller_delegated",
    )
    record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
    return host, request, record


def _commit_planner_ticket(
    host: Any,
    request: Any,
    pending: Any,
    directive: dict[str, Any],
    *,
    prefix: str,
) -> tuple[Any, dict[str, str]]:
    repository = host.caller_work
    assert repository is not None
    claimed = repository.claim(
        {
            **caller_common_input(
                host.registry.database_path,
                request,
                pending,
                idempotency_key=f"{prefix}-claim",
            ),
            "adapter_id": "fixture-adapter",
            "adapter_generation": 1,
            "claim_lease_ms": 5_000,
        }
    )
    assert claimed.claimant is not None and claimed.physical_attempt is not None
    started = repository.mark_send_started(
        {
            **caller_common_input(
                host.registry.database_path,
                request,
                claimed,
                idempotency_key=f"{prefix}-send-start",
            ),
            "claim_id": claimed.claimant.claim_id,
            "claim_fence": claimed.claimant.claim_fence,
            "physical_attempt_id": claimed.physical_attempt.physical_attempt_id,
            "expected_claim_expires_at_unix_ms": claimed.claimant.claim_expires_at_unix_ms,
            "provider_or_child_idempotency_key": (
                claimed.physical_attempt.provider_or_child_idempotency_key
            ),
            "sent_request_digest": claimed.request_digest,
            "lookup_supported": True,
            "cancel_supported": True,
        }
    )
    assert started.claimant is not None and started.physical_attempt is not None
    output_text = canonical_json_bytes(directive).decode("utf-8")
    evidence, model_response = _model_commit_evidence(started, output_text)
    settled = repository.commit(
        {
            **caller_common_input(
                host.registry.database_path,
                request,
                started,
                idempotency_key=f"{prefix}-commit",
            ),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": FIXED_NOW_MS + 2,
            "provider_or_child_request_id": f"provider-{prefix}",
            "observation": {
                "kind": "model",
                "outcome": "succeeded",
                "output_text": output_text,
                "output_digest": digest_bytes(output_text.encode("utf-8")),
                **evidence,
            },
            "model_response": model_response,
        }
    )
    assert settled.state == "settled_success"
    return settled, evidence


def _install_sql_audit(
    monkeypatch: pytest.MonkeyPatch,
    host: Any,
) -> list[tuple[int, str | None, str | None]]:
    actions: list[tuple[int, str | None, str | None]] = []

    def authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        _database: str | None,
        _trigger: str | None,
    ) -> int:
        if action in {
            sqlite3.SQLITE_READ,
            sqlite3.SQLITE_INSERT,
            sqlite3.SQLITE_UPDATE,
            sqlite3.SQLITE_DELETE,
        }:
            actions.append((action, arg1, arg2))
        return sqlite3.SQLITE_OK

    original_transaction = SQLiteConnectionFactory.transaction

    @contextlib.contextmanager
    def traced_transaction(
        factory: SQLiteConnectionFactory,
        *,
        write: bool,
    ) -> Any:
        with original_transaction(factory, write=write) as connection:
            connection.set_authorizer(authorizer)
            try:
                yield connection
            finally:
                connection.set_authorizer(None)

    monkeypatch.setattr(SQLiteConnectionFactory, "transaction", traced_transaction)
    host.registry._connection.set_authorizer(authorizer)
    return actions


def test_recovery_logical_owner_row_preserves_exact_wire_and_step_index(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, request, record = _open_caller_delegated_host(tmp_path, monkeypatch)
    try:
        first_claim, first_fence = _claim(host, "recovery-owner-first")
        snapshot = host.rlm_workbench.run_claimed(
            record.operation,
            first_claim.attempt,
            first_fence,
        )
        assert snapshot.phase == "waiting_external"
        ticket_id = wait_for_pending_ticket(host.registry.database_path)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)
        settled, _ = _commit_planner_ticket(
            host,
            request,
            pending,
            {
                "kind": "finalize",
                "output": {"answer": "recovery-owner", "artifacts": []},
                "artifact_stage_ids": [],
            },
            prefix="recovery-owner",
        )

        owner = PlannerOwner(phase="recovery", step_index=0).as_wire()
        owner_json = canonical_json_bytes(owner).decode("utf-8")
        ticket_payload = dict(settled.root)
        ticket_payload["owner"] = owner
        ticket_payload.pop("ticket_digest", None)
        ticket_digest = canonical_sha256(ticket_payload)
        host.registry._connection.execute("PRAGMA defer_foreign_keys = ON")
        with host.registry._connection:
            host.registry._connection.execute(
                """
                UPDATE caller_work_tickets
                SET logical_owner_json = ?, ticket_digest = ?
                WHERE ticket_id = ?
                """,
                (owner_json, ticket_digest, ticket_id),
            )
            host.registry._connection.execute(
                """
                UPDATE rlm_workbench_suspensions
                SET logical_owner_json = ?, logical_owner_digest = ?
                WHERE operation_id = ? AND suspension_revision = 1
                """,
                (owner_json, canonical_sha256(owner), record.operation.value),
            )

        successor, successor_fence = _claim(host, "recovery-owner-successor")
        token = host.registry.prepared_planner_successor(
            successor.attempt,
            successor_fence.dispatcher_generation,
            successor_fence.lease_epoch,
            successor_fence.owner_digest,
        )
        assert token is not None
        assert token["logical_owner"] == owner
        assert set(token["logical_owner"]) == {"kind", "phase", "step_index"}
        assert token["logical_owner"]["step_index"] == 0
    finally:
        host.close()


def test_finalizer_owner_and_model_receipt_use_exact_wire_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, request, record = _open_caller_delegated_host(tmp_path, monkeypatch)
    try:
        first_claim, first_fence = _claim(host, "finalizer-owner-first")
        initial = host.rlm_workbench.run_claimed(
            record.operation,
            first_claim.attempt,
            first_fence,
        )
        assert initial.phase == "waiting_external"
        first_ticket_id = wait_for_pending_ticket(host.registry.database_path)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(first_ticket_id)
        directive = {
            "kind": "execute_cell",
            "code": "answer = 42\nanswer",
            "expected_result_hint": "42",
        }
        settled, evidence = _commit_planner_ticket(
            host,
            request,
            pending,
            directive,
            prefix="finalizer-owner",
        )
        assert settled.settled_receipt_digest is not None
        with sqlite3.connect(host.registry.database_path) as connection:
            connection.row_factory = sqlite3.Row
            receipt_row = connection.execute(
                "SELECT receipt_json FROM caller_work_candidate_receipts "
                "WHERE ticket_id = ? AND receipt_digest = ?",
                (first_ticket_id, settled.settled_receipt_digest),
            ).fetchone()
            journal_row = connection.execute(
                "SELECT request_digest, binding_json, response_digest, response_json, usage_json "
                "FROM model_executions WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()
        assert receipt_row is not None and journal_row is not None
        observation = json.loads(str(receipt_row[0]))["observation"]
        assert all(
            isinstance(observation[name], str)
            for name in (
                "route_receipt_digest",
                "usage_receipt_digest",
                "host_receipt_digest",
                "output_digest",
            )
        )
        assert observation["route_receipt_digest"] == evidence["route_receipt_digest"]
        assert observation["usage_receipt_digest"] == evidence["usage_receipt_digest"]
        assert observation["host_receipt_digest"] == evidence["host_receipt_digest"]
        assert journal_row["request_digest"] == pending.request_digest
        assert (
            json.loads(str(journal_row["binding_json"]))
            == request.root["spec"]["model"]["route_binding"]
        )
        assert journal_row["response_digest"] == evidence["host_receipt_digest"]
        assert json.loads(str(journal_row["response_json"]))["usage"]["cache_read_tokens"] is None
        assert json.loads(str(journal_row["usage_json"]))["cache_write_tokens"] is None

        successor, successor_fence = _claim(host, "finalizer-owner-successor")
        monkeypatch.setattr(host.rlm_workbench, "_execute_cell", lambda *_args, **_kwargs: False)
        waiting = host.rlm_workbench.run_claimed(
            record.operation,
            successor.attempt,
            successor_fence,
        )
        assert waiting.phase == "waiting_external"
        with sqlite3.connect(host.registry.database_path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                """
                SELECT logical_owner_json, cell_execution_id
                FROM rlm_workbench_suspensions
                WHERE operation_id = ? ORDER BY suspension_revision DESC LIMIT 1
                """,
                (record.operation.value,),
            ).fetchone()
        assert row is not None
        finalizer_owner = {"kind": "planner", "phase": "finalizer", "step_index": 1}
        assert json.loads(str(row["logical_owner_json"])) == finalizer_owner
        finalizer_ticket = repository.get(
            str(
                host.registry._connection.execute(
                    "SELECT ticket_id FROM rlm_workbench_suspensions "
                    "WHERE operation_id = ? ORDER BY suspension_revision DESC LIMIT 1",
                    (record.operation.value,),
                ).fetchone()[0]
            )
        )
        prompt = json.loads(str(finalizer_ticket.root["request"]["prompt"]))
        assert finalizer_ticket.root["owner"] == finalizer_owner
        assert prompt["planner_owner"] == finalizer_owner
        assert (
            finalizer_ticket.root["request"]["route_binding"]
            == request.root["spec"]["model"]["route_binding"]
        )
        assert row["cell_execution_id"] is None
    finally:
        host.close()


def test_planner_phases_do_not_touch_cell_bound_authority_tables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, _request, record = _open_caller_delegated_host(tmp_path, monkeypatch)
    actions = _install_sql_audit(monkeypatch, host)
    try:
        first_claim, first_fence = _claim(host, "sql-denylist-first")
        actions.clear()
        first = host.rlm_workbench.run_claimed(record.operation, first_claim.attempt, first_fence)
        assert first.phase == "waiting_external"
        assert not any(table in _CELL_BOUND_AUTHORITY_TABLES for _, table, _ in actions)
        assert not any(
            table == "rlm_workbench_cells" and action in _WRITE_ACTIONS
            for action, table, _ in actions
        )

        ticket_id = wait_for_pending_ticket(host.registry.database_path)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)
        _commit_planner_ticket(
            host,
            _request,
            pending,
            {
                "kind": "execute_cell",
                "code": "answer = 42\nanswer",
                "expected_result_hint": "42",
            },
            prefix="sql-denylist",
        )
        successor, successor_fence = _claim(host, "sql-denylist-successor")
        monkeypatch.setattr(host.rlm_workbench, "_execute_cell", lambda *_args, **_kwargs: False)
        original_consume = host.rlm_workbench._consume_root_planner

        def consume_then_audit(*args: Any, **kwargs: Any) -> Any:
            result = original_consume(*args, **kwargs)
            # Cell projection is deliberately allowed before the root
            # finalizer; audit only the finalizer planner phase itself.
            actions.clear()
            return result

        monkeypatch.setattr(host.rlm_workbench, "_consume_root_planner", consume_then_audit)
        second = host.rlm_workbench.run_claimed(
            record.operation,
            successor.attempt,
            successor_fence,
        )
        assert second.phase == "waiting_external"
        assert not any(table in _CELL_BOUND_AUTHORITY_TABLES for _, table, _ in actions)
        assert not any(
            table == "rlm_workbench_cells" and action in _WRITE_ACTIONS
            for action, table, _ in actions
        )
    finally:
        host.registry._connection.set_authorizer(None)
        host.close()


def test_live_owner_takeover_refuses_without_advancing_prepared_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    host, request, record = _open_caller_delegated_host(tmp_path, monkeypatch)
    try:
        first_claim, first_fence = _claim(host, "live-owner-first")
        host.rlm_workbench.run_claimed(record.operation, first_claim.attempt, first_fence)
        ticket_id = wait_for_pending_ticket(host.registry.database_path)
        repository = host.caller_work
        assert repository is not None
        _commit_planner_ticket(
            host,
            request,
            repository.get(ticket_id),
            {
                "kind": "finalize",
                "output": {"answer": "live-owner", "artifacts": []},
                "artifact_stage_ids": [],
            },
            prefix="live-owner",
        )
        live_claim, _ = _claim(host, "live-owner-prepared")
        before = host.registry._connection.execute(
            "SELECT state, rebind_generation, successor_attempt_id "
            "FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()
        assert before is not None and before["state"] == "prepared"
        before_attempts = host.registry._connection.execute(
            "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()[0]
        with host.registry._connection:
            host.registry._connection.execute(
                "UPDATE operations SET state = 'accepted' WHERE operation_id = ?",
                (record.operation.value,),
            )
            host.registry._connection.execute(
                "UPDATE operation_dispatch SET state = 'queued' WHERE operation_id = ?",
                (record.operation.value,),
            )
        with pytest.raises(StaleAttemptFence, match="owner is still live"):
            _claim(host, "live-owner-competitor")
        after = host.registry._connection.execute(
            "SELECT state, rebind_generation, successor_attempt_id "
            "FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
            (record.operation.value,),
        ).fetchone()
        assert tuple(after) == tuple(before)
        assert (
            host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_attempts WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone()[0]
            == before_attempts
        )
        assert live_claim.attempt.attempt_no == 2
    finally:
        host.close()


@pytest.mark.parametrize(
    "evidence_axis",
    ("route", "usage", "attempt", "response", "budget", "control", "deadline", "fence"),
)
def test_receipt_evidence_one_axis_mutants_do_not_consume_successor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    evidence_axis: str,
) -> None:
    host, request, record = _open_caller_delegated_host(tmp_path, monkeypatch)
    try:
        first_claim, first_fence = _claim(host, f"evidence-{evidence_axis}-first")
        host.rlm_workbench.run_claimed(record.operation, first_claim.attempt, first_fence)
        ticket_id = wait_for_pending_ticket(host.registry.database_path)
        repository = host.caller_work
        assert repository is not None
        pending = repository.get(ticket_id)
        settled, _ = _commit_planner_ticket(
            host,
            request,
            pending,
            {
                "kind": "finalize",
                "output": {"answer": "evidence", "artifacts": []},
                "artifact_stage_ids": [],
            },
            prefix=f"evidence-{evidence_axis}",
        )
        successor, fence = _claim(host, f"evidence-{evidence_axis}-successor")
        execution_key = str(settled.root["physical_attempt"]["provider_or_child_idempotency_key"])
        expected_error = "planner model journal"
        if evidence_axis == "route":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE model_executions SET response_json = "
                    "json_set(response_json, '$.route_receipt.receipt_digest', ?) "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    ("sha256:" + "0" * 64, record.operation.value, execution_key),
                )
        elif evidence_axis == "usage":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE model_executions SET usage_json = "
                    "json_set(usage_json, '$.input_tokens', 8, '$.total_tokens', 11) "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    (record.operation.value, execution_key),
                )
        elif evidence_axis == "attempt":
            with host.registry._connection:
                candidate_row = host.registry._connection.execute(
                    "SELECT receipt_digest, receipt_json FROM caller_work_candidate_receipts "
                    "WHERE ticket_id = ? AND receipt_digest = ?",
                    (ticket_id, settled.settled_receipt_digest),
                ).fetchone()
                assert candidate_row is not None
                candidate = json.loads(str(candidate_row["receipt_json"]))
                candidate_payload = dict(candidate)
                candidate_payload.pop("receipt_digest")
                candidate_payload["callback_adapter_generation"] = 99
                candidate_digest = canonical_sha256(candidate_payload)
                candidate_payload["receipt_digest"] = candidate_digest
                host.registry._connection.execute(
                    "UPDATE caller_work_candidate_receipts "
                    "SET receipt_digest = ?, receipt_json = ? "
                    "WHERE ticket_id = ? AND receipt_digest = ?",
                    (
                        candidate_digest,
                        canonical_json_bytes(candidate_payload).decode("utf-8"),
                        ticket_id,
                        str(candidate_row["receipt_digest"]),
                    ),
                )
                host.registry._connection.execute(
                    "UPDATE caller_work_tickets SET settled_receipt_digest = ? WHERE ticket_id = ?",
                    (candidate_digest, ticket_id),
                )
                host.registry._connection.execute(
                    "UPDATE rlm_workbench_successor_outbox SET settlement_digest = ? "
                    "WHERE operation_id = ? AND suspension_revision = 1",
                    (candidate_digest, record.operation.value),
                )
            expected_error = "planner candidate lineage"
        elif evidence_axis == "response":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE model_executions SET response_digest = ? "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    ("sha256:" + "0" * 64, record.operation.value, execution_key),
                )
        elif evidence_axis == "budget":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE model_executions SET request_json = "
                    "json_set(request_json, '$.max_output_bytes', 1) "
                    "WHERE operation_id = ? AND idempotency_key = ?",
                    (record.operation.value, execution_key),
                )
        elif evidence_axis == "control":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE operation_controls SET control_revision = control_revision + 1 "
                    "WHERE operation_id = ?",
                    (record.operation.value,),
                )
            expected_error = "authority is stale"
        elif evidence_axis == "deadline":
            with host.registry._connection:
                host.registry._connection.execute(
                    "UPDATE rlm_workbench_jobs SET cumulative_deadline_unix_ms = ? "
                    "WHERE operation_id = ?",
                    (FIXED_NOW_MS - 1, record.operation.value),
                )
        else:
            fence = AttemptFence(
                dispatcher_generation=fence.dispatcher_generation,
                lease_epoch=fence.lease_epoch,
                owner_digest=canonical_sha256("wrong-fence-owner"),
            )

        if evidence_axis == "deadline":
            terminal = host.rlm_workbench._consume_root_planner(successor.attempt, fence)
            assert terminal.phase == "timed_out"
        else:
            error_type = (
                StaleAttemptFence if evidence_axis in {"control", "fence"} else InvalidTransition
            )
            error_match = None if evidence_axis == "fence" else expected_error
            with pytest.raises(error_type, match=error_match):
                host.rlm_workbench._consume_root_planner(successor.attempt, fence)
        assert (
            host.registry._connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_successor_consumed'",
                (record.operation.value,),
            ).fetchone()[0]
            == 0
        )
    finally:
        host.close()


def test_optional_usage_dimensions_remain_null_without_zero_imputation(tmp_path: Path) -> None:
    repository = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    try:
        reserved = repository.claim(claim_input(repository.get(TICKET_ID)))
        started = repository.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="usage-null-send")
        )
        settled = repository.commit(commit_input(started, idempotency_key="usage-null-commit"))
        assert settled.state == "settled_success"
        with sqlite3.connect(repository.database_path) as connection:
            row = connection.execute(
                "SELECT usage_json, response_json FROM model_executions "
                "WHERE operation_id = ? AND idempotency_key = ?",
                (OPERATION_ID, started.physical_attempt.provider_or_child_idempotency_key),
            ).fetchone()
        assert row is not None
        usage = json.loads(str(row[0]))
        response_usage = json.loads(str(row[1]))["usage"]
        assert usage["input_tokens"] == 7
        assert usage["output_tokens"] == 3
        assert usage["total_tokens"] == 10
        for name in ("cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
            assert usage[name] is None
            assert response_usage[name] is None
    finally:
        repository.close()


def test_outcome_unknown_reconcile_is_lookup_only_and_creates_no_successor(
    tmp_path: Path,
) -> None:
    repository = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))

    class LookupOnly:
        def lookup(self, *, idempotency_key: str) -> None:
            del idempotency_key
            return None

    dispatcher = CallerWorkDispatcher(repository, LookupOnly())
    try:
        reserved = repository.claim(claim_input(repository.get(TICKET_ID)))
        started = repository.mark_send_started(
            mark_send_started_input(reserved, idempotency_key="reconcile-only-send")
        )
        unknown = dispatcher.resume(TICKET_ID)
        assert unknown.state == "outcome_unknown"
        inspected = repository.reconcile(
            reconcile_input(
                unknown,
                action="lookup",
                idempotency_key="reconcile-only-lookup",
            )
        )
        assert inspected.state == "outcome_unknown"
        assert inspected.revision == unknown.revision
        assert repository.send_started_count(TICKET_ID) == 1
        assert started.physical_attempt is not None
        with sqlite3.connect(repository.database_path) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox"
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_candidate_receipts"
            ).fetchone() == (0,)
    finally:
        repository.close()


def test_failed_source_recovery_refuses_replay_before_python_execution(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    failed_directive = {
        "kind": "execute_cell",
        "code": "raise RuntimeError('failed source')",
        "expected_result_hint": "failed",
    }
    failed_digest = canonical_sha256(failed_directive)

    class ReplayPlanner:
        def __init__(self) -> None:
            self.recover_calls = 0
            self.executed = 0

        def plan(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("recovery discriminator must not request ordinary planning")

        def recover(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
            self.recover_calls += 1
            return dict(failed_directive)

    planner = ReplayPlanner()
    host = reference_host_module.ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=False,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        claim, fence = _claim(host, "failed-source-replay")
        planner_input = RecoveryPlannerInput.model_validate(
            {
                "schema_version": "aar.rlm-recovery-planner-input.v1",
                "operation": record.operation.model_dump(mode="json"),
                "failed_cell_execution_id": "cell-failed-source",
                "failed_cell_source_digest": failed_digest,
                "pre_cell_checkpoint_digest": "sha256:" + "1" * 64,
                "suspension_revision": 1,
                "settled_ticket_receipt_digests": ["sha256:" + "2" * 64],
                "committed_artifact_binding_digests": [],
                "committed_event_digests": [],
                "failure_code": "supervisor_restarted",
                "remaining_model_calls": 2,
                "remaining_wall_time_ms": 60_000,
            },
            strict=True,
        )
        continuation = _RecoveryContinuation(
            planner_input=planner_input,
            consumed_model_calls=0,
            failed_source_digest=failed_digest,
        )
        coordinator = host.rlm_workbench
        assert coordinator is not None
        coordinator._prepare_recovery_continuation = (  # type: ignore[method-assign]
            lambda *_args, **_kwargs: continuation
        )

        def execute_must_not_run(*_args: object, **_kwargs: object) -> bool:
            planner.executed += 1
            raise AssertionError("failed source must be reconcile-only, not executed")

        coordinator._execute_cell = execute_must_not_run  # type: ignore[method-assign]
        with pytest.raises(RlmWorkbenchConflict, match="replay the failed cell"):
            coordinator.run_claimed(record.operation, claim.attempt, fence)
        assert planner.recover_calls == 1
        assert planner.executed == 0
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox WHERE operation_id = ?",
                (record.operation.value,),
            ).fetchone() == (0,)
    finally:
        host.close()


@pytest.mark.parametrize("owner", ("initial", "correction", "recovery", "finalizer"))
def test_planner_owner_wire_has_no_ordinal_alias(owner: str) -> None:
    index = {"initial": 0, "correction": 1, "recovery": 0, "finalizer": 1}[owner]
    value = PlannerOwner(phase=owner, step_index=index).as_wire()
    assert value == {"kind": "planner", "phase": owner, "step_index": index}
    assert canonical_json_bytes(value).decode("utf-8") == json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert "ordinal" not in value
