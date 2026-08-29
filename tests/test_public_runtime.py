from __future__ import annotations

from pathlib import Path

import pytest

from aar.mcp.public_auth import PublicIdentity
from aar.mcp.public_rlm_models import PublicRlmJobSpec
from aar.mcp.public_runtime import TenantCapacityExceeded, TenantRuntime, TenantRuntimePool
from aar.runtime.ownership import RuntimeOwnershipConflict
from aar.schemas import WorkspaceRef


def _identity(value: str) -> PublicIdentity:
    return PublicIdentity(
        tenant_key=value * 32,
        principal_id=f"principal-{value}",
        session_id=f"session-{value}",
        scopes=("aar:rlm", "aar:workspace"),
    )


def test_two_tenant_pools_cannot_own_the_same_runtime_concurrently(tmp_path: Path) -> None:
    data_root = tmp_path / "tenants"
    first = TenantRuntimePool(data_root, max_active_tenants=1)
    second = TenantRuntimePool(data_root, max_active_tenants=1)
    identity = _identity("a")
    lease = first.acquire(identity)
    try:
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            second.acquire(identity)
    finally:
        lease.__exit__(None, None, None)
        first.close()
    try:
        with second.acquire(identity) as successor:
            assert successor.host.ready().runtime_generation == 2
    finally:
        second.close()


def test_failed_tenant_close_retains_runtime_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    lease = pool.acquire(_identity("a"))
    runtime = lease.runtime
    database = runtime.ownership.path
    real_close = runtime.host.close

    def fail_close() -> None:
        raise RuntimeError("tenant host resources remain live")

    monkeypatch.setattr(runtime.host, "close", fail_close)
    with pytest.raises(RuntimeError, match="resources remain live"):
        runtime.close()
    with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
        runtime.ownership.__class__(database)

    monkeypatch.setattr(runtime.host, "close", real_close)
    runtime.close()
    with runtime.ownership.__class__(database):
        pass
    lease.__exit__(None, None, None)
    pool._runtimes.clear()
    pool.close()


def test_failed_tenant_eviction_retains_runtime_for_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    tenant_a = _identity("a")
    tenant_b = _identity("b")
    lease = pool.acquire(tenant_a)
    runtime = lease.runtime
    database = runtime.ownership.path
    spec = PublicRlmJobSpec(
        query="remain readable after failed eviction",
        strategy="single_call",
        executor_kind="codex-host",
        requested_model="model-fixed",
        requested_reasoning_effort="high",
        max_model_calls=1,
        max_output_tokens_per_call=64,
        max_result_bytes_per_call=1024,
    )
    started = runtime.rlm.start(spec, idempotency_key="failed-eviction-start")
    assert started.job is not None
    lease.__exit__(None, None, None)
    real_host_close = runtime.host.close
    host_close_calls = 0

    def fail_host_once() -> None:
        nonlocal host_close_calls
        host_close_calls += 1
        if host_close_calls == 1:
            raise RuntimeError("tenant eviction close failed")
        real_host_close()

    monkeypatch.setattr(runtime.host, "close", fail_host_once)
    try:
        with pytest.raises(RuntimeError, match="eviction close failed"):
            pool.acquire(tenant_b)
        assert tenant_a.tenant_key in pool._runtimes
        assert pool._runtimes[tenant_a.tenant_key].closing is True
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            runtime.ownership.__class__(database)
        with pytest.raises(TenantCapacityExceeded, match="runtime is closing"):
            pool.acquire(tenant_a)
        recovered = runtime.rlm.status(started.job.job_id)
        assert recovered.job is not None
        assert recovered.job.state_digest == started.job.state_digest

        with pool.acquire(tenant_b):
            pass
        assert tenant_a.tenant_key not in pool._runtimes
        assert host_close_calls == 2
    finally:
        pool.close()


def test_failed_pool_close_retains_runtime_and_retry_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = TenantRuntimePool(tmp_path / "tenants", max_active_tenants=1)
    tenant = _identity("a")
    lease = pool.acquire(tenant)
    runtime = lease.runtime
    database = runtime.ownership.path
    lease.__exit__(None, None, None)
    real_close = TenantRuntime.close
    close_calls = 0

    def fail_once(candidate: TenantRuntime) -> None:
        nonlocal close_calls
        if candidate is runtime:
            close_calls += 1
            if close_calls == 1:
                raise RuntimeError("tenant pool close failed")
        real_close(candidate)

    monkeypatch.setattr(TenantRuntime, "close", fail_once)
    with pytest.raises(RuntimeError, match="pool close failed"):
        pool.close()
    assert pool._closed is True
    assert tenant.tenant_key in pool._runtimes
    with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
        runtime.ownership.__class__(database)

    pool.close()
    assert pool._runtimes == {}
    assert close_calls == 2
    with runtime.ownership.__class__(database):
        pass


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
