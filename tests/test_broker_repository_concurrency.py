from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event, Thread

import pytest

from aar.broker_models import BrokerContext
from aar.runtime.brokers import FakeArtifactBroker, FakeSubagentBroker
from aar.schemas import OperationRef


def test_db_artifact_repository_supports_cross_thread_put_and_read(tmp_path: Path) -> None:
    """RED: one broker instance must never share a SQLite connection across worker threads."""

    broker = FakeArtifactBroker(tmp_path / "artifact-cross-thread.sqlite3")
    operation = OperationRef(value="operation-artifact-cross-thread")

    def round_trip(index: int) -> tuple[str, bytes]:
        content = f"artifact-{index:03d}".encode()
        reference = broker.put(content, "text/plain", operation)
        return reference.digest, broker.read(reference)

    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            observed = list(workers.map(round_trip, range(32)))
        assert len({digest for digest, _content in observed}) == 32
        assert [content for _digest, content in observed] == [
            f"artifact-{index:03d}".encode() for index in range(32)
        ]
    finally:
        broker.close()


def test_db_subagent_repository_supports_cross_thread_submit_and_status(
    tmp_path: Path,
) -> None:
    """RED: retained-child submit/status must use thread-owned transactions."""

    broker = FakeSubagentBroker(tmp_path / "subagent-cross-thread.sqlite3")
    operation = OperationRef(value="operation-subagent-cross-thread")

    def round_trip(index: int) -> tuple[str, str]:
        task = f"bounded-task-{index:03d}"
        context = BrokerContext(
            parent_operation=operation,
            grant_id="grant-subagent-submit",
            deadline_unix_ms=4_102_444_800_000,
            idempotency_key=f"subagent-{index:03d}",
        )
        handle = broker.submit(task, context)
        assert broker.lookup_submission(task, context) == handle
        return handle, broker.result(handle).value

    try:
        with ThreadPoolExecutor(max_workers=8) as workers:
            observed = list(workers.map(round_trip, range(32)))
        assert len({handle for handle, _value in observed}) == 32
        assert [value for _handle, value in observed] == [
            f"completed:bounded-task-{index:03d}" for index in range(32)
        ]
    finally:
        broker.close()


def test_repository_close_fences_new_transactions_and_waits_for_active_one(
    tmp_path: Path,
) -> None:
    """RED: close must be a lifecycle fence, not a concurrent connection teardown."""

    from aar.runtime.sqlite_repository import (  # intentionally absent at the RED boundary
        RepositoryClosed,
        SQLiteConnectionFactory,
    )

    factory = SQLiteConnectionFactory(tmp_path / "close-race.sqlite3")
    transaction_entered = Event()
    release_transaction = Event()
    transaction_finished = Event()
    close_finished = Event()

    def hold_transaction() -> None:
        with factory.transaction(write=True) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS held(value INTEGER NOT NULL)")
            transaction_entered.set()
            assert release_transaction.wait(timeout=5)
            connection.execute("INSERT INTO held(value) VALUES (1)")
        transaction_finished.set()

    def close_factory() -> None:
        factory.close()
        close_finished.set()

    worker = Thread(target=hold_transaction, name="repository-active-transaction")
    closer = Thread(target=close_factory, name="repository-close")
    worker.start()
    assert transaction_entered.wait(timeout=5)
    closer.start()

    try:
        assert not close_finished.wait(timeout=0.1)
        release_transaction.set()
        worker.join(timeout=5)
        closer.join(timeout=5)
        assert transaction_finished.is_set()
        assert close_finished.is_set()
        with pytest.raises(RepositoryClosed), factory.transaction(write=False):
            pass
    finally:
        release_transaction.set()
        worker.join(timeout=5)
        closer.join(timeout=5)
        factory.close()
