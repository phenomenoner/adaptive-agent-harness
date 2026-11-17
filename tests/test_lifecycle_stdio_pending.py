"""Bounded owned-stream cleanup when a real buffered reader has no EOF."""

from __future__ import annotations

import io
import os
import threading
from types import SimpleNamespace

import pytest

from aar.runtime.ipython_backend import (
    SupervisedIPythonWorkspaceBackend,
    WorkspaceWorkerLost,
    _WorkerState,
)
from aar.schemas import SessionRef


class _ObservedPipe(io.TextIOWrapper):
    def __init__(self, descriptor: int) -> None:
        super().__init__(io.BufferedReader(io.FileIO(descriptor, "rb", closefd=True)))
        self.read_started = threading.Event()
        self.close_calls = 0

    def readline(self, *args, **kwargs):
        self.read_started.set()
        return super().readline(*args, **kwargs)

    def close(self):
        self.close_calls += 1
        return super().close()


@pytest.mark.parametrize("action", ["terminate", "mark_lost"])
def test_pending_buffered_read_cannot_block_owned_cleanup(action):
    read_fd, write_fd = os.pipe()
    stdout = _ObservedPipe(read_fd)
    writer = os.fdopen(write_fd, "wb")
    process = SimpleNamespace(stdin=io.StringIO(), stdout=stdout, stderr=io.StringIO())
    state = _WorkerState(
        session=SessionRef(value="controlled-pipe"),
        generation=1,
        process=process,
        exact_child=SimpleNamespace(terminalize=lambda timeout: None),
    )
    backend = SupervisedIPythonWorkspaceBackend()
    returned = threading.Event()
    errors = []

    def terminate():
        try:
            if action == "terminate":
                backend._terminate(state)
            else:
                backend._mark_lost(state, "controlled timeout")
        except BaseException as error:
            errors.append(error)
        finally:
            returned.set()

    thread = threading.Thread(target=terminate, daemon=True)
    try:
        assert backend._readline(stdout, 0.02) is None
        assert stdout.read_started.is_set()
        thread.start()
        completed_without_eof = returned.wait(1.5)
        pipe_was_still_open = not stdout.closed
        siblings_closed = process.stdin.closed and process.stderr.closed
        lost_before_eof = state.lost
    finally:
        writer.close()  # Test-owned release, never credited as product cleanup.
        if thread.ident is not None:
            thread.join(5)
        for attempt in getattr(state, "stdio_close_attempts", {}).values():
            attempt.done.wait(5)
        if not stdout.closed:
            stdout.close()
    assert not thread.is_alive()
    assert completed_without_eof, "owned close waited for the pending reader lock"
    assert pipe_was_still_open and siblings_closed
    assert len(errors) == 1 and isinstance(errors[0], WorkspaceWorkerLost)
    assert "stdio cleanup" in str(errors[0])
    if action == "mark_lost":
        assert lost_before_eof
    backend._terminate(state)
    assert stdout.closed
    assert stdout.close_calls == 1, "retry must join the same owned close, not start another"
