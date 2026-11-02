from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    caller_common_input,
    digest_bytes,
    model_commit_evidence,
    prepare_v6_registry,
    wait_for_pending_ticket,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.rlm_workbench as rlm_workbench_module
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import RlmWorkbenchExecuteInput
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.workspace_models import WorkspaceProgramSpec


class _BlockingFinalizePlanner:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def plan(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release the planner")
        return {
            "kind": "finalize",
            "output": {"answer": "done", "artifacts": []},
            "artifact_stage_ids": [],
        }


class _ExecuteCellPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        return {
            "kind": "execute_cell",
            "code": "answer = 6 * 7\nanswer",
            "expected_result_hint": "42",
        }


class _MustNotRunPlanner:
    def plan(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("caller-delegated execution must not call this planner")


def _request_with_corrections(
    host: ReferenceHost,
    *,
    max_model_calls: int,
    max_corrections: int,
) -> RlmWorkbenchExecuteInput:
    request = workbench_request(
        host,
        require_named_artifacts=False,
        execution_mode="caller_delegated",
        max_model_calls=max_model_calls,
    )
    document = copy.deepcopy(request.root)
    document["spec"]["model"]["max_corrections"] = max_corrections
    return RlmWorkbenchExecuteInput.model_validate(document, strict=True)


def _settle_ticket(
    host: ReferenceHost,
    database: Path,
    request: RlmWorkbenchExecuteInput,
    ticket_id: str,
    *,
    output_text: str,
    key_prefix: str,
) -> Any:
    repository = host.caller_work
    assert repository is not None
    pending = repository.get(ticket_id)
    claimed = repository.claim(
        {
            **caller_common_input(
                database,
                request,
                pending,
                idempotency_key=f"{key_prefix}-claim",
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
                database,
                request,
                claimed,
                idempotency_key=f"{key_prefix}-send-start",
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
    evidence, model_response = model_commit_evidence(started, output_text)
    return repository.commit(
        {
            **caller_common_input(
                database,
                request,
                started,
                idempotency_key=f"{key_prefix}-commit",
            ),
            "claim_id": started.claimant.claim_id,
            "claim_fence": started.claimant.claim_fence,
            "physical_attempt_id": started.physical_attempt.physical_attempt_id,
            "sent_request_digest": started.request_digest,
            "sent_at_unix_ms": FIXED_NOW_MS + 2,
            "provider_or_child_request_id": f"provider-{key_prefix}",
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


def test_workbench_attempt_lease_renews_under_same_fence_during_local_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    clock = [FIXED_NOW_MS]
    planner = _BlockingFinalizePlanner()
    host = ReferenceHost(database, now_ms=lambda: clock[0], workbench_planner=planner)
    monkeypatch.setattr(
        rlm_workbench_module,
        "_WORKBENCH_ATTEMPT_LEASE_HEARTBEAT_INTERVAL_MS",
        5,
    )
    heartbeat_before_expiry = threading.Event()
    heartbeat_after_expiry = threading.Event()
    heartbeat_count = 0
    heartbeat_lock = threading.Lock()
    original_heartbeat = host.registry.heartbeat
    try:
        request = workbench_request(host, require_named_artifacts=False, max_model_calls=1)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=canonical_sha256("short-lease-workbench-owner"),
            lease_duration_ms=40,
        )
        assert claim is not None
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=claim.owner_digest,
        )
        with sqlite3.connect(database) as connection:
            original_expiry = int(
                connection.execute(
                    "SELECT expires_at_unix_ms FROM operation_leases "
                    "WHERE operation_id = ? AND attempt_no = ?",
                    (admitted.operation.value, claim.attempt.attempt_no),
                ).fetchone()[0]
            )

        def observe_heartbeat(*args: Any, **kwargs: Any) -> Any:
            nonlocal heartbeat_count
            lease = original_heartbeat(*args, **kwargs)
            with heartbeat_lock:
                heartbeat_count += 1
                observed_count = heartbeat_count
            if observed_count == 1:
                heartbeat_before_expiry.set()
            if clock[0] > original_expiry:
                heartbeat_after_expiry.set()
            return lease

        monkeypatch.setattr(host.registry, "heartbeat", observe_heartbeat)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result_future = executor.submit(
                host.run_claimed,
                "rlm.workbench.execute",
                admitted.operation,
                claim.attempt,
                fence,
            )
            assert planner.entered.wait(timeout=5)
            assert heartbeat_before_expiry.wait(timeout=3)
            clock[0] = original_expiry + 1
            assert heartbeat_after_expiry.wait(timeout=3)
            with sqlite3.connect(database) as connection:
                renewed = connection.execute(
                    "SELECT lease_epoch, owner_digest, expires_at_unix_ms, released_at_unix_ms "
                    "FROM operation_leases WHERE operation_id = ? AND attempt_no = ?",
                    (admitted.operation.value, claim.attempt.attempt_no),
                ).fetchone()
            assert renewed is not None
            assert renewed[0] == fence.lease_epoch
            assert renewed[1] == fence.owner_digest
            assert renewed[2] > clock[0]
            assert renewed[3] is None

            planner.release.set()
            terminal = result_future.result(timeout=10)

        assert terminal.state.value == "succeeded"
        assert planner.calls == 1
        assert host.rlm_workbench is not None
        assert host.rlm_workbench.snapshot(admitted.operation).phase == "succeeded"
        with heartbeat_lock:
            count_after_return = heartbeat_count
        time.sleep(0.02)
        with heartbeat_lock:
            assert heartbeat_count == count_after_return
    finally:
        planner.release.set()
        host.close()


def test_cell_wall_time_uses_requested_budget_and_only_clips_to_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        request = workbench_request(host, require_named_artifacts=False)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        coordinator = host.rlm_workbench
        assert coordinator is not None
        program_spec = WorkspaceProgramSpec(
            code="answer = 42",
            wall_time_ms=120_000,
            max_output_chars=262_144,
            max_events=512,
        )
        assert program_spec.wall_time_ms == 120_000
        monkeypatch.setattr(
            coordinator,
            "_deadline",
            lambda _operation: FIXED_NOW_MS + 150_000,
        )
        assert coordinator._remaining_wall_time_ms(FIXED_NOW_MS + 900_001) == 900_001
        assert coordinator._cell_execution_wall_time_ms(admitted.operation, 120_000) == 120_000
        assert coordinator._cell_execution_wall_time_ms(admitted.operation, 200_000) == 150_000
    finally:
        host.close()


def test_service_managed_root_budget_blocks_successor_planner_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    planner = _ExecuteCellPlanner()
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False, max_model_calls=1)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=20)
        snapshot = host.rlm_workbench.snapshot(admitted.operation)  # type: ignore[union-attr]
        assert terminal.state.value == "failed"
        assert snapshot.phase == "failed"
        assert snapshot.failure.code == "BUDGET_EXCEEDED"
        assert terminal.failure is not None and terminal.failure.code == "BUDGET_EXCEEDED"
        assert planner.calls == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (1,)
    finally:
        host.close()


def test_caller_delegated_root_budget_prevents_an_extra_successor_ticket(
    tmp_path: Path,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=_MustNotRunPlanner(),
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
            max_model_calls=1,
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        first_ticket = wait_for_pending_ticket(database)
        directive = {
            "kind": "execute_cell",
            "code": "answer = 6 * 7\nanswer",
            "expected_result_hint": "42",
        }
        settled = _settle_ticket(
            host,
            database,
            request,
            first_ticket,
            output_text=canonical_json_bytes(directive).decode("utf-8"),
            key_prefix="single-call-root",
        )
        assert settled.state == "settled_success"
        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=20)
        assert terminal.state.value == "failed"
        assert terminal.failure is not None and terminal.failure.code == "BUDGET_EXCEEDED"
        assert host.rlm_workbench is not None
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "failed"
        assert snapshot.failure.code == "BUDGET_EXCEEDED"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (1,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_cells WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (1,)
    finally:
        host.close()


@pytest.mark.parametrize(
    ("max_corrections", "expected_tickets"),
    ((0, 1), (1, 2)),
)
def test_invalid_directive_corrections_obey_zero_and_finite_limits(
    tmp_path: Path,
    max_corrections: int,
    expected_tickets: int,
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = _request_with_corrections(
            host,
            max_model_calls=4,
            max_corrections=max_corrections,
        )
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        initial_ticket = wait_for_pending_ticket(database)
        _settle_ticket(
            host,
            database,
            request,
            initial_ticket,
            output_text="not-json",
            key_prefix=f"correction-{max_corrections}-initial",
        )

        correction_ticket: str | None = None
        if max_corrections:
            correction_ticket = wait_for_pending_ticket(database)
            with sqlite3.connect(database) as connection:
                owner_json = connection.execute(
                    "SELECT logical_owner_json FROM rlm_workbench_suspensions "
                    "WHERE ticket_id = ?",
                    (correction_ticket,),
                ).fetchone()[0]
            assert json.loads(str(owner_json))["phase"] == "correction"
            _settle_ticket(
                host,
                database,
                request,
                correction_ticket,
                output_text="still-not-json",
                key_prefix=f"correction-{max_corrections}-second",
            )

        assert host.dispatcher is not None
        terminal = host.dispatcher.wait(admitted.operation, timeout_s=20)
        assert terminal.state.value == "failed"
        assert terminal.failure is not None and terminal.failure.code == "INVALID_ARGUMENT"
        assert host.rlm_workbench is not None
        snapshot = host.rlm_workbench.snapshot(admitted.operation)
        assert snapshot.phase == "failed"
        assert snapshot.failure.code == "INVALID_ARGUMENT"
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id = ?",
                (admitted.operation.value,),
            ).fetchone() == (expected_tickets,)
            assert connection.execute(
                "SELECT COUNT(*) FROM operation_events WHERE operation_id = ? "
                "AND event_kind = 'planner_correction_requested'",
                (admitted.operation.value,),
            ).fetchone() == (max_corrections,)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_suspensions "
                "WHERE operation_id = ? AND cell_execution_id IS NULL",
                (admitted.operation.value,),
            ).fetchone() == (expected_tickets,)
    finally:
        host.close()
