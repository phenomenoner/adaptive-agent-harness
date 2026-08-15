from __future__ import annotations

import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import aar.runtime.process_identity as process_identity_module
import aar.runtime.worker_manager as worker_manager_module
from aar.canonical import canonical_sha256
from aar.runtime.process_identity import (
    ProcessIdentityMismatch,
    ProcessIdentityObservation,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    current_process_identity,
)
from aar.runtime.registry import OperationRegistry
from aar.runtime.worker_manager import WorkerManager


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _sleeping_child() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )


def _synthetic_identity(pid: int = 424_242) -> ProcessStartIdentity:
    current = current_process_identity()
    return current.model_copy(update={"pid": pid})


def test_successor_manager_fences_exact_orphan_process(tmp_path: Path) -> None:
    database = tmp_path / "workers.sqlite3"
    registry = OperationRegistry(database, now_ms)
    first_generation = registry.start_runtime()
    first = WorkerManager(registry, runtime_generation=first_generation, now_ms=now_ms)
    child = _sleeping_child()
    try:
        managed = first.register(
            worker_kind="ipython",
            workspace_id="workspace-orphan",
            workspace_generation=1,
            pid=child.pid,
            capability_digest=canonical_sha256({"capability": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )
        first.heartbeat(managed)
        waiter = threading.Thread(target=child.wait, daemon=True)
        waiter.start()

        second_generation = registry.start_runtime()
        second = WorkerManager(
            registry,
            runtime_generation=second_generation,
            now_ms=now_ms,
            termination_timeout_s=3,
        )
        recovered = second.recover_orphans()
        assert len(recovered) == 1
        assert recovered[0].worker_id == managed.worker_id
        assert recovered[0].state == "terminated"
        waiter.join(timeout=3)
        assert child.poll() is not None
        assert registry.active_worker_bindings() == ()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=3)
        registry.close()


def test_heartbeat_unavailable_does_not_finish_worker_as_lost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "heartbeat-unavailable.sqlite3", now_ms)
    try:
        generation = registry.start_runtime()
        manager = WorkerManager(registry, runtime_generation=generation, now_ms=now_ms)
        current = current_process_identity()
        managed = manager.register(
            worker_kind="ipython",
            workspace_id="workspace-heartbeat-unavailable",
            workspace_generation=1,
            pid=current.pid,
            capability_digest=canonical_sha256({"capability": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )

        def unavailable(_pid: int) -> ProcessStartIdentity:
            raise ProcessIdentityUnavailable("transient heartbeat identity failure")

        monkeypatch.setattr(process_identity_module, "process_identity", unavailable)
        monkeypatch.setattr(worker_manager_module, "process_identity", unavailable)

        try:
            observed = manager.heartbeat(managed)
        except ProcessIdentityUnavailable:
            # An explicit unresolved result is allowed to surface as an exception, but the
            # durable binding must remain active and retain no terminal receipt.
            observed = registry.worker_binding(managed.worker_id)

        assert observed.state not in {"lost", "quarantined", "terminated"}
        assert observed.ended_at_unix_ms is None
        assert observed.termination_receipt_json is None
        assert registry.active_worker_bindings() == (observed,)
    finally:
        registry.close()


def test_orphan_unavailable_is_unresolved_and_blocks_successor_registration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "orphan-unavailable.sqlite3", now_ms)
    try:
        predecessor_generation = registry.start_runtime()
        expected = _synthetic_identity()
        registry.register_worker_binding(
            worker_id="worker-orphan-unavailable",
            runtime_generation=predecessor_generation,
            worker_kind="ipython",
            workspace_id="workspace-orphan-unavailable",
            workspace_generation=1,
            pid=expected.pid,
            process_start_identity=expected.model_dump_json(),
            capability_digest=canonical_sha256({"capability": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )

        successor_generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=successor_generation,
            now_ms=now_ms,
        )

        unavailable = ProcessIdentityUnavailable("transient orphan identity failure")
        monkeypatch.setattr(
            worker_manager_module,
            "observe_process_identity",
            lambda _identity: ProcessIdentityObservation(
                state=ProcessIdentityState.UNAVAILABLE,
                error=unavailable,
            ),
        )

        try:
            recovered = manager.recover_orphans()
        except ProcessIdentityUnavailable:
            recovered = ()

        binding = registry.worker_binding("worker-orphan-unavailable")
        if recovered:
            assert recovered == (binding,)
        assert binding.state not in {"lost", "quarantined", "terminated"}
        assert binding.ended_at_unix_ms is None
        assert binding.termination_receipt_json is None

        # Restore a successful identity observation so this assertion reaches admission rather
        # than failing early while constructing the candidate worker.
        monkeypatch.setattr(worker_manager_module, "process_identity", lambda _pid: expected)
        with pytest.raises(RuntimeError):
            manager.register(
                worker_kind="ipython",
                workspace_id="workspace-orphan-unavailable",
                workspace_generation=2,
                pid=expected.pid,
                capability_digest=canonical_sha256({"capability": "ipython"}),
                environment_digest=canonical_sha256({"environment": "test"}),
            )
    finally:
        registry.close()


def test_termination_unavailable_sends_no_signal_and_is_not_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "termination-unavailable.sqlite3", now_ms)
    try:
        generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=generation,
            now_ms=now_ms,
            termination_timeout_s=0.01,
        )
        identity = _synthetic_identity()
        signals: list[tuple[int, int]] = []

        def unavailable(*_args: object, **_kwargs: object):
            raise ProcessIdentityUnavailable("transient termination identity failure")

        monkeypatch.setattr(worker_manager_module, "open_exact_process", unavailable)
        monkeypatch.setattr(
            worker_manager_module.os,
            "kill",
            lambda pid, signum: signals.append((pid, signum)),
        )

        with pytest.raises(ProcessIdentityUnavailable, match="transient termination"):
            manager._terminate_exact(identity)
        assert signals == []
    finally:
        registry.close()


def test_termination_pid_reuse_before_sigterm_never_signals_or_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "termination-sigterm-reuse.sqlite3", now_ms)
    try:
        generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=generation,
            now_ms=now_ms,
            termination_timeout_s=0.01,
        )
        identity = _synthetic_identity()
        signals: list[tuple[int, int]] = []

        def reused(*_args: object, **_kwargs: object):
            raise ProcessIdentityMismatch("post-open identity mismatch")

        monkeypatch.setattr(worker_manager_module, "open_exact_process", reused)
        monkeypatch.setattr(
            worker_manager_module.os,
            "kill",
            lambda pid, signum: signals.append((pid, signum)),
        )

        with pytest.raises(ProcessIdentityMismatch, match="post-open identity mismatch"):
            manager._terminate_exact(identity)
        assert signals == []
    finally:
        registry.close()


def test_termination_pid_reuse_before_sigkill_never_signals_or_succeeds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "termination-sigkill-reuse.sqlite3", now_ms)
    try:
        generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=generation,
            now_ms=now_ms,
            termination_timeout_s=0.01,
        )
        identity = _synthetic_identity()
        numeric_signals: list[tuple[int, int]] = []
        exact_signals: list[int] = []

        class ExactTarget:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def send_signal(self, signum: int) -> bool:
                exact_signals.append(signum)
                return True

            def wait(self, _timeout: float) -> bool:
                return False

        monkeypatch.setattr(
            worker_manager_module,
            "open_exact_process",
            lambda *_args, **_kwargs: ExactTarget(),
        )
        monkeypatch.setattr(worker_manager_module.os, "name", "posix")
        monkeypatch.setattr(worker_manager_module.signal, "SIGKILL", 9, raising=False)
        monkeypatch.setattr(
            worker_manager_module.os,
            "kill",
            lambda pid, signum: numeric_signals.append((pid, signum)),
        )

        assert manager._terminate_exact(identity) is False
        assert numeric_signals == []
        assert exact_signals == [signal.SIGTERM, 9]
    finally:
        registry.close()


def test_orphan_exact_handle_unavailable_after_match_remains_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "orphan-post-match-unavailable.sqlite3", now_ms)
    try:
        predecessor_generation = registry.start_runtime()
        identity = _synthetic_identity()
        registry.register_worker_binding(
            worker_id="worker-post-match-unavailable",
            runtime_generation=predecessor_generation,
            worker_kind="ipython",
            workspace_id="workspace-post-match-unavailable",
            workspace_generation=1,
            pid=identity.pid,
            process_start_identity=identity.model_dump_json(),
            capability_digest=canonical_sha256({"capability": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )
        successor_generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=successor_generation,
            now_ms=now_ms,
        )
        monkeypatch.setattr(
            worker_manager_module,
            "observe_process_identity",
            lambda _identity: ProcessIdentityObservation(
                state=ProcessIdentityState.MATCH,
                observed=identity,
            ),
        )

        def unavailable(*_args: object, **_kwargs: object):
            raise ProcessIdentityUnavailable("exact handle became unavailable")

        monkeypatch.setattr(worker_manager_module, "open_exact_process", unavailable)

        recovered = manager.recover_orphans()

        binding = registry.worker_binding("worker-post-match-unavailable")
        assert recovered == (binding,)
        assert binding.state not in {"lost", "quarantined", "terminated"}
        assert binding.ended_at_unix_ms is None
        assert binding.termination_receipt_json is None
    finally:
        registry.close()


def test_orphan_signal_without_terminal_readback_remains_active(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = OperationRegistry(tmp_path / "orphan-terminal-unconfirmed.sqlite3", now_ms)
    try:
        predecessor_generation = registry.start_runtime()
        identity = _synthetic_identity()
        registry.register_worker_binding(
            worker_id="worker-terminal-unconfirmed",
            runtime_generation=predecessor_generation,
            worker_kind="ipython",
            workspace_id="workspace-terminal-unconfirmed",
            workspace_generation=1,
            pid=identity.pid,
            process_start_identity=identity.model_dump_json(),
            capability_digest=canonical_sha256({"capability": "ipython"}),
            environment_digest=canonical_sha256({"environment": "test"}),
        )
        successor_generation = registry.start_runtime()
        manager = WorkerManager(
            registry,
            runtime_generation=successor_generation,
            now_ms=now_ms,
            termination_timeout_s=0.01,
        )
        monkeypatch.setattr(
            worker_manager_module,
            "observe_process_identity",
            lambda _identity: ProcessIdentityObservation(
                state=ProcessIdentityState.MATCH,
                observed=identity,
            ),
        )

        class UnconfirmedTarget:
            def __enter__(self):
                return self

            def __exit__(self, *_args: object) -> None:
                return None

            def send_signal(self, _signum: int) -> bool:
                return True

            def wait(self, _timeout: float) -> bool:
                return False

        monkeypatch.setattr(
            worker_manager_module,
            "open_exact_process",
            lambda *_args, **_kwargs: UnconfirmedTarget(),
        )
        monkeypatch.setattr(worker_manager_module.os, "name", "nt")

        recovered = manager.recover_orphans()

        binding = registry.worker_binding("worker-terminal-unconfirmed")
        assert recovered == (binding,)
        assert binding.state not in {"lost", "quarantined", "terminated"}
        assert binding.ended_at_unix_ms is None
        assert binding.termination_receipt_json is None
    finally:
        registry.close()


def test_pid_reuse_mismatch_is_quarantined_without_signalling_current_process(
    tmp_path: Path,
) -> None:
    registry = OperationRegistry(tmp_path / "pid-reuse.sqlite3", now_ms)
    first_generation = registry.start_runtime()
    current = current_process_identity()
    mismatched = current.model_copy(update={"start_time": current.start_time + 1})
    registry.register_worker_binding(
        worker_id="worker-pid-reuse",
        runtime_generation=first_generation,
        worker_kind="ipython",
        workspace_id="workspace-pid-reuse",
        workspace_generation=1,
        pid=current.pid,
        process_start_identity=mismatched.model_dump_json(),
        capability_digest=canonical_sha256({"capability": "ipython"}),
        environment_digest=canonical_sha256({"environment": "test"}),
    )

    second_generation = registry.start_runtime()
    manager = WorkerManager(registry, runtime_generation=second_generation, now_ms=now_ms)
    recovered = manager.recover_orphans()
    assert [item.state for item in recovered] == ["quarantined"]
    receipt = recovered[0].termination_receipt_json
    assert receipt is not None
    assert "pid_reused_identity_mismatch" in receipt
    assert current_process_identity() == current
    registry.close()


def test_malformed_prior_identity_is_quarantined_as_unknown(tmp_path: Path) -> None:
    registry = OperationRegistry(tmp_path / "malformed.sqlite3", now_ms)
    first_generation = registry.start_runtime()
    registry.register_worker_binding(
        worker_id="worker-malformed",
        runtime_generation=first_generation,
        worker_kind="ipython",
        workspace_id="workspace-malformed",
        workspace_generation=1,
        pid=999_999,
        process_start_identity="not-json",
        capability_digest=canonical_sha256({"capability": "ipython"}),
        environment_digest=canonical_sha256({"environment": "test"}),
    )

    second_generation = registry.start_runtime()
    manager = WorkerManager(registry, runtime_generation=second_generation, now_ms=now_ms)
    recovered = manager.recover_orphans()
    assert [item.state for item in recovered] == ["quarantined"]
    receipt = recovered[0].termination_receipt_json
    assert receipt is not None
    assert '"process_identity":null' in receipt
    assert '"recorded_process_identity":"not-json"' in receipt
    registry.close()
