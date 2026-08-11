from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

from aar.canonical import canonical_sha256
from aar.runtime.process_identity import current_process_identity
from aar.runtime.registry import OperationRegistry
from aar.runtime.worker_manager import WorkerManager


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _sleeping_child() -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        text=True,
    )


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
