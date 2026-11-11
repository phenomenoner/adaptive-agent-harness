from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from test_caller_work_lifecycle import (
    FIXED_NOW_MS,
    TICKET_ID,
    MutableClock,
    claim_input,
    commit_input,
    mark_send_started_input,
    new_repository,
)

from aar.runtime.programming import PlainPythonWorkspaceBackend
from aar.runtime.workspace import WorkspaceNotFound
from aar.runtime.workspace_models import ProgrammableWorkspaceHandle, WorkspaceProgramSpec
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


def test_missing_workspace_does_not_leave_an_execution_running() -> None:
    backend = PlainPythonWorkspaceBackend()
    workspace = WorkspaceRef(value="workspace-not-created-yet")
    operation = OperationRef(value="operation-missing-workspace")
    handle = ProgrammableWorkspaceHandle(
        workspace=workspace, backend=backend.descriptor, generation=1, revision=0
    )
    spec = WorkspaceProgramSpec(code="answer = 42\nanswer")
    try:
        with pytest.raises(WorkspaceNotFound):
            backend.execute(operation, handle, spec)
        actual = backend.create(workspace, SessionRef(value="session-missing-workspace"))
        result = backend.execute(operation, actual, spec)
        assert result.status == "succeeded"
        assert result.result == 42
        assert result.revision_before == 0
        assert result.revision_after == 1
        current = actual.model_copy(update={"revision": result.revision_after})
        assert backend.reconcile(current, operation).state == "completed"
    finally:
        backend.shutdown()


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_unknown_caller_observation_retains_uncertainty_until_a_certain_receipt(
    tmp_path: Path, cancel_requested: bool
) -> None:
    repository = new_repository(tmp_path, MutableClock(FIXED_NOW_MS))
    database = tmp_path / "registry.sqlite"
    try:
        pending = repository.get(TICKET_ID)
        claimed = repository.claim(claim_input(pending))
        started = repository.mark_send_started(
            mark_send_started_input(claimed, idempotency_key="send-once")
        )
        if cancel_requested:
            started = repository.request_cancel(
                ticket_id=TICKET_ID,
                expected_revision=started.revision,
                reason="requested-by-test",
                idempotency_key="request-cancel",
            )
        command = commit_input(started, idempotency_key="unknown-observation")
        command["model_response"] = None
        command["observation"].update(
            outcome="outcome_unknown",
            output_text=None,
            output_digest=None,
            route_receipt_digest=None,
            usage_receipt_digest=None,
        )
        unknown = repository.commit(command)
        assert unknown.state == ("cancel_requested" if cancel_requested else "outcome_unknown")
        assert unknown.settled_receipt_digest is None
        assert unknown.settled_at_unix_ms is None
        assert repository.send_started_count(TICKET_ID) == 1
        assert len(repository.candidate_receipts(TICKET_ID)) == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM rlm_workbench_suspensions"
            ).fetchone() == ("pending",)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox"
            ).fetchone() == (0,)
        assert repository.commit(command).model_dump() == unknown.model_dump()

        settled = repository.commit(commit_input(unknown, idempotency_key="certain-observation"))
        assert settled.state == "settled_success"
        assert len(repository.candidate_receipts(TICKET_ID)) == 2
        assert repository.send_started_count(TICKET_ID) == 1
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT state FROM rlm_workbench_suspensions"
            ).fetchone() == ("settled",)
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_successor_outbox"
            ).fetchone() == (1,)
    finally:
        repository.close()
