from __future__ import annotations

from pathlib import Path

import pytest

from aar.mcp.public_auth import PublicIdentity
from aar.mcp.public_rlm_models import PublicRlmJobSpec
from aar.mcp.public_runtime import TenantCapacityExceeded, TenantRuntimePool
from aar.schemas import WorkspaceRef


def _identity(value: str) -> PublicIdentity:
    return PublicIdentity(
        tenant_key=value * 32,
        principal_id=f"principal-{value}",
        session_id=f"session-{value}",
        scopes=("aar:rlm", "aar:workspace"),
    )


def test_tenant_pool_evicts_only_idle_runtime_and_reopens_persisted_state(
    tmp_path: Path,
) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    tenant_a = _identity("a")
    tenant_b = _identity("b")
    workspace = WorkspaceRef(value="persisted")
    try:
        with pool.acquire(tenant_a) as runtime_a:
            created = runtime_a.host.create_workspace(workspace, runtime_a.session)
            assert created.revision == 0
            with pytest.raises(TenantCapacityExceeded):
                pool.acquire(tenant_b)

        with pool.acquire(tenant_b) as runtime_b:
            assert runtime_b.host.create_workspace(workspace, runtime_b.session).revision == 0

        with pool.acquire(tenant_a) as reopened_a:
            handle = reopened_a.host.workspace.current_handle(workspace)
            reopened_a.host.workspace.assert_session(workspace, reopened_a.session)
            assert handle.revision == 0

        assert pool.tenant_database_path(tenant_a.tenant_key).is_file()
        assert pool.tenant_database_path(tenant_b.tenant_key).is_file()
    finally:
        pool.close()


def test_tenant_lease_release_is_idempotent(tmp_path: Path) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    lease = pool.acquire(_identity("a"))
    try:
        lease.__exit__(None, None, None)
        lease.__exit__(None, None, None)
        with pool.acquire(_identity("b")):
            pass
    finally:
        pool.close()


def test_tenant_lru_reopens_pending_caller_delegated_rlm_state(tmp_path: Path) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    tenant_a = _identity("a")
    tenant_b = _identity("b")
    spec = PublicRlmJobSpec(
        query="persist across tenant LRU eviction",
        strategy="single_call",
        executor_kind="codex-host",
        requested_model="model-fixed",
        requested_reasoning_effort="high",
        max_model_calls=1,
        max_output_tokens_per_call=64,
        max_result_bytes_per_call=1024,
    )
    try:
        with pool.acquire(tenant_a) as runtime_a:
            started = runtime_a.rlm.start(spec, idempotency_key="lru-rlm-start")
            assert started.job is not None
            job_id = started.job.job_id
            state_digest = started.job.state_digest

        with pool.acquire(tenant_b):
            pass

        with pool.acquire(tenant_a) as reopened_a:
            recovered = reopened_a.rlm.status(job_id)
            assert recovered.job is not None
            assert recovered.job.phase == "pending_model_call"
            assert recovered.job.state_digest == state_digest
    finally:
        pool.close()
