from __future__ import annotations

import asyncio
import json
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import test_supervisor_provider_ready_integration as provider_ready
from mcp import Client

import aar.runtime.provider_ready_startup as startup_module
import aar.runtime.reference_host as reference_host_module
from aar.canonical import canonical_json_bytes
from aar.mcp.server import build_server
from aar.provider_ready_runtime_models import WorkbenchGrantSet
from aar.rlm_workbench_models import (
    CallerWorkClaimInput,
    build_workbench_capability,
    derive_required_workbench_methods,
    normalize_workbench_planner_mode,
    workbench_method_capabilities,
)
from aar.runtime.provider_ready_activation import (
    ProviderReadyActivationCoordinator,
    ProviderReadyActivationStore,
    SessionGrantDenied,
)
from aar.runtime.provider_ready_startup import ProviderReadyStartup, ProviderReadyStartupError
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.supervisor import SupervisorService
from aar.schemas import Budget

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "docs"
    / "sdd"
    / "aar-rlm-native-workbench-v2"
    / "fixtures"
    / "valid-workbench-execute.json"
)
NOW_MS = provider_ready.NOW_MS
PRINCIPAL = provider_ready.PRINCIPAL
SESSION = provider_ready.SESSION
OTHER_DIGEST = provider_ready.OTHER_DIGEST
BUDGET = provider_ready.BUDGET


METHOD_GRANT_CAPABILITIES = (
    "artifact.write",
    "model.request",
    "subagent.result",
    "subagent.submit",
)


def _fixture() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _issue_grants(
    startup: ProviderReadyStartup,
    *,
    suffix: str,
    capabilities: tuple[str, ...] = METHOD_GRANT_CAPABILITIES,
) -> tuple[str, ...]:
    grants = [
        startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability="rlm.workbench.execute",
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id=f"{suffix}-rlm",
        )
    ]
    grants.extend(
        startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability=capability,
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id=f"{suffix}-{capability.replace('.', '-')}",
        )
        for capability in capabilities
    )
    return tuple(sorted(grant.grant_id for grant in grants))


def _provider_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    reference_evidence: bool = False,
) -> tuple[Any, ProviderReadyStartup, Any, Path]:
    startup, readback, _authority = provider_ready._startup(
        tmp_path,
        monkeypatch,
        reference_evidence=reference_evidence,
    )
    database = startup.runtime_home / "reference.sqlite3"
    provider_ready._prepare_v6_registry(database, tmp_path / "acceptance.snapshot.sqlite3")
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        provider_ready._GrantAdmissionIPythonWorkspaceBackend,
    )
    application = build_server(
        database,
        enable_durable_dispatch=False,
        now_ms=lambda: NOW_MS,
        provider_ready_startup=startup,
        **provider_ready._host_options(readback),
    )
    return application, startup, readback, database


def _call_workbench(
    client: Client,
    capabilities: dict[str, Any],
    *,
    grant_ids: tuple[str, ...],
    suffix: str,
    spec: dict[str, Any] | None = None,
) -> Any:
    arguments = provider_ready._execute_arguments(
        capabilities,
        suffix=suffix,
        grant_ids=grant_ids,
    )
    if spec is not None:
        arguments["spec"] = spec
    return client.call_tool("aar_rlm_workbench_execute", arguments)


@pytest.mark.parametrize(
    ("row_id", "mode", "start_only", "expected"),
    (
        ("A-ADM-001", "caller_delegated", True, "caller_delegated_ticketed"),
        ("A-ADM-002", "service_managed", False, "service_managed"),
    ),
    ids=("A-ADM-001-caller-mode", "A-ADM-002-service-mode"),
)
def test_normalized_planner_mode_is_the_admission_axis(
    row_id: str,
    mode: str,
    start_only: bool,
    expected: str,
) -> None:
    del row_id
    document = _fixture()
    document["spec"]["model"]["execution_mode"] = mode
    document["spec"]["budgets"]["max_artifact_bytes"] = 0
    document["spec"]["budgets"]["max_subagent_calls"] = 0
    document["start_only"] = start_only
    assert normalize_workbench_planner_mode(document) == expected
    assert derive_required_workbench_methods(document) == ("model.request",)
    assert workbench_method_capabilities(("model.request",)) == ("model.request",)


@pytest.mark.parametrize(
    ("row_id", "artifact_bytes", "subagent_calls", "effective", "expected"),
    (
        (
            "A-ADM-003",
            0,
            0,
            ("artifact.write", "evidence.query", "effect.propose"),
            ("model.request", "evidence.query", "effect.propose"),
        ),
        (
            "A-ADM-004",
            1,
            0,
            ("artifact.write",),
            ("model.request", "artifact.put"),
        ),
        (
            "A-ADM-005",
            0,
            0,
            ("subagent.result", "subagent.submit"),
            ("model.request",),
        ),
        (
            "A-ADM-006",
            0,
            1,
            ("subagent.result", "subagent.submit"),
            ("model.request", "subagent.submit", "subagent.result"),
        ),
        (
            "A-ADM-007",
            0,
            0,
            ("evidence.query",),
            ("model.request", "evidence.query"),
        ),
        (
            "A-ADM-008",
            0,
            0,
            ("effect.propose",),
            ("model.request", "effect.propose"),
        ),
    ),
    ids=(
        "A-ADM-003-artifact-zero",
        "A-ADM-004-artifact-positive",
        "A-ADM-005-subagent-zero",
        "A-ADM-006-subagent-positive",
        "A-ADM-007-evidence-grant",
        "A-ADM-008-effect-grant",
    ),
)
def test_method_admission_is_pure_over_budget_and_effective_grant_axes(
    row_id: str,
    artifact_bytes: int,
    subagent_calls: int,
    effective: tuple[str, ...],
    expected: tuple[str, ...],
) -> None:
    del row_id
    document = _fixture()
    document["spec"]["budgets"]["max_artifact_bytes"] = artifact_bytes
    document["spec"]["budgets"]["max_subagent_calls"] = subagent_calls
    assert (
        derive_required_workbench_methods(
            document,
            effective_capabilities=effective,
        )
        == expected
    )


def test_A_ACT_004_capability_evidence_does_not_upgrade_method_classification() -> None:
    document = _fixture()
    document["spec"]["budgets"]["max_artifact_bytes"] = 0
    document["spec"]["budgets"]["max_subagent_calls"] = 0
    classified = derive_required_workbench_methods(document)
    assert classified == ("model.request",)
    assert workbench_method_capabilities(classified) == ("model.request",)
    # A separate capability projection may truthfully contain reference rows,
    # but that metadata cannot add an operation or an implied method grant.
    capability = build_workbench_capability(
        {
            method: {
                "backend_kind": "reference",
                "configured": True,
                "reference_only": True,
                "adapter_id": None,
                "adapter_generation": None,
                "evidence_tier": "unknown",
            }
            for method in (
                "model.request",
                "subagent.submit",
                "subagent.result",
                "evidence.query",
                "artifact.put",
                "effect.propose",
            )
        }
    )
    assert len(capability.root["methods"]) == 6
    assert derive_required_workbench_methods(document) == classified


def test_A_ADM_009_missing_implied_method_is_denied_before_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, database = _provider_application(tmp_path, monkeypatch)
    try:

        async def scenario() -> dict[str, Any]:
            async with Client(application.server) as client:
                capabilities_result = await client.call_tool("aar_capabilities")
                assert capabilities_result.structured_content is not None
                capabilities = capabilities_result.structured_content
                grant_ids = _issue_grants(startup, suffix="missing", capabilities=())
                result = await _call_workbench(
                    client,
                    capabilities,
                    grant_ids=grant_ids,
                    suffix="missing-implied",
                )
                assert result.structured_content is not None
                return result.structured_content

        failure = asyncio.run(scenario())
    finally:
        application.close()
    assert failure["code"] == "GRANT_DENIED"
    assert failure["operation"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()[0] == 0


def test_A_ADM_010_reference_only_required_method_is_grant_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, database = _provider_application(
        tmp_path,
        monkeypatch,
        reference_evidence=True,
    )
    try:

        async def scenario() -> dict[str, Any]:
            async with Client(application.server) as client:
                capabilities_result = await client.call_tool("aar_capabilities")
                assert capabilities_result.structured_content is not None
                capabilities = capabilities_result.structured_content
                grant_ids = _issue_grants(
                    startup,
                    suffix="reference-required",
                    capabilities=(*METHOD_GRANT_CAPABILITIES, "evidence.query"),
                )
                result = await _call_workbench(
                    client,
                    capabilities,
                    grant_ids=grant_ids,
                    suffix="reference-required",
                )
                assert result.structured_content is not None
                return result.structured_content

        failure = asyncio.run(scenario())
    finally:
        application.close()
    assert failure["code"] == "GRANT_DENIED"
    assert "current authority" in failure["message"]
    assert failure["operation"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_A_ADM_011_retired_factory_is_denied_before_generation_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup, readback, authority = provider_ready._startup(tmp_path, monkeypatch)
    adapters = list(readback.profile.intent.adapters)
    readback.profile.intent.adapters = tuple(adapters[:-1])
    with pytest.raises(ProviderReadyStartupError, match="adapters"):
        startup.activate(1)
    assert not (authority / "runtime-generations").exists()


def test_A_ADM_012_stale_factory_generation_is_denied_before_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, database = _provider_application(tmp_path, monkeypatch)
    try:
        current = application.host.workbench_capability()
        availability = {
            row["method"]: {
                key: row[key]
                for key in (
                    "backend_kind",
                    "configured",
                    "reference_only",
                    "adapter_id",
                    "adapter_generation",
                    "evidence_tier",
                )
            }
            for row in current.root["methods"]
        }
        availability["model.request"]["adapter_generation"] = (
            application.host.runtime_generation + 1
        )
        stale = build_workbench_capability(availability)
        monkeypatch.setattr(application.host, "workbench_capability", lambda: stale)

        async def scenario() -> dict[str, Any]:
            async with Client(application.server) as client:
                capabilities_result = await client.call_tool("aar_capabilities")
                assert capabilities_result.structured_content is not None
                capabilities = capabilities_result.structured_content
                result = await _call_workbench(
                    client,
                    capabilities,
                    grant_ids=_issue_grants(startup, suffix="stale"),
                    suffix="stale-generation",
                )
                assert result.structured_content is not None
                return result.structured_content

        failure = asyncio.run(scenario())
    finally:
        application.close()
    assert failure["code"] == "GRANT_DENIED"
    assert "stale authority" in failure["message"]
    assert failure["operation"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0


def test_A_GEN_013_factory_digest_mismatch_is_denied_before_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    startup, _readback, authority = provider_ready._startup(tmp_path, monkeypatch)
    loaded = startup_module.load_package_factory_bindings()
    drifted = (replace(loaded[0], implementation_digest=OTHER_DIGEST), *loaded[1:])
    monkeypatch.setattr(startup_module, "load_package_factory_bindings", lambda: drifted)
    with pytest.raises(ProviderReadyStartupError, match="loaded package factory digest"):
        startup.activate(1)
    assert not (authority / "runtime-generations").exists()


@pytest.mark.parametrize(
    ("row_id", "artifact_bytes", "raises"),
    (
        ("A-ADM-014", BUDGET.artifact_bytes - 1, False),
        ("A-ADM-015", BUDGET.artifact_bytes + 1, True),
    ),
    ids=("A-ADM-014-under-ceiling", "A-ADM-015-over-ceiling"),
)
def test_grant_budget_ceiling_is_checked_during_session_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row_id: str,
    artifact_bytes: int,
    raises: bool,
) -> None:
    del row_id
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        grant = startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability="rlm.workbench.execute",
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id="grant-budget-axis",
        )
        budget = Budget(
            wall_time_ms=BUDGET.wall_time_ms,
            model_requests=BUDGET.model_requests,
            input_tokens=BUDGET.input_tokens,
            output_tokens=BUDGET.output_tokens,
            child_operations=BUDGET.child_operations,
            artifact_bytes=artifact_bytes,
        )
        resolver = dict(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            runtime_generation=1,
            now_unix_ms=NOW_MS,
            deadline_unix_ms=NOW_MS + 900_000,
            required_capability="rlm.workbench.execute",
            budget=budget,
        )
        if raises:
            with pytest.raises(SessionGrantDenied, match="budget"):
                startup.resolve_session_grants((grant.grant_id,), **resolver)
        else:
            assert (
                startup.resolve_session_grants((grant.grant_id,), **resolver)[0].grant_id
                == grant.grant_id
            )
    finally:
        application.close()


def test_A_AUTH_001_activation_has_no_operation_ticket_credential_or_provider_effect(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, readback, database = _provider_application(tmp_path, monkeypatch)
    try:
        assert startup.activation_result.runtime_generation == 1
        assert startup.grant_set.runtime_generation == 1
        assert database.is_file()
        assert not (readback.target / "supervisor").exists()
        files = tuple(
            path.relative_to(startup.runtime_home / "authority")
            for path in (startup.runtime_home / "authority").rglob("*")
            if path.is_file()
        )
        assert files == (Path("runtime-generations/00000000000000000001/workbench-grant-set.json"),)
        assert not any("credential" in path.name or "ticket" in path.name for path in files)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM operations").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM rlm_workbench_jobs").fetchone()[0] == 0
    finally:
        application.close()


def test_A_GEN_014_generation_grant_set_is_canonical_mode_bound_and_read_back(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        result = startup.activation_result
        path = result.path
        assert path == (
            startup.runtime_home
            / "authority"
            / "runtime-generations"
            / "00000000000000000001"
            / "workbench-grant-set.json"
        )
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_bytes() == canonical_json_bytes(result.grant_set)
        assert startup.coordinator.store.read(1) == result.grant_set
    finally:
        application.close()


@pytest.mark.anyio
async def test_A_GEN_007_ready_follows_generation_grant_readback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readback, authority = provider_ready._preflight(tmp_path)
    database = readback.target / "reference.sqlite3"
    provider_ready._prepare_v6_registry(database, tmp_path / "ready-order.snapshot.sqlite3")
    monkeypatch.setattr(startup_module, "verify_published_install", lambda *_a, **_k: readback)
    startup = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        provider_ready._grant_set,
    )
    events: list[str] = []
    store = startup.coordinator.store
    read = store.read

    def observed_read(generation: int) -> WorkbenchGrantSet:
        events.append("grant-readback")
        return read(generation)

    monkeypatch.setattr(store, "read", observed_read)
    original_write = SupervisorService._write_private_file

    def observed_write(path: Path, content: bytes) -> None:
        if path == service.discovery_path:
            events.append("discovery")
            assert (
                startup.grant_set.runtime_generation == service.application.host.runtime_generation
            )
        original_write(path, content)

    monkeypatch.setattr(SupervisorService, "_write_private_file", staticmethod(observed_write))
    service = SupervisorService(
        readback.target,
        programmable_backend="ipython",
        model_broker_registry=provider_ready._route_owner(readback.route_catalog),
        default_model_route_profile=provider_ready.ROUTE_PROFILE_ID,
        provider_ready_startup=startup,
    )
    try:
        await service._start()
        assert service.discovery is not None
        assert service.discovery.runtime_generation == 1
        assert events.index("grant-readback") < events.index("discovery")
        assert service.discovery_path.is_file()
        assert startup.coordinator.store.read(1) == startup.grant_set
    finally:
        await service._shutdown()


def test_A_GEN_018_session_grant_is_explicit_current_memory_only_and_revocable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        with pytest.raises(SessionGrantDenied, match="not owned"):
            startup.coordinator.accept_session_grant(
                "grant-never-issued",
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="model.request",
                runtime_generation=1,
                now_unix_ms=NOW_MS,
            )
        grant = startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability="model.request",
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id="grant-explicit-current",
        )
        assert (
            startup.coordinator.accept_session_grant(
                grant.grant_id,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="model.request",
                runtime_generation=1,
                now_unix_ms=NOW_MS + 1,
            )
            == grant
        )
        startup.coordinator.revoke_session_grant(grant)
        with pytest.raises(SessionGrantDenied, match="revoked"):
            startup.coordinator.accept_session_grant(
                grant.grant_id,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="model.request",
                runtime_generation=1,
                now_unix_ms=NOW_MS + 1,
            )
        assert tuple(path.name for path in (startup.runtime_home / "authority").rglob("*")) == (
            "runtime-generations",
            "00000000000000000001",
            "workbench-grant-set.json",
        )
    finally:
        application.close()


def test_A_GRANT_005_mixed_generation_grant_is_denied_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readback, authority = provider_ready._preflight(tmp_path, reference_evidence=False)
    database = readback.target / "reference.sqlite3"
    provider_ready._prepare_v6_registry(database, tmp_path / "generation-grant.snapshot.sqlite3")
    monkeypatch.setattr(startup_module, "verify_published_install", lambda *_a, **_k: readback)
    monkeypatch.setattr(
        reference_host_module,
        "SupervisedIPythonWorkspaceBackend",
        provider_ready._GrantAdmissionIPythonWorkspaceBackend,
    )
    first = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        provider_ready._grant_set,
    )
    first_host = ReferenceHost(
        database,
        provider_ready_startup=first,
        **provider_ready._host_options(readback),
    )
    prior = first.coordinator.issue_session_grant(
        principal_id=PRINCIPAL,
        session_id=SESSION,
        capability="rlm.workbench.execute",
        issued_at_unix_ms=NOW_MS,
        ttl_ms=900_000,
        policy_approved=True,
        grant_id="grant-prior-generation",
    )
    first_host.close()
    second = ProviderReadyStartup(
        readback.target,
        ProviderReadyActivationCoordinator(ProviderReadyActivationStore(authority)),
        provider_ready._grant_set,
    )
    second_host = ReferenceHost(
        database,
        provider_ready_startup=second,
        **provider_ready._host_options(readback),
    )
    try:
        assert second_host.runtime_generation == 2
        with pytest.raises(SessionGrantDenied, match="not owned"):
            second.coordinator.accept_session_grant(
                prior.grant_id,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                runtime_generation=2,
                now_unix_ms=NOW_MS + 1,
            )
    finally:
        second_host.close()


def test_A_GRANT_006_mixed_profile_binding_is_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        grant = startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability="model.request",
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id="grant-profile-bound",
        )
        with pytest.raises(SessionGrantDenied, match="profile digest context"):
            startup.coordinator.accept_session_grant(
                grant.grant_id,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="model.request",
                runtime_generation=1,
                now_unix_ms=NOW_MS + 1,
                profile_digest=OTHER_DIGEST,
            )
    finally:
        application.close()


def test_A_GRANT_007_duplicate_capability_grants_are_denied(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        grants = tuple(
            startup.coordinator.issue_session_grant(
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="rlm.workbench.execute",
                issued_at_unix_ms=NOW_MS,
                ttl_ms=900_000,
                policy_approved=True,
                grant_id=grant_id,
            )
            for grant_id in ("grant-duplicate-a", "grant-duplicate-b")
        )
        with pytest.raises(SessionGrantDenied, match="duplicate a capability"):
            startup.resolve_session_grants(
                tuple(grant.grant_id for grant in grants),
                principal_id=PRINCIPAL,
                session_id=SESSION,
                runtime_generation=1,
                now_unix_ms=NOW_MS + 1,
                deadline_unix_ms=NOW_MS + 900_000,
                required_capability="rlm.workbench.execute",
                budget=Budget(
                    wall_time_ms=BUDGET.wall_time_ms,
                    model_requests=BUDGET.model_requests,
                    input_tokens=BUDGET.input_tokens,
                    output_tokens=BUDGET.output_tokens,
                    child_operations=BUDGET.child_operations,
                    artifact_bytes=BUDGET.artifact_bytes,
                ),
            )
    finally:
        application.close()


def test_A_GRANT_010_client_authored_grant_bytes_are_not_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, _database = _provider_application(tmp_path, monkeypatch)
    try:
        grant = startup.coordinator.issue_session_grant(
            principal_id=PRINCIPAL,
            session_id=SESSION,
            capability="model.request",
            issued_at_unix_ms=NOW_MS,
            ttl_ms=900_000,
            policy_approved=True,
            grant_id="grant-owned-by-server",
        )
        forged = grant.model_copy(update={"capability": "effect.propose"})
        with pytest.raises(SessionGrantDenied, match="owned immutable grant"):
            startup.coordinator.accept_session_grant(
                forged,
                principal_id=PRINCIPAL,
                session_id=SESSION,
                capability="effect.propose",
                runtime_generation=1,
                now_unix_ms=NOW_MS + 1,
            )
    finally:
        application.close()


def test_A_C03_claim_rejects_sibling_only_current_authority_before_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    application, startup, _readback, database = _provider_application(tmp_path, monkeypatch)

    async def scenario() -> tuple[dict[str, Any], str, str]:
        async with Client(application.server) as client:
            capabilities_result = await client.call_tool("aar_capabilities")
            assert not capabilities_result.is_error
            assert capabilities_result.structured_content is not None
            capabilities = capabilities_result.structured_content
            grant_ids = _issue_grants(
                startup,
                suffix="claim-sibling",
                capabilities=("model.request", "broker.caller.cancel"),
            )
            execute_arguments = provider_ready._execute_arguments(
                capabilities,
                suffix="claim-sibling-operation",
                grant_ids=grant_ids,
            )
            execute_arguments["spec"]["budgets"]["max_artifact_bytes"] = 0
            execute_arguments["spec"]["budgets"]["max_subagent_calls"] = 0
            accepted = await client.call_tool(
                "aar_rlm_workbench_execute",
                execute_arguments,
            )
            assert not accepted.is_error
            assert accepted.structured_content is not None
            assert accepted.structured_content.get("phase") == "preparing_workspace", (
                accepted.structured_content
            )
            operation = accepted.structured_content["operation"]

            with sqlite3.connect(database) as connection:
                before = "\n".join(connection.iterdump())

            successor_context = dict(execute_arguments["context"])
            successor_context.update(
                request_id="request-claim-sibling-successor",
                idempotency_key="context-claim-sibling-successor",
            )
            claim_arguments = {
                "context": successor_context,
                "operation": operation,
                "expected_control_revision": 1,
                "expected_cancellation_revision": 0,
                "expected_suspension_revision": 1,
                "expected_cumulative_deadline_unix_ms": successor_context[
                    "deadline_unix_ms"
                ],
                "ticket_id": "ticket-claim-sibling",
                "expected_revision": 0,
                "ticket_digest": "sha256:" + "1" * 64,
                "adapter_id": "adapter-model-request",
                "adapter_generation": capabilities["ready"]["runtime_generation"],
                "claim_lease_ms": 60_000,
                "idempotency_key": "command-claim-sibling-successor",
            }
            CallerWorkClaimInput.model_validate_json(
                canonical_json_bytes(claim_arguments), strict=True
            )
            denied = await client.call_tool(
                "aar_broker_work_claim",
                claim_arguments,
            )
            assert not denied.is_error, [item.text for item in denied.content]
            assert denied.structured_content is not None
            with sqlite3.connect(database) as connection:
                after = "\n".join(connection.iterdump())
            return denied.structured_content, before, after

    try:
        failure, before, after = asyncio.run(scenario())
    finally:
        application.close()

    assert failure["code"] == "GRANT_DENIED"
    assert "broker.caller.claim" in failure["message"]
    assert before == after


anyio_backend = "asyncio"
