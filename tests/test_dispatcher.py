from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

import pytest

from aar.runtime.dispatcher import (
    AttemptFence,
    DispatchClaim,
    DispatcherClosed,
    DurableDispatcher,
)
from aar.runtime.models import OperationRecord
from aar.schemas import OperationRef, OperationState, OutcomeCertainty


@dataclass(frozen=True, slots=True)
class FakeAttempt:
    operation: OperationRef
    attempt_no: int
    lease_epoch: int


def _record(
    operation: OperationRef,
    state: OperationState = OperationState.ACCEPTED,
    *,
    revision: int = 0,
    result_json: str | None = None,
) -> OperationRecord:
    uncertain = state is OperationState.INDETERMINATE
    return OperationRecord(
        operation=operation,
        host_value="fake-host",
        principal_value="fake-principal",
        idempotency_key=f"key-{operation.value}",
        input_digest="sha256:" + "0" * 64,
        state=state,
        certainty=OutcomeCertainty.INDETERMINATE if uncertain else OutcomeCertainty.CERTAIN,
        runtime_generation=1,
        record_revision=revision,
        reconciliation_required=uncertain,
        request_json="{}",
        payload_json="{}",
        result_json=result_json,
        created_at_unix_ms=1,
        updated_at_unix_ms=revision + 1,
    )


def _state(
    record: OperationRecord,
    state: OperationState,
    *,
    result_json: str | None = None,
) -> OperationRecord:
    uncertain = state is OperationState.INDETERMINATE
    return record.model_copy(
        update={
            "state": state,
            "certainty": OutcomeCertainty.INDETERMINATE if uncertain else OutcomeCertainty.CERTAIN,
            "record_revision": record.record_revision + 1,
            "reconciliation_required": uncertain,
            "result_json": result_json if result_json is not None else record.result_json,
            "updated_at_unix_ms": record.updated_at_unix_ms + 1,
        }
    )


class FakeRegistry:
    def __init__(self, operations: list[OperationRef]) -> None:
        self.records = {operation.value: _record(operation) for operation in operations}
        self.pending: deque[str] = deque()
        self.dispatch_requests: list[tuple[OperationRef, str]] = []
        self.claim_calls: list[dict[str, Any]] = []
        self.finish_calls: list[
            tuple[OperationRef, FakeAttempt, AttemptFence, OperationRecord | None]
        ] = []
        self.indeterminate_calls: list[tuple[OperationRef, int, str]] = []
        self.write_count = 0
        self._attempt_no = 0
        self._lock = threading.Lock()

    def request_dispatch(self, operation: OperationRef, *, kind: str) -> OperationRecord:
        with self._lock:
            self.dispatch_requests.append((operation, kind))
            if (
                self.records[operation.value].state is OperationState.ACCEPTED
                and operation.value not in self.pending
            ):
                self.pending.append(operation.value)
            self.write_count += 1
            return self.records[operation.value]

    def claim_next(
        self,
        *,
        runtime_generation: int,
        dispatcher_generation: int,
        owner_digest: str,
        lease_duration_ms: int,
    ) -> DispatchClaim | None:
        with self._lock:
            self.claim_calls.append(
                {
                    "runtime_generation": runtime_generation,
                    "dispatcher_generation": dispatcher_generation,
                    "owner_digest": owner_digest,
                    "lease_duration_ms": lease_duration_ms,
                }
            )
            while self.pending:
                operation = OperationRef(value=self.pending.popleft())
                current = self.records[operation.value]
                if current.state is not OperationState.ACCEPTED:
                    continue
                self._attempt_no += 1
                attempt = FakeAttempt(
                    operation=operation,
                    attempt_no=self._attempt_no,
                    lease_epoch=self._attempt_no,
                )
                self.records[operation.value] = _state(current, OperationState.RUNNING)
                self.write_count += 1
                return DispatchClaim(operation, attempt, attempt.lease_epoch)
            return None

    def finish_attempt(
        self,
        attempt: FakeAttempt,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
    ) -> OperationRecord:
        with self._lock:
            operation = attempt.operation
            fence = AttemptFence(
                dispatcher_generation=dispatcher_generation,
                lease_epoch=lease_epoch,
                owner_digest=owner_digest,
            )
            assert attempt.operation == operation
            assert attempt.lease_epoch == fence.lease_epoch
            assert runtime_generation == 1
            record = self.records[operation.value]
            self.finish_calls.append((operation, attempt, fence, record))
            self.write_count += 1
            return self.records[operation.value]

    def get(self, operation: OperationRef) -> OperationRecord:
        with self._lock:
            return self.records[operation.value]

    def park_attempt(
        self,
        attempt: FakeAttempt,
        runtime_generation: int,
        dispatcher_generation: int,
        lease_epoch: int,
        owner_digest: str,
        *,
        note: str,
    ) -> OperationRecord:
        with self._lock:
            operation = attempt.operation
            fence = AttemptFence(
                dispatcher_generation=dispatcher_generation,
                lease_epoch=lease_epoch,
                owner_digest=owner_digest,
            )
            assert attempt.operation == operation
            assert attempt.lease_epoch == fence.lease_epoch
            assert runtime_generation == 1
            current = self.records[operation.value]
            record = _state(current, OperationState.INDETERMINATE)
            self.records[operation.value] = record
            self.indeterminate_calls.append((operation, fence.dispatcher_generation, note))
            self.write_count += 1
            return record

    def set_terminal(self, operation: OperationRef) -> OperationRecord:
        with self._lock:
            current = self.records[operation.value]
            record = _state(current, OperationState.SUCCEEDED, result_json='{"ok":true}')
            self.records[operation.value] = record
            return record


class FakeHost:
    runtime_generation = 1

    def __init__(
        self,
        registry: FakeRegistry,
        *,
        delay_s: float = 0,
        block_until_release: bool = False,
    ) -> None:
        self.registry = registry
        self.delay_s = delay_s
        self.block_until_release = block_until_release
        self.calls: list[tuple[OperationRef, FakeAttempt, AttemptFence]] = []
        self.entered = threading.Event()
        self.release = threading.Event()
        self.active = 0
        self.max_active = 0
        self._lock = threading.Lock()

    def recover_durable_rlm(self) -> None:
        return None

    def run_claimed_rlm(
        self,
        operation: OperationRef,
        attempt: FakeAttempt,
        fence: AttemptFence,
    ) -> OperationRecord:
        with self._lock:
            self.calls.append((operation, attempt, fence))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.entered.set()
        try:
            if self.block_until_release:
                self.release.wait(1)
            if self.delay_s:
                time.sleep(self.delay_s)
            return self.registry.set_terminal(operation)
        finally:
            with self._lock:
                self.active -= 1


class ExplodingHost(FakeHost):
    def run_claimed_rlm(
        self,
        operation: OperationRef,
        attempt: FakeAttempt,
        fence: AttemptFence,
    ) -> OperationRecord:
        with self._lock:
            self.calls.append((operation, attempt, fence))
            self.entered.set()
        raise RuntimeError("worker exploded")


def test_queued_claim_runs_to_terminal_with_exact_fence() -> None:
    operation = OperationRef(value="op-queued")
    registry = FakeRegistry([operation])
    host = FakeHost(registry)
    dispatcher = DurableDispatcher(
        host,
        concurrency=1,
        lease_duration_ms=1_234,
        idle_poll_ms=5,
    )
    dispatcher.start()
    try:
        dispatcher.notify(operation)
        completed = dispatcher.wait(operation, timeout_s=1)

        assert completed.state is OperationState.SUCCEEDED
        assert registry.dispatch_requests == [(operation, "rlm.execute")]
        assert len(host.calls) == 1
        assert len(registry.finish_calls) == 1
        finished_operation, attempt, finished_fence, finished_record = registry.finish_calls[0]
        called_operation, called_attempt, called_fence = host.calls[0]
        assert finished_operation == called_operation == operation
        assert attempt == called_attempt
        assert finished_fence == called_fence
        assert finished_record is completed
        assert finished_fence.dispatcher_generation == host.runtime_generation
        assert finished_fence.lease_epoch == attempt.lease_epoch
        assert finished_fence.owner_digest == dispatcher.owner_digest
        assert registry.claim_calls[0]["lease_duration_ms"] == 1_234
    finally:
        dispatcher.close()


def test_wait_timeout_only_reads_and_does_not_cancel() -> None:
    operation = OperationRef(value="op-timeout")
    registry = FakeRegistry([operation])
    host = FakeHost(registry, block_until_release=True)
    dispatcher = DurableDispatcher(host, concurrency=1, idle_poll_ms=5)
    dispatcher.start()
    try:
        dispatcher.notify(operation)
        assert host.entered.wait(1)
        writes_before_wait = registry.write_count

        current = dispatcher.wait(operation, timeout_s=0.02)

        assert current.state is OperationState.RUNNING
        assert registry.write_count == writes_before_wait
        assert registry.indeterminate_calls == []

        host.release.set()
        completed = dispatcher.wait(operation, timeout_s=1)
        assert completed.state is OperationState.SUCCEEDED
    finally:
        dispatcher.close()


def test_idle_workers_poll_at_a_bounded_rate_without_waking_each_other() -> None:
    registry = FakeRegistry([])
    host = FakeHost(registry)
    dispatcher = DurableDispatcher(host, concurrency=2, idle_poll_ms=20)
    dispatcher.start()
    try:
        time.sleep(0.08)
    finally:
        dispatcher.close()

    assert 2 <= len(registry.claim_calls) <= 12


def test_execution_is_bounded_by_two_workers() -> None:
    operations = [OperationRef(value=f"op-{index}") for index in range(4)]
    registry = FakeRegistry(operations)
    host = FakeHost(registry, delay_s=0.03)
    dispatcher = DurableDispatcher(host, concurrency=2, idle_poll_ms=5)
    dispatcher.start()
    try:
        for operation in operations:
            dispatcher.notify(operation)
        completed = [dispatcher.wait(operation, timeout_s=2) for operation in operations]

        assert all(record.state is OperationState.SUCCEEDED for record in completed)
        assert len(host.calls) == len(operations)
        assert host.max_active <= 2
        assert host.max_active == 2
        assert len(registry.finish_calls) == len(operations)
    finally:
        dispatcher.close()


def test_close_rejects_new_submit_and_does_not_claim_again() -> None:
    operation = OperationRef(value="op-close")
    registry = FakeRegistry([operation])
    host = FakeHost(registry)
    dispatcher = DurableDispatcher(host, concurrency=2, idle_poll_ms=5)
    dispatcher.start()
    dispatcher.close(drain_timeout_s=1)

    with pytest.raises(DispatcherClosed, match="closed"):
        dispatcher.submit(operation)
    with pytest.raises(DispatcherClosed, match="closed"):
        dispatcher.notify(operation)

    assert registry.dispatch_requests == []
    assert all(not worker.is_alive() for worker in dispatcher._workers)


def test_unexpected_worker_exception_parks_indeterminate() -> None:
    operation = OperationRef(value="op-error")
    registry = FakeRegistry([operation])
    host = ExplodingHost(registry)
    dispatcher = DurableDispatcher(host, concurrency=1, idle_poll_ms=5)
    dispatcher.start()
    try:
        dispatcher.notify(operation)
        parked = dispatcher.wait(operation, timeout_s=1)

        assert parked.state is OperationState.INDETERMINATE
        assert parked.certainty is OutcomeCertainty.INDETERMINATE
        assert parked.reconciliation_required
        assert registry.indeterminate_calls
        assert registry.finish_calls == []
    finally:
        dispatcher.close()
