"""A valid bounded event envelope must not turn certain execution into worker loss."""

from __future__ import annotations

from test_lifecycle_consumer import exact_executed_package  # noqa: F401

from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.workspace_models import WorkspaceProgramSpec
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


def test_stdout_at_event_capacity_preserves_certain_success_and_terminal_metadata():
    backend = SupervisedIPythonWorkspaceBackend()
    handle = backend.create(
        WorkspaceRef(value="lifecycle-event-capacity"), SessionRef(value="lifecycle-event-capacity")
    )
    try:
        result = backend.execute(
            OperationRef(value="lifecycle-event-capacity-cell"),
            handle,
            WorkspaceProgramSpec(code="print('bounded')", max_events=2),
        )
        assert result.status == "succeeded"
        assert result.workspace_lost is False
        assert len(result.events) <= 2
        # add() reserves one terminal-event slot; bounded stdout may be dropped.
        assert any(
            isinstance(event.data, dict)
            and event.data.get("phase") == "finished"
            and event.data.get("events_truncated") is True
            for event in result.events
        )
    finally:
        backend.shutdown()
