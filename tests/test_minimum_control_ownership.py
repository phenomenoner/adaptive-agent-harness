from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    prepare_v6_registry,
    workbench_envelope,
    workbench_request,
)

from aar.canonical import canonical_sha256
from aar.runtime.dispatcher import AttemptFence
from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.programming import WorkspaceOperationConflict
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.rlm_workbench import RlmWorkbenchConflict


def test_artifact_callback_release_is_exact_and_cannot_remove_a_successor() -> None:
    backend = SupervisedIPythonWorkspaceBackend()

    def first(*_args: Any, **_kwargs: Any) -> Any:
        return None

    def successor(*_args: Any, **_kwargs: Any) -> Any:
        return None

    try:
        backend.bind_artifact_stage_handler(first)
        backend.unbind_artifact_stage_handler(successor)
        with pytest.raises(WorkspaceOperationConflict):
            backend.bind_artifact_stage_handler(successor)
        backend.unbind_artifact_stage_handler(first)
        backend.bind_artifact_stage_handler(successor)
        backend.unbind_artifact_stage_handler(first)
        with pytest.raises(WorkspaceOperationConflict):
            backend.bind_artifact_stage_handler(first)
    finally:
        backend.shutdown()


@pytest.mark.parametrize("stale_kind", ["replaced_owner", "expired_lease"])
def test_budget_terminal_transition_requires_the_current_unexpired_attempt(
    tmp_path: Path, stale_kind: str
) -> None:
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "registry-v5.snapshot.sqlite")
    clock = [FIXED_NOW_MS]
    host = ReferenceHost(database, now_ms=lambda: clock[0])
    try:
        request = workbench_request(host, require_named_artifacts=False)
        admitted = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        claim = host.registry.claim_next(
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            owner_digest=canonical_sha256("budget-owner"),
            lease_duration_ms=1000,
        )
        assert claim is not None
        coordinator = host.rlm_workbench
        assert coordinator is not None
        before = coordinator.snapshot(admitted.operation)
        fence = AttemptFence(
            dispatcher_generation=claim.dispatcher_generation,
            lease_epoch=claim.lease_epoch,
            owner_digest=(
                canonical_sha256("not-the-owner")
                if stale_kind == "replaced_owner"
                else claim.owner_digest
            ),
        )
        if stale_kind == "expired_lease":
            clock[0] += 1001
        with pytest.raises(RlmWorkbenchConflict, match="workbench attempt fence is stale"):
            coordinator.mark_model_call_budget_terminal(admitted.operation, claim.attempt, fence)
        assert coordinator.snapshot(admitted.operation) == before
    finally:
        host.close()
