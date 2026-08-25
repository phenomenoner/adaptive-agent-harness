from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from mcp import Client

import aar.runtime.provider_ready_startup as startup_module
import aar.runtime.reference_host as reference_host_module
from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.canonical import canonical_sha256
from aar.mcp.server import build_server
from aar.provider_ready_install_models import InstallCandidateFactoryEntry
from aar.provider_ready_models import GrantBudgetCeiling, MethodAdapterManifest
from aar.provider_ready_package_factory import (
    PACKAGE_FACTORY_DECLARATIONS,
    PACKAGE_FACTORY_WHEEL_MEMBER,
)
from aar.provider_ready_runtime_models import (
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    WorkbenchGrantSet,
)
from aar.runtime.migrations import apply_registry_v6, create_sqlite_backup
from aar.runtime.model_broker import ReferenceModelBroker, StaticModelBrokerRegistry
from aar.runtime.provider_ready_activation import (
    ProviderReadyActivationCoordinator,
    ProviderReadyActivationStore,
)
from aar.runtime.provider_ready_startup import ProviderReadyStartup, ProviderReadyStartupError
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import OperationRegistry
from aar.runtime.supervisor import SupervisorService
from aar.runtime.workspace_models import ProgrammableWorkspaceHandle, WorkspaceBackendDescriptor
from aar.schemas import SessionRef, WorkspaceRef

ROOT = Path(__file__).resolve().parents[1]
WORKBENCH_SDD = ROOT / "docs/sdd/aar-rlm-native-workbench-v2"
NOW_MS = 1_700_000_000_000
DIGEST = canonical_sha256({"fixture": "c2-provider-ready-startup"})
OTHER_DIGEST = canonical_sha256({"fixture": "wrong-provider-ready-binding"})
PRINCIPAL = "principal-local"
SESSION = "session-local"
ROUTE_PROFILE_ID = "route-primary"
CAPABILITIES = tuple(
    sorted(
        (
            "artifact.write",
            "effect.propose",
            "evidence.query",
            "model.request",
            "rlm.workbench.execute",
            "subagent.result",
            "subagent.submit",
        )
    )
)
BUDGET = GrantBudgetCeiling(
    wall_time_ms=900_000,
    model_requests=40,
    input_tokens=1_000_000,
    output_tokens=500_000,
    child_operations=8,
    artifact_bytes=33_554_432,
)


class _GrantAdmissionIPythonWorkspaceBackend:
    """No-process backend: this shard owns grant admission, not IPython startup."""

    descriptor = WorkspaceBackendDescriptor.issue(
        kind="ipython",
        version="grant-admission-test",
        checkpoint_formats=("aar.workspace-checkpoint.v1",),
        features=(),
    )

    def __init__(self, *, artifact_sink: object, worker_manager: object) -> None:
        del artifact_sink, worker_manager

    def create(self, workspace: WorkspaceRef, session: SessionRef) -> ProgrammableWorkspaceHandle:
        del session
        return ProgrammableWorkspaceHandle(
            workspace=workspace,
            backend=self.descriptor,
            generation=1,
            revision=0,
        )

    def attach(
        self,
        handle: ProgrammableWorkspaceHandle,
        session: SessionRef,
    ) -> ProgrammableWorkspaceHandle:
        del session
        return handle

    def bind_broker_handler(self, handler: object) -> None:
        del handler

    def shutdown(self) -> None:
        return None


def _prepare_v6_registry(database: Path, snapshot_path: Path) -> None:
    migration_sql = WORKBENCH_SDD / "migration-v6.sql"
    registry = OperationRegistry(database, lambda: NOW_MS)
    registry.close()
    snapshot = create_sqlite_backup(database, snapshot_path)
    migration_bytes = migration_sql.read_bytes()
    payload: dict[str, object] = {
        "schema_version": "aar.migration-v6-attestation-payload.v1",
        "migration_version": 6,
        "cutover_epoch": "cutover-provider-ready-c2-test",
        "snapshot_id": "snapshot-provider-ready-c2-test",
        "snapshot_sha256": snapshot.sha256,
        "snapshot_size_bytes": snapshot.size_bytes,
        "canonical_v5_row_set_digest": "sha256:" + "2" * 64,
        "source_commit": "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "wheel_digest": "sha256:" + "3" * 64,
        "profile_digest": "sha256:" + "4" * 64,
        "skill_digest": "sha256:" + "5" * 64,
        "contract_manifest_digest": "sha256:" + "6" * 64,
        "migration_sql_digest": "sha256:" + hashlib.sha256(migration_bytes).hexdigest(),
        "external_authority_store_id": "cutover-authority-provider-ready-c2-test",
        "external_authority_prepared_digest": "sha256:" + "8" * 64,
        "started_at_unix_ms": NOW_MS,
        "completed_at_unix_ms": NOW_MS + 1,
        "foreign_key_violation_count": 0,
        "integrity_result": "ok",
    }
    apply_registry_v6(
        database,
        migration_sql_bytes=migration_bytes,
        attestation={
            "attestation": payload,
            "attestation_digest": canonical_sha256(payload),
        },
    )


def _route_catalog(
    *,
    cache_policy: str = "disabled",
    profile_id: str = ROUTE_PROFILE_ID,
) -> ModelRouteCatalog:
    profile = ModelRouteProfile(
        profile_id=profile_id,
        provider_driver="reference-driver",
        provider="reference",
        model="deterministic",
        reasoning_effort="high",
        max_output_tokens=8_192,
        fallback_policy="none",
        cache_policy=cache_policy,
    )
    return ModelRouteCatalog.issue((profile,))


def _route_owner(catalog: ModelRouteCatalog) -> StaticModelBrokerRegistry:
    return StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker() for profile in catalog.profiles},
    )


def _adapters(
    factory_digest: str,
    *,
    reference_evidence: bool,
) -> tuple[MethodAdapterManifest, ...]:
    adapters = []
    for declaration in PACKAGE_FACTORY_DECLARATIONS:
        backend_kind = (
            "caller_driver"
            if declaration.method == "model.request"
            else "reference"
            if declaration.method == "evidence.query" and reference_evidence
            else "native"
        )
        adapters.append(
            MethodAdapterManifest.issue(
                schema_version="aar.method-adapter-manifest.v1",
                method=declaration.method,
                contract_id=declaration.contract_id,
                request_schema_digest=declaration.request_schema_digest,
                response_schema_digest=declaration.response_schema_digest,
                backend_kind=backend_kind,
                factory_id=declaration.factory_id,
                factory_digest=factory_digest,
                adapter_id=f"adapter-{declaration.method.replace('.', '-')}",
                adapter_generation_policy="runtime_generation",
                reference_only=backend_kind == "reference",
                evidence_tier=("unknown" if backend_kind == "reference" else "host_receipt_bound"),
                lookup_supported=True,
                cancel_supported=False,
            )
        )
    return tuple(adapters)


def _preflight(
    tmp_path: Path,
    *,
    reference_evidence: bool = True,
) -> tuple[Any, Path]:
    runtime_home = tmp_path / "published-runtime"
    authority = runtime_home / "authority"
    authority.mkdir(parents=True, mode=0o700)
    loaded = startup_module.load_package_factory_bindings()
    factory_digest = loaded[0].implementation_digest
    catalog = _route_catalog()
    grant_policy = SimpleNamespace(
        principal_patterns=(PRINCIPAL,),
        capabilities=CAPABILITIES,
        budget_ceiling=BUDGET,
        max_deadline_ms=900_000,
    )
    routes = SimpleNamespace(
        catalog_digest=catalog.catalog_digest,
        allowed_profile_ids=(ROUTE_PROFILE_ID,),
        fallback_policy="none",
        cache_policy="disabled",
    )
    intent = SimpleNamespace(
        profile_id="profile-primary",
        adapters=_adapters(
            factory_digest,
            reference_evidence=reference_evidence,
        ),
        runtime=SimpleNamespace(
            required_registry_version=6,
            programmable_backend="ipython",
        ),
        routes=routes,
        grant_policy=grant_policy,
    )
    profile = SimpleNamespace(intent=intent, profile_digest=DIGEST)
    receipt = SimpleNamespace(
        factory_entries=tuple(
            InstallCandidateFactoryEntry(
                factory_id=declaration.factory_id,
                wheel_member=PACKAGE_FACTORY_WHEEL_MEMBER,
                implementation_digest=factory_digest,
            )
            for declaration in sorted(
                PACKAGE_FACTORY_DECLARATIONS,
                key=lambda item: item.factory_id,
            )
        )
    )
    readback = SimpleNamespace(
        target=runtime_home,
        profile=profile,
        receipt=receipt,
        route_catalog=catalog,
        authority=SimpleNamespace(
            activation_generation=1,
            authority_digest=DIGEST,
        ),
    )
    return readback, authority


def _grant_set(
    generation: int,
    readback: Any,
    capability_digest: str,
) -> WorkbenchGrantSet:
    intent = readback.profile.intent
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=generation,
        activation_generation=readback.authority.activation_generation,
        profile_id=intent.profile_id,
        profile_digest=readback.profile.profile_digest,
        activation_authority_digest=readback.authority.authority_digest,
        capability_digest=capability_digest,
        route_catalog_digest=intent.routes.catalog_digest,
        principal_ids=intent.grant_policy.principal_patterns,
        session_binding_policy="bind_exact_request_session",
        capabilities=intent.grant_policy.capabilities,
        budget_ceiling=intent.grant_policy.budget_ceiling,
        max_ttl_ms=intent.grant_policy.max_deadline_ms,
    )


def _startup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    factory=_grant_set,
    reference_evidence: bool = True,
) -> tuple[ProviderReadyStartup, Any, Path]:
    readback, authority = _preflight(
        tmp_path,
        reference_evidence=reference_evidence,
    )
    monkeypatch.setattr(startup_module, "verify_published_install", lambda *_a, **_k: readback)
    startup = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        factory,
    )
    return startup, readback, authority


def _host_options(readback: Any) -> dict[str, Any]:
    return {
        "programmable_backend": "ipython",
        "model_broker_registry": _route_owner(readback.route_catalog),
        "default_model_route_profile": ROUTE_PROFILE_ID,
    }


def _execute_arguments(
    capabilities: dict[str, Any],
    *,
    grant_id: str,
    suffix: str,
) -> dict[str, Any]:
    fixture = json.loads(
        (WORKBENCH_SDD / "fixtures/valid-workbench-execute.json").read_text(encoding="utf-8")
    )
    fixture["context"] = {
        "principal_id": PRINCIPAL,
        "session_id": SESSION,
        "runtime_generation": capabilities["ready"]["runtime_generation"],
        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
        "deadline_unix_ms": NOW_MS + BUDGET.wall_time_ms,
        "schema_version": "aar.mcp-rlm-workbench-context.v1",
        "request_id": f"request-{suffix}",
        "idempotency_key": f"idempotency-{suffix}",
        "grant_ids": [grant_id],
        "budget_wall_time_ms": BUDGET.wall_time_ms,
        "budget_model_requests": BUDGET.model_requests,
        "budget_input_tokens": BUDGET.input_tokens,
        "budget_output_tokens": BUDGET.output_tokens,
        "budget_child_operations": BUDGET.child_operations,
        "budget_artifact_bytes": BUDGET.artifact_bytes,
    }
    fixture["start_only"] = True
    return fixture


def test_startup_projects_loaded_factories_and_replays_exact_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[int, Any, str]] = []

    def factory(generation: int, readback: Any, capability_digest: str) -> WorkbenchGrantSet:
        calls.append((generation, readback, capability_digest))
        return _grant_set(generation, readback, capability_digest)

    startup, readback, _authority = _startup(tmp_path, monkeypatch, factory=factory)

    assert startup.preflight() is readback
    assert tuple(
        binding.implementation.__qualname__ for binding in startup.loaded_factories
    ) == tuple(
        f"BoundBrokerFacade.{declaration.method.replace('.', '_')}"
        for declaration in PACKAGE_FACTORY_DECLARATIONS
    )
    with pytest.raises(ProviderReadyStartupError, match="bound host factory owner"):
        startup.backend_availability(1)
    with pytest.raises(ProviderReadyStartupError, match="bound host factory owner"):
        startup.activate(1)
    assert calls == []
    assert not (_authority / "runtime-generations").exists()

    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "factory-binding.snapshot.sqlite3")
    host = ReferenceHost(
        database,
        provider_ready_startup=startup,
        **_host_options(readback),
    )
    try:
        availability = startup.backend_availability(1)
        assert availability["model.request"]["adapter_generation"] == 1
        assert availability["evidence.query"] == {
            "backend_kind": "reference",
            "configured": True,
            "reference_only": True,
            "adapter_id": None,
            "adapter_generation": None,
            "evidence_tier": "unknown",
        }
        result = startup.activate(1)
        assert startup.activate(1) is result
        assert len(calls) == 1
        assert startup.coordinator.store.read(1) == result.grant_set
    finally:
        host.close()

    with pytest.raises(ProviderReadyStartupError, match="different runtime generation"):
        startup.activate(2)


def test_loaded_factory_digest_drift_fails_before_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup, _readback, authority = _startup(tmp_path, monkeypatch)
    loaded = startup_module.load_package_factory_bindings()
    drifted = (replace(loaded[0], implementation_digest=OTHER_DIGEST), *loaded[1:])
    monkeypatch.setattr(startup_module, "load_package_factory_bindings", lambda: drifted)

    with pytest.raises(ProviderReadyStartupError, match="loaded package factory digest"):
        startup.activate(1)
    assert not (authority / "runtime-generations").exists()


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("route_catalog_digest", OTHER_DIGEST),
        ("principal_ids", ("principal-other",)),
        ("capabilities", ("rlm.workbench.execute",)),
        (
            "budget_ceiling",
            GrantBudgetCeiling(
                wall_time_ms=60_000,
                model_requests=1,
                input_tokens=1,
                output_tokens=1,
                child_operations=0,
                artifact_bytes=0,
            ),
        ),
        ("max_ttl_ms", 60_000),
        ("session_binding_policy", "wrong-session-policy"),
    ),
)
def test_grant_set_must_equal_installed_route_and_grant_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    def drifted_factory(
        generation: int, readback: Any, capability_digest: str
    ) -> WorkbenchGrantSet:
        return _grant_set(generation, readback, capability_digest).model_copy(update={field: value})

    startup, readback, authority = _startup(
        tmp_path,
        monkeypatch,
        factory=drifted_factory,
    )
    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / f"grant-policy-{field}.snapshot.sqlite3")
    with pytest.raises(ProviderReadyStartupError, match="route, policy"):
        ReferenceHost(
            database,
            provider_ready_startup=startup,
            **_host_options(readback),
        )
    assert not (authority / "runtime-generations").exists()


def test_host_configuration_mismatch_fails_before_runtime_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup, readback, authority = _startup(tmp_path, monkeypatch)
    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "mismatch.snapshot.sqlite3")
    owner = _route_owner(readback.route_catalog)
    try:
        with pytest.raises(ProviderReadyStartupError, match="programmable backend"):
            ReferenceHost(
                database,
                programmable_backend="plain",
                model_broker_registry=owner,
                default_model_route_profile=ROUTE_PROFILE_ID,
                provider_ready_startup=startup,
            )
    finally:
        owner.close()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT generation FROM runtime_meta WHERE singleton=1").fetchone()[
                0
            ]
            == 0
        )
    assert not (authority / "runtime-generations").exists()


def test_route_owner_mismatch_fails_before_runtime_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup, readback, authority = _startup(tmp_path, monkeypatch)
    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "route-mismatch.snapshot.sqlite3")
    wrong_catalog = _route_catalog(profile_id="route-other")
    owner = _route_owner(wrong_catalog)
    try:
        with pytest.raises(ProviderReadyStartupError, match="catalog differs"):
            ReferenceHost(
                database,
                programmable_backend="ipython",
                model_broker_registry=owner,
                default_model_route_profile="route-other",
                provider_ready_startup=startup,
            )
    finally:
        owner.close()
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT generation FROM runtime_meta WHERE singleton=1").fetchone()[
                0
            ]
            == 0
        )
    assert not (authority / "runtime-generations").exists()


def test_reference_host_projects_capability_before_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readback, authority = _preflight(tmp_path)
    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "host.snapshot.sqlite3")
    events: list[str] = []
    observed_capability: list[str] = []

    def verify(*_args: object, **_kwargs: object) -> Any:
        events.append("preflight")
        return readback

    monkeypatch.setattr(startup_module, "verify_published_install", verify)

    def factory(generation: int, observed: Any, capability_digest: str) -> WorkbenchGrantSet:
        events.append(f"factory:{generation}")
        observed_capability.append(capability_digest)
        return _grant_set(generation, observed, capability_digest)

    startup = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        factory,
    )
    host = ReferenceHost(
        database,
        provider_ready_startup=startup,
        **_host_options(readback),
    )
    try:
        assert host.runtime_generation == 1
        assert startup.activation_result.runtime_generation == host.runtime_generation
        assert events == ["preflight", "factory:1"]
        assert observed_capability == [canonical_sha256(host.workbench_capability().root)]
        methods = {row["method"]: row for row in host.workbench_capability().root["methods"]}
        assert methods["model.request"]["adapter_generation"] == 1
        assert methods["evidence.query"]["adapter_generation"] is None
        assert host.rlm_workbench is not None
    finally:
        host.close()


def test_reference_host_restart_publishes_successor_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readback, authority = _preflight(tmp_path)
    database = readback.target / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "restart.snapshot.sqlite3")
    monkeypatch.setattr(
        startup_module,
        "verify_published_install",
        lambda *_args, **_kwargs: readback,
    )

    first = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        _grant_set,
    )
    first_host = ReferenceHost(
        database,
        provider_ready_startup=first,
        **_host_options(readback),
    )
    assert first_host.runtime_generation == 1
    prior_grant = first.coordinator.issue_session_grant(
        principal_id=PRINCIPAL,
        session_id=SESSION,
        capability="rlm.workbench.execute",
        issued_at_unix_ms=NOW_MS,
        ttl_ms=60_000,
        policy_approved=True,
        grant_id="grant-prior-generation",
    )
    first_host.close()

    second = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        _grant_set,
    )
    second_host = ReferenceHost(
        database,
        provider_ready_startup=second,
        **_host_options(readback),
    )
    try:
        assert second_host.runtime_generation == 2
        assert second.grant_set.runtime_generation == 2
        with pytest.raises(Exception, match="not owned by this activation process"):
            second.coordinator.accept_session_grant(
                prior_grant.grant_id,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                runtime_generation=2,
                now_unix_ms=NOW_MS + 1,
            )
        assert first.coordinator.store.path_for_generation(1).is_file()
        assert second.coordinator.store.path_for_generation(2).is_file()
    finally:
        second_host.close()


@pytest.mark.anyio
async def test_supervisor_failure_has_no_discovery_after_provider_ready_barrier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    readback, authority = _preflight(tmp_path)
    runtime_home = readback.target
    database = runtime_home / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "supervisor.snapshot.sqlite3")
    private_dir_observed = False

    def verify(*_args: object, **_kwargs: object) -> Any:
        nonlocal private_dir_observed
        private_dir_observed = (runtime_home / "supervisor").exists()
        return readback

    monkeypatch.setattr(startup_module, "verify_published_install", verify)

    def fail_factory(
        _generation: int, _readback: Any, _capability_digest: str
    ) -> WorkbenchGrantSet:
        raise RuntimeError("activation composition failed")

    startup = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        fail_factory,
    )
    service = SupervisorService(
        runtime_home,
        programmable_backend="ipython",
        model_broker_registry=_route_owner(readback.route_catalog),
        default_model_route_profile=ROUTE_PROFILE_ID,
        provider_ready_startup=startup,
    )
    with pytest.raises(RuntimeError, match="activation composition failed"):
        await service._start()

    assert private_dir_observed is False
    assert service.discovery is None
    assert not (runtime_home / "supervisor" / "discovery.json").exists()


def test_mcp_uses_only_current_memory_session_grants_in_provider_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup, readback, _authority = _startup(
        tmp_path,
        monkeypatch,
        reference_evidence=False,
    )
    database = startup.runtime_home / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "mcp.snapshot.sqlite3")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        _GrantAdmissionIPythonWorkspaceBackend,
    )
    application = build_server(
        database,
        enable_durable_dispatch=False,
        now_ms=lambda: NOW_MS,
        provider_ready_startup=startup,
        **_host_options(readback),
    )

    async def scenario() -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
        async with Client(application.server) as client:
            capabilities_result = await client.call_tool("aar_capabilities")
            assert not capabilities_result.is_error
            assert capabilities_result.structured_content is not None
            capabilities = capabilities_result.structured_content

            denied_payloads: list[dict[str, Any]] = []
            no_auto_result = await client.call_tool(
                "aar_rlm_workbench_execute",
                _execute_arguments(
                    capabilities,
                    grant_id="grant-never-issued",
                    suffix="no-auto-issue",
                ),
            )
            assert not no_auto_result.is_error and no_auto_result.structured_content is not None
            denied_payloads.append(no_auto_result.structured_content)

            issued = startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS,
                ttl_ms=900_000,
                policy_approved=True,
                grant_id="grant-current-workbench",
            )
            wrong_session = startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id="session-other",
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS,
                ttl_ms=900_000,
                policy_approved=True,
                grant_id="grant-wrong-session",
            )
            revoked = startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS,
                ttl_ms=900_000,
                policy_approved=True,
                grant_id="grant-revoked",
            )
            startup.coordinator.revoke_session_grant(revoked)
            expired = startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS - 2_000,
                ttl_ms=1_000,
                policy_approved=True,
                grant_id="grant-expired",
            )

            for suffix, grant_id in (
                ("static-denied", "reference-grant-rlm-workbench-execute"),
                ("wrong-session", wrong_session.grant_id),
                ("revoked", revoked.grant_id),
                ("expired", expired.grant_id),
            ):
                denied_result = await client.call_tool(
                    "aar_rlm_workbench_execute",
                    _execute_arguments(
                        capabilities,
                        grant_id=grant_id,
                        suffix=suffix,
                    ),
                )
                assert not denied_result.is_error
                assert denied_result.structured_content is not None
                denied_payloads.append(denied_result.structured_content)

            mixed_arguments = _execute_arguments(
                capabilities,
                grant_id=issued.grant_id,
                suffix="mixed",
            )
            mixed_arguments["context"]["grant_ids"] = sorted(
                (issued.grant_id, "reference-grant-rlm-workbench-execute")
            )
            mixed_result = await client.call_tool(
                "aar_rlm_workbench_execute",
                mixed_arguments,
            )
            assert not mixed_result.is_error and mixed_result.structured_content is not None
            denied_payloads.append(mixed_result.structured_content)

            accepted_result = await client.call_tool(
                "aar_rlm_workbench_execute",
                _execute_arguments(
                    capabilities,
                    grant_id=issued.grant_id,
                    suffix="current-accepted",
                ),
            )
            assert not accepted_result.is_error and accepted_result.structured_content is not None
            workbench_result = await client.call_tool(
                "aar_rlm_workbench_capabilities",
                {
                    "context": {
                        "principal_id": PRINCIPAL,
                        "session_id": SESSION,
                        "runtime_generation": capabilities["ready"]["runtime_generation"],
                        "capability_digest": capabilities["ready"]["capabilities"]["digest"],
                        "deadline_unix_ms": NOW_MS + 1_000,
                    }
                },
            )
            assert not workbench_result.is_error
            assert workbench_result.structured_content is not None
            return capabilities, denied_payloads, accepted_result.structured_content

    try:
        capabilities, denied_payloads, accepted = asyncio.run(scenario())
    finally:
        application.close()

    assert all(payload["code"] == "GRANT_DENIED" for payload in denied_payloads)
    assert all(payload["operation"] is None for payload in denied_payloads)
    assert "phase" in accepted, accepted
    assert accepted["phase"] == "preparing_workspace"
    projection = capabilities["provider_ready"]
    assert projection["runtime_generation"] == capabilities["ready"]["runtime_generation"]
    assert projection["grant_set_digest"] == startup.grant_set.grant_set_digest
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()[0] == 1


def test_mcp_grant_denial_precedes_capability_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    startup, readback, _authority = _startup(tmp_path, monkeypatch)
    database = startup.runtime_home / "reference.sqlite3"
    _prepare_v6_registry(database, tmp_path / "mcp-ordering.snapshot.sqlite3")
    application = build_server(
        database,
        enable_durable_dispatch=False,
        now_ms=lambda: NOW_MS,
        provider_ready_startup=startup,
        **_host_options(readback),
    )

    async def scenario() -> tuple[dict[str, Any], dict[str, Any]]:
        async with Client(application.server) as client:
            capabilities_result = await client.call_tool("aar_capabilities")
            assert not capabilities_result.is_error
            assert capabilities_result.structured_content is not None
            capabilities = capabilities_result.structured_content
            denied_result = await client.call_tool(
                "aar_rlm_workbench_execute",
                _execute_arguments(
                    capabilities,
                    grant_id="grant-never-issued",
                    suffix="ordering-denied",
                ),
            )
            assert not denied_result.is_error and denied_result.structured_content is not None
            grant = startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS,
                ttl_ms=900_000,
                policy_approved=True,
                grant_id="grant-ordering-current",
            )
            unavailable_result = await client.call_tool(
                "aar_rlm_workbench_execute",
                _execute_arguments(
                    capabilities,
                    grant_id=grant.grant_id,
                    suffix="ordering-unavailable",
                ),
            )
            assert not unavailable_result.is_error
            assert unavailable_result.structured_content is not None
            return denied_result.structured_content, unavailable_result.structured_content

    try:
        denied, unavailable = asyncio.run(scenario())
    finally:
        application.close()

    assert denied["code"] == "GRANT_DENIED"
    assert denied["operation"] is None
    assert unavailable["code"] == "CAPABILITY_UNAVAILABLE"
    assert unavailable["operation"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()[0] == 0


anyio_backend = "asyncio"
