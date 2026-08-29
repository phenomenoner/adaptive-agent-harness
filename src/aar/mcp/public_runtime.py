"""Tenant-scoped runtime ownership for the curated public AAR MCP surface."""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path

from aar.mcp.public_auth import PublicIdentity
from aar.mcp.public_rlm import PublicRlmCoordinator
from aar.runtime.ownership import RuntimeOwnershipLock
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import PrincipalRef, SessionRef

_TENANT_KEY = re.compile(r"^[0-9a-f]{32}$")


class TenantCapacityExceeded(RuntimeError):
    """The bounded in-process tenant runtime pool is full."""


@dataclass(slots=True)
class TenantRuntime:
    host: ReferenceHost
    rlm: PublicRlmCoordinator
    ownership: RuntimeOwnershipLock
    principal: PrincipalRef
    session: SessionRef

    def close(self) -> None:
        self.host.close()
        self.rlm.close()
        self.ownership.close()


@dataclass(slots=True)
class _TenantEntry:
    runtime: TenantRuntime
    leases: int
    last_used: int
    closing: bool = False


@dataclass(slots=True)
class TenantRuntimeLease:
    _pool: TenantRuntimePool
    _tenant_key: str
    runtime: TenantRuntime
    _released: bool = False

    def __enter__(self) -> TenantRuntime:
        return self.runtime

    def __exit__(self, *_exc_info: object) -> None:
        if not self._released:
            self._released = True
            self._pool._release(self._tenant_key)


class TenantRuntimePool:
    """Lease bounded per-tenant runtimes and evict only idle least-recently-used owners."""

    def __init__(
        self,
        data_root: Path,
        *,
        max_active_tenants: int = 128,
        max_rlm_jobs_per_tenant: int = 256,
    ) -> None:
        if max_active_tenants < 1 or max_active_tenants > 10_000:
            raise ValueError("max_active_tenants must be between 1 and 10000")
        if max_rlm_jobs_per_tenant < 1 or max_rlm_jobs_per_tenant > 100_000:
            raise ValueError("max_rlm_jobs_per_tenant must be between 1 and 100000")
        data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.data_root = data_root.resolve()
        _tighten_directory(self.data_root)
        self._max_active_tenants = max_active_tenants
        self._max_rlm_jobs_per_tenant = max_rlm_jobs_per_tenant
        self._lock = threading.RLock()
        self._runtimes: dict[str, _TenantEntry] = {}
        self._use_counter = 0
        self._closed = False

    def acquire(self, identity: PublicIdentity) -> TenantRuntimeLease:
        if not _TENANT_KEY.fullmatch(identity.tenant_key):
            raise ValueError("tenant identity is not a path-safe opaque digest")
        with self._lock:
            if self._closed:
                raise RuntimeError("tenant runtime pool is closed")
            existing = self._runtimes.get(identity.tenant_key)
            if existing is not None:
                if existing.closing:
                    raise TenantCapacityExceeded("tenant runtime is closing")
                existing.leases += 1
                existing.last_used = self._next_use()
                return TenantRuntimeLease(self, identity.tenant_key, existing.runtime)
            if len(self._runtimes) >= self._max_active_tenants:
                idle = [
                    (tenant_key, entry)
                    for tenant_key, entry in self._runtimes.items()
                    if entry.leases == 0
                ]
                if not idle:
                    raise TenantCapacityExceeded("all bounded tenant runtimes are leased")
                evicted_key, evicted_entry = min(
                    idle,
                    key=lambda item: (item[1].last_used, item[0]),
                )
                evicted_entry.closing = True
                evicted_entry.runtime.close()
                del self._runtimes[evicted_key]
            tenant_directory = (self.data_root / "tenants" / identity.tenant_key).resolve()
            if not tenant_directory.is_relative_to(self.data_root):  # pragma: no cover
                raise ValueError("tenant runtime path escaped the configured data root")
            tenant_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            _tighten_directory(tenant_directory)
            database_path = tenant_directory / "runtime.sqlite3"
            ownership = RuntimeOwnershipLock(database_path)
            try:
                host = ReferenceHost(
                    database_path,
                    programmable_backend="plain",
                    enable_durable_dispatch=False,
                )
            except BaseException:
                ownership.close()
                raise
            principal = PrincipalRef(value=identity.principal_id)
            try:
                rlm = PublicRlmCoordinator(
                    database_path,
                    principal_id=principal.value,
                    now_ms=host.now_ms,
                    max_jobs=self._max_rlm_jobs_per_tenant,
                )
            except BaseException:
                try:
                    host.close()
                finally:
                    ownership.close()
                raise
            runtime = TenantRuntime(
                host=host,
                rlm=rlm,
                ownership=ownership,
                principal=principal,
                session=SessionRef(value=identity.session_id),
            )
            self._runtimes[identity.tenant_key] = _TenantEntry(
                runtime=runtime,
                leases=1,
                last_used=self._next_use(),
            )
            return TenantRuntimeLease(self, identity.tenant_key, runtime)

    def _next_use(self) -> int:
        self._use_counter += 1
        return self._use_counter

    def _release(self, tenant_key: str) -> None:
        with self._lock:
            entry = self._runtimes.get(tenant_key)
            if entry is None:
                return
            if entry.leases < 1:  # pragma: no cover - internal lease invariant
                raise RuntimeError("tenant runtime lease underflow")
            entry.leases -= 1
            entry.last_used = self._next_use()

    def tenant_database_path(self, tenant_key: str) -> Path:
        if not _TENANT_KEY.fullmatch(tenant_key):
            raise ValueError("tenant key is invalid")
        return self.data_root / "tenants" / tenant_key / "runtime.sqlite3"

    def close(self) -> None:
        with self._lock:
            if self._closed and not self._runtimes:
                return
            self._closed = True
            for entry in self._runtimes.values():
                entry.closing = True
            runtimes = tuple(
                (tenant_key, entry.runtime) for tenant_key, entry in self._runtimes.items()
            )
        first_error: BaseException | None = None
        for tenant_key, runtime in runtimes:
            try:
                runtime.close()
            except BaseException as error:  # pragma: no cover - best-effort drain
                if first_error is None:
                    first_error = error
            else:
                with self._lock:
                    current = self._runtimes.get(tenant_key)
                    if current is not None and current.runtime is runtime:
                        del self._runtimes[tenant_key]
        if first_error is not None:
            raise first_error


def _tighten_directory(path: Path) -> None:
    if os.name != "nt":
        path.chmod(0o700)


__all__ = [
    "TenantCapacityExceeded",
    "TenantRuntime",
    "TenantRuntimeLease",
    "TenantRuntimePool",
]
