from __future__ import annotations

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from aar.canonical import canonical_sha256
from aar.mcp.public_rlm import (
    PublicRlmCapacityExceeded,
    PublicRlmConflict,
    PublicRlmCoordinator,
    PublicRlmNotFound,
)
from aar.mcp.public_rlm_models import (
    PublicRlmClaimRequest,
    PublicRlmCommitRequest,
    PublicRlmJobSpec,
    PublicRlmJobView,
)
from aar.schemas import OutcomeCertainty


class _Clock:
    def __init__(self) -> None:
        self.value = 1_700_000_000_000

    def __call__(self) -> int:
        self.value += 1
        return self.value


def _spec(
    *,
    query: str = "Explain the caller-delegated boundary.",
    strategy: str = "single_call",
    max_model_calls: int | None = None,
    max_result_bytes: int = 1024,
    executor_kind: str = "codex-host",
    model: str = "model-a",
    effort: str | None = "high",
) -> PublicRlmJobSpec:
    if max_model_calls is None:
        max_model_calls = 1 if strategy == "single_call" else 2
    return PublicRlmJobSpec(
        query=query,
        strategy=strategy,
        executor_kind=executor_kind,
        requested_model=model,
        requested_reasoning_effort=effort,
        max_model_calls=max_model_calls,
        max_output_tokens_per_call=256,
        max_result_bytes_per_call=max_result_bytes,
    )


def _coordinator(
    database: Path,
    *,
    principal: str = "principal-a",
    max_jobs: int = 256,
) -> PublicRlmCoordinator:
    return PublicRlmCoordinator(
        database,
        principal_id=principal,
        now_ms=_Clock(),
        max_jobs=max_jobs,
    )


def _claim_request(
    job: PublicRlmJobView,
    *,
    key: str = "claim-1",
    expected_revision: int | None = None,
    call_spec_digest: str | None = None,
) -> PublicRlmClaimRequest:
    assert job.pending_call is not None
    return PublicRlmClaimRequest(
        job_id=job.job_id,
        call_id=job.pending_call.call_id,
        expected_revision=(
            job.revision if expected_revision is None else expected_revision
        ),
        call_spec_digest=call_spec_digest or job.pending_call.spec_digest,
        idempotency_key=key,
    )


def _commit_request(
    job: PublicRlmJobView,
    *,
    output: str | None = "bounded answer",
    outcome: str = "succeeded",
    key: str = "commit-1",
    effective_model: str | None = None,
    effective_effort: str | None = None,
    output_tokens: int | None = 8,
    failure_code: str | None = None,
    failure_message: str | None = None,
    ticket_digest: str | None = None,
    host_receipt: bool = False,
) -> PublicRlmCommitRequest:
    assert job.active_ticket is not None
    ticket = job.active_ticket
    if effective_model is None and outcome == "succeeded":
        effective_model = ticket.requested_model
    if effective_effort is None and outcome == "succeeded":
        effective_effort = ticket.requested_reasoning_effort
    return PublicRlmCommitRequest(
        job_id=job.job_id,
        call_id=ticket.call_id,
        expected_revision=job.revision,
        ticket_digest=ticket_digest or ticket.ticket_digest,
        outcome=outcome,
        output_text=output,
        effective_model=effective_model,
        effective_reasoning_effort=effective_effort,
        input_tokens=12,
        output_tokens=output_tokens,
        failure_code=failure_code,
        failure_message=failure_message,
        host_receipt_id="host-receipt-1" if host_receipt else None,
        host_receipt_digest=(
            canonical_sha256("host receipt bytes") if host_receipt else None
        ),
        idempotency_key=key,
    )


def _start_claimed(
    coordinator: PublicRlmCoordinator,
    *,
    key: str,
    spec: PublicRlmJobSpec | None = None,
) -> PublicRlmJobView:
    started = coordinator.start(spec or _spec(), idempotency_key=key)
    assert started.job is not None
    claimed = coordinator.claim(
        _claim_request(
            started.job,
            key=f"claim-{key}",
        )
    )
    assert claimed.job is not None
    return claimed.job


def test_single_call_replay_conflict_restart_and_host_receipt(tmp_path: Path) -> None:
    database = tmp_path / "public-rlm.sqlite3"
    coordinator = _coordinator(database)
    spec = _spec()
    try:
        started = coordinator.start(spec, idempotency_key="start-1")
        replayed_start = coordinator.start(spec, idempotency_key="start-1")
        assert replayed_start == started
        assert started.job is not None
        with pytest.raises(PublicRlmConflict):
            coordinator.start(
                _spec(query="different bytes"), idempotency_key="start-1"
            )

        claim_request = _claim_request(started.job)
        claimed = coordinator.claim(claim_request)
        assert claimed.job is not None
        assert coordinator.claim(claim_request) == claimed
        assert coordinator.start(spec, idempotency_key="start-1") == claimed
        with pytest.raises(PublicRlmConflict):
            coordinator.claim(claim_request.model_copy(update={"expected_revision": 1}))
        ticket = claimed.job.active_ticket
        assert ticket is not None
    finally:
        coordinator.close()

    reopened = _coordinator(database)
    try:
        recovered = reopened.status(started.job.job_id)
        assert recovered.job is not None
        assert recovered.job.active_ticket == ticket
        commit_request = _commit_request(recovered.job, host_receipt=True)
        committed = reopened.commit(commit_request)
        assert committed.job is not None
        assert committed.job.phase == "succeeded"
        assert committed.job.revision == 2
        assert committed.job.result is not None
        assert committed.job.result.answer == "bounded answer"
        assert committed.job.steps[0].provenance_assurance == "host_receipt_bound"
        assert reopened.commit(commit_request) == committed
        assert reopened.start(spec, idempotency_key="start-1") == committed
        assert reopened.claim(claim_request) == committed
        with pytest.raises(PublicRlmConflict):
            reopened.commit(
                commit_request.model_copy(update={"output_text": "different result"})
            )
    finally:
        reopened.close()


def test_terminal_commit_rejects_fresh_keys_and_stores_only_a_bounded_marker(
    tmp_path: Path,
) -> None:
    database = tmp_path / "terminal-commit.sqlite3"
    coordinator = _coordinator(database, max_jobs=1)
    try:
        claimed = _start_claimed(
            coordinator,
            key="terminal-commit",
            spec=_spec(max_result_bytes=8192),
        )
        request = _commit_request(
            claimed,
            output="bounded answer " * 256,
            key="commit-original",
        )
        committed = coordinator.commit(request)
        assert committed.job is not None
        assert committed.job.phase == "succeeded"
        assert coordinator.commit(request) == committed

        with pytest.raises(PublicRlmConflict, match="original idempotency key"):
            coordinator.commit(
                request.model_copy(update={"idempotency_key": "commit-fresh"})
            )

        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                """
                SELECT command_kind, idempotency_key, response_json
                FROM public_rlm_commands
                WHERE job_id = ? ORDER BY command_kind
                """,
                (claimed.job_id,),
            ).fetchall()
        assert [(row[0], row[1]) for row in rows] == [
            ("claim", "claim-terminal-commit"),
            ("commit", "commit-original"),
        ]
        for _kind, _key, response_json in rows:
            marker = json.loads(response_json)
            assert marker["schema_version"] == "aar.public-rlm-command-response.v1"
            assert marker["response_digest"].startswith("sha256:")
            assert len(response_json.encode("utf-8")) < 256
            assert "bounded answer" not in response_json
    finally:
        coordinator.close()


def test_terminal_cancel_rejects_fresh_keys_without_command_growth(
    tmp_path: Path,
) -> None:
    database = tmp_path / "terminal-cancel.sqlite3"
    coordinator = _coordinator(database, max_jobs=1)
    try:
        started = coordinator.start(_spec(), idempotency_key="terminal-cancel")
        assert started.job is not None
        cancelled = coordinator.cancel(
            started.job.job_id,
            expected_revision=started.job.revision,
            idempotency_key="cancel-original",
        )
        assert cancelled.job is not None
        assert cancelled.job.phase == "cancelled"
        assert coordinator.cancel(
            started.job.job_id,
            expected_revision=started.job.revision,
            idempotency_key="cancel-original",
        ) == cancelled

        with pytest.raises(PublicRlmConflict, match="original idempotency key"):
            coordinator.cancel(
                started.job.job_id,
                expected_revision=cancelled.job.revision,
                idempotency_key="cancel-fresh",
            )
        with pytest.raises(PublicRlmConflict, match="different request bytes"):
            coordinator.cancel(
                started.job.job_id,
                expected_revision=cancelled.job.revision,
                idempotency_key="cancel-original",
            )

        with sqlite3.connect(database) as connection:
            rows = connection.execute(
                """
                SELECT idempotency_key, response_json
                FROM public_rlm_commands
                WHERE job_id = ? AND command_kind = 'cancel'
                """,
                (started.job.job_id,),
            ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "cancel-original"
        assert len(rows[0][1].encode("utf-8")) < 256
    finally:
        coordinator.close()


def test_command_history_cap_rolls_back_the_state_transition(tmp_path: Path) -> None:
    database = tmp_path / "command-cap.sqlite3"
    coordinator = _coordinator(database, max_jobs=1)
    started = coordinator.start(_spec(), idempotency_key="command-cap")
    assert started.job is not None
    coordinator.close()

    with sqlite3.connect(database) as connection:
        connection.executemany(
            """
            INSERT INTO public_rlm_commands(
                job_id, command_kind, idempotency_key, request_digest,
                response_json, created_at_unix_ms
            ) VALUES (?, ?, ?, ?, '{}', ?)
            """,
            [
                (
                    started.job.job_id,
                    "fault-injection",
                    f"preexisting-{index}",
                    canonical_sha256({"index": index}),
                    index,
                )
                for index in range(3)
            ],
        )

    reopened = _coordinator(database, max_jobs=1)
    try:
        with pytest.raises(PublicRlmCapacityExceeded, match="command history"):
            reopened.claim(_claim_request(started.job, key="claim-after-cap"))
        current = reopened.status(started.job.job_id)
        assert current.job == started.job
        with sqlite3.connect(database) as connection:
            row_count = connection.execute(
                "SELECT COUNT(*) FROM public_rlm_commands WHERE job_id = ?",
                (started.job.job_id,),
            ).fetchone()[0]
        assert row_count == 3
    finally:
        reopened.close()


def test_iterative_job_uses_one_fixed_model_and_effort_for_every_step(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path / "iterative.sqlite3")
    try:
        first = _start_claimed(
            coordinator,
            key="iterative",
            spec=_spec(
                strategy="iterative_refinement",
                max_model_calls=2,
                model="model-fixed",
                effort="medium",
            ),
        )
        first_commit = coordinator.commit(
            _commit_request(
                first,
                output="draft answer",
                key="commit-draft",
                effective_model="model-fixed",
                effective_effort="medium",
            )
        )
        assert first_commit.job is not None
        assert first_commit.job.phase == "pending_model_call"
        assert first_commit.job.pending_call is not None
        assert "draft answer" in first_commit.job.pending_call.prompt

        second = coordinator.claim(
            _claim_request(
                first_commit.job,
                key="claim-review",
            )
        )
        assert second.job is not None
        completed = coordinator.commit(
            _commit_request(
                second.job,
                output="final answer",
                key="commit-review",
                effective_model="model-fixed",
                effective_effort="medium",
            )
        )
        assert completed.job is not None
        assert completed.job.phase == "succeeded"
        assert completed.job.result is not None
        assert completed.job.result.answer == "final answer"
        assert [step.requested_model for step in completed.job.steps] == [
            "model-fixed",
            "model-fixed",
        ]
        assert [step.requested_reasoning_effort for step in completed.job.steps] == [
            "medium",
            "medium",
        ]
    finally:
        coordinator.close()


@pytest.mark.parametrize(
    ("case", "commit_updates", "failure_code", "certainty"),
    (
        (
            "route",
            {"effective_model": "different-model"},
            "RLM_ROUTE_MISMATCH",
            OutcomeCertainty.CERTAIN,
        ),
        (
            "usage",
            {"output_tokens": 257},
            "RLM_REPORTED_USAGE_EXCEEDS_TICKET",
            OutcomeCertainty.CERTAIN,
        ),
        (
            "unknown",
            {
                "outcome": "outcome_unknown",
                "output": None,
                "effective_model": None,
                "effective_effort": None,
                "output_tokens": None,
                "failure_code": "HOST_OUTCOME_UNKNOWN",
            },
            "HOST_OUTCOME_UNKNOWN",
            OutcomeCertainty.INDETERMINATE,
        ),
    ),
)
def test_route_usage_and_unknown_outcomes_fail_closed(
    tmp_path: Path,
    case: str,
    commit_updates: dict[str, object],
    failure_code: str,
    certainty: OutcomeCertainty,
) -> None:
    coordinator = _coordinator(tmp_path / f"{case}.sqlite3")
    try:
        claimed = _start_claimed(coordinator, key=case)
        request = _commit_request(claimed, key=f"commit-{case}", **commit_updates)
        result = coordinator.commit(request)
        assert result.job is not None
        assert result.job.phase == ("indeterminate" if case == "unknown" else "failed")
        assert result.job.failure_code == failure_code
        assert result.job.certainty is certainty
        assert result.job.result is None
        assert result.job.calls_committed == 0
        if case == "unknown":
            assert result.job.active_ticket == claimed.active_ticket
    finally:
        coordinator.close()


def test_oversized_result_retains_only_digest_and_size(tmp_path: Path) -> None:
    database = tmp_path / "oversized.sqlite3"
    coordinator = _coordinator(database)
    try:
        claimed = _start_claimed(
            coordinator,
            key="oversized",
            spec=_spec(max_result_bytes=4),
        )
        result = coordinator.commit(
            _commit_request(claimed, output="too large", key="commit-oversized")
        )
        assert result.job is not None
        assert result.job.phase == "failed"
        assert result.job.failure_code == "RLM_RESULT_TOO_LARGE"
        with sqlite3.connect(database) as connection:
            stored = connection.execute(
                "SELECT caller_receipt_json FROM public_rlm_calls WHERE job_id = ?",
                (claimed.job_id,),
            ).fetchone()
        assert stored is not None
        receipt = json.loads(stored[0])
        assert receipt["output_text"] is None
        assert receipt["output_size_bytes"] == len(b"too large")
        assert receipt["output_digest"] == canonical_sha256("too large")
    finally:
        coordinator.close()


def test_cancel_before_and_after_claim_never_creates_a_successor(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path / "cancel.sqlite3")
    try:
        pending = coordinator.start(_spec(), idempotency_key="cancel-pending")
        assert pending.job is not None
        cancelled = coordinator.cancel(
            pending.job.job_id,
            expected_revision=0,
            idempotency_key="cancel-before-claim",
        )
        assert cancelled.job is not None
        assert cancelled.job.phase == "cancelled"
        assert cancelled.job.certainty is OutcomeCertainty.CERTAIN

        claimed = _start_claimed(coordinator, key="cancel-claimed")
        requested = coordinator.cancel(
            claimed.job_id,
            expected_revision=claimed.revision,
            idempotency_key="cancel-after-claim",
        )
        assert requested.job is not None
        assert requested.job.phase == "cancel_requested"
        assert requested.job.certainty is OutcomeCertainty.INDETERMINATE
        assert requested.job.active_ticket == claimed.active_ticket

        observed = coordinator.commit(
            _commit_request(
                requested.job,
                output="late result",
                key="late-observation",
            )
        )
        assert observed.job is not None
        assert observed.job.phase == "cancel_requested"
        assert observed.job.certainty is OutcomeCertainty.INDETERMINATE
        assert observed.job.calls_committed == 0
        assert observed.job.pending_call is None
        assert observed.job.result is None
    finally:
        coordinator.close()


def test_wrong_revision_spec_and_ticket_are_rejected_without_state_change(
    tmp_path: Path,
) -> None:
    coordinator = _coordinator(tmp_path / "bindings.sqlite3")
    try:
        started = coordinator.start(_spec(), idempotency_key="bindings")
        assert started.job is not None
        with pytest.raises(PublicRlmConflict):
            coordinator.claim(
                _claim_request(started.job, expected_revision=1, key="bad-revision")
            )
        with pytest.raises(PublicRlmConflict):
            coordinator.claim(
                _claim_request(
                    started.job,
                    call_spec_digest=canonical_sha256("different spec"),
                    key="bad-spec",
                )
            )
        assert coordinator.status(started.job.job_id).job == started.job

        claimed_result = coordinator.claim(_claim_request(started.job, key="good-claim"))
        assert claimed_result.job is not None
        with pytest.raises(PublicRlmConflict):
            coordinator.commit(
                _commit_request(
                    claimed_result.job,
                    key="bad-ticket",
                    ticket_digest=canonical_sha256("different ticket"),
                )
            )
        assert coordinator.status(started.job.job_id).job == claimed_result.job
    finally:
        coordinator.close()


def test_concurrent_claim_issues_only_one_ticket_for_the_fixed_job_route(
    tmp_path: Path,
) -> None:
    database = tmp_path / "concurrent.sqlite3"
    first = _coordinator(database)
    second = _coordinator(database)
    try:
        started = first.start(_spec(), idempotency_key="concurrent")
        assert started.job is not None
        barrier = Barrier(2)

        def claim(coordinator: PublicRlmCoordinator, key: str):
            barrier.wait()
            try:
                return coordinator.claim(_claim_request(started.job, key=key))
            except PublicRlmConflict as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = tuple(
                executor.map(
                    lambda item: claim(*item),
                    (
                        (first, "claim-a"),
                        (second, "claim-b"),
                    ),
                )
            )
        successes = [item for item in outcomes if not isinstance(item, Exception)]
        conflicts = [item for item in outcomes if isinstance(item, PublicRlmConflict)]
        assert len(successes) == 1
        assert len(conflicts) == 1
        current = first.status(started.job.job_id)
        assert current.job is not None
        assert current.job.phase == "awaiting_caller_result"
        assert current.job.active_ticket is not None
        assert current.job.active_ticket.requested_model == "model-a"
    finally:
        second.close()
        first.close()


def test_tenant_binding_capacity_and_fixed_input_bounds(tmp_path: Path) -> None:
    database = tmp_path / "tenant.sqlite3"
    owner = _coordinator(database, max_jobs=1)
    try:
        started = owner.start(_spec(), idempotency_key="only-job")
        assert started.job is not None
        with pytest.raises(PublicRlmCapacityExceeded):
            owner.start(_spec(), idempotency_key="overflow")
        with pytest.raises(ValueError, match="UTF-8"):
            owner.start(_spec(query="界" * 6000), idempotency_key="utf8-overflow")
        with pytest.raises(ValueError, match="32768"):
            owner.start(
                _spec(
                    strategy="iterative_refinement",
                    max_model_calls=2,
                    max_result_bytes=32_769,
                ),
                idempotency_key="iterative-overflow",
            )
    finally:
        owner.close()

    foreign = _coordinator(database, principal="principal-b")
    try:
        with pytest.raises(PublicRlmNotFound):
            foreign.status(started.job.job_id)
    finally:
        foreign.close()


def test_invalid_transition_does_not_turn_unknown_into_retry(tmp_path: Path) -> None:
    coordinator = _coordinator(tmp_path / "unknown.sqlite3")
    try:
        claimed = _start_claimed(coordinator, key="unknown-terminal")
        unknown = coordinator.commit(
            _commit_request(
                claimed,
                outcome="outcome_unknown",
                output=None,
                effective_model=None,
                effective_effort=None,
                output_tokens=None,
                failure_code="HOST_OUTCOME_UNKNOWN",
                key="commit-unknown-terminal",
            )
        )
        assert unknown.job is not None
        with pytest.raises(PublicRlmConflict):
            coordinator.claim(
                PublicRlmClaimRequest(
                    job_id=unknown.job.job_id,
                    call_id=claimed.active_ticket.call_id,
                    expected_revision=unknown.job.revision,
                    call_spec_digest=claimed.active_ticket.call_spec_digest,
                    idempotency_key="forbidden-reclaim",
                )
            )
    finally:
        coordinator.close()
