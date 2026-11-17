"""Owned stdio has its own lifecycle after exact child termination."""

from __future__ import annotations

import io
import threading
from types import SimpleNamespace

import pytest
from test_lifecycle_consumer import exact_executed_package  # noqa: F401

from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend, WorkspaceWorkerLost
from aar.runtime.process_identity import ProcessIdentityUnavailable
from aar.runtime.workspace_models import WorkspaceProgramSpec
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


def assert_owned_stdio_closed(state):
    assert state.process.poll() is not None
    assert all(
        stream is None or stream.closed
        for stream in (state.process.stdin, state.process.stdout, state.process.stderr)
    )


def test_real_close_reaps_child_and_closes_owned_stdio():
    backend = SupervisedIPythonWorkspaceBackend()
    handle = backend.create(
        WorkspaceRef(value="lifecycle-pipes-close"), SessionRef(value="lifecycle-pipes")
    )
    state = backend._states[handle.workspace.value]
    try:
        backend.close(handle, reason="scoped lifecycle test")
        assert_owned_stdio_closed(state)
    finally:
        backend.shutdown()


def test_real_shutdown_is_idempotent_and_closes_all_worker_pipes():
    backend = SupervisedIPythonWorkspaceBackend()
    states = []
    try:
        for index in range(2):
            handle = backend.create(
                WorkspaceRef(value=f"lifecycle-pipes-shutdown-{index}"),
                SessionRef(value="lifecycle-pipes"),
            )
            states.append(backend._states[handle.workspace.value])
        backend.shutdown()
        first_receipts = [state.exact_child.terminal_receipt for state in states]
        backend.shutdown()
        for state, receipt in zip(states, first_receipts, strict=True):
            assert_owned_stdio_closed(state)
            assert state.exact_child.terminal_receipt is receipt
    finally:
        backend.shutdown()


def test_real_timeout_keeps_primary_status_and_closes_owned_stdio():
    backend = SupervisedIPythonWorkspaceBackend()
    handle = backend.create(
        WorkspaceRef(value="lifecycle-pipes-timeout"), SessionRef(value="lifecycle-pipes")
    )
    state = backend._states[handle.workspace.value]
    try:
        result = backend.execute(
            OperationRef(value="lifecycle-pipes-timeout-cell"),
            handle,
            WorkspaceProgramSpec(code="import time; time.sleep(2)", wall_time_ms=10),
        )
        assert result.status == "timed_out"
        assert result.workspace_lost is True
        assert_owned_stdio_closed(state)
    finally:
        backend.shutdown()


def test_unresolved_exact_child_cannot_be_claimed_as_stdio_cleanup():
    streams = [io.StringIO() for _ in range(3)]

    def unresolved(_timeout):
        raise ProcessIdentityUnavailable("controlled unresolved exact child")

    state = SimpleNamespace(
        stdio_close_lock=threading.Lock(),
        stdio_close_attempts={},
        exact_child=SimpleNamespace(terminalize=unresolved),
        process=SimpleNamespace(stdin=streams[0], stdout=streams[1], stderr=streams[2]),
    )
    try:
        with pytest.raises(WorkspaceWorkerLost, match="verified terminal state"):
            SupervisedIPythonWorkspaceBackend._terminate(state)
        assert all(not stream.closed for stream in streams)
    finally:
        for stream in streams:
            stream.close()


def test_close_failure_attempts_sibling_pipes_and_is_not_silent():
    calls = []

    class BrokenPipe:
        closed = False

        def close(self):
            calls.append("broken-close")
            raise OSError("controlled pipe close failure")

    def terminalize(_timeout):
        calls.append("exact-terminal")

    good = [io.StringIO(), io.StringIO()]
    state = SimpleNamespace(
        stdio_close_lock=threading.Lock(),
        stdio_close_attempts={},
        exact_child=SimpleNamespace(terminalize=terminalize),
        process=SimpleNamespace(stdin=BrokenPipe(), stdout=good[0], stderr=good[1]),
    )
    try:
        with pytest.raises(WorkspaceWorkerLost, match="stdio"):
            SupervisedIPythonWorkspaceBackend._terminate(state)
        assert calls[0] == "exact-terminal"
        assert all(stream.closed for stream in good)
    finally:
        for stream in good:
            stream.close()
