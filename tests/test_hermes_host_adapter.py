from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anyio
import pytest

from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.compat import hermes_authority, hermes_mcp
from aar.provider_ready_models import GrantBudgetCeiling
from aar.provider_ready_package_factory import PACKAGE_FACTORY_DECLARATIONS
from aar.provider_ready_runtime_models import IssuedWorkbenchGrant, WorkbenchGrantSet
from aar.runtime import supervisor_client as supervisor_client_module
from aar.runtime.hermes_host import build_hermes_model_registry, load_hermes_route_catalog
from aar.runtime.model_broker import ModelProviderFailure
from aar.runtime.provider_ready_activation import ProviderReadyActivationStore
from aar.runtime.provider_ready_startup import ProviderReadyStartupError
from aar.runtime.supervisor_client import (
    SupervisorClient,
    SupervisorClientError,
    SupervisorControlOutcomeIndeterminate,
    SupervisorControlRejected,
)
from aar.runtime.supervisor_protocol import (
    SUPERVISOR_PROTOCOL_DIGEST,
    SUPERVISOR_PROTOCOL_VERSION,
    PrivateFrame,
    SupervisorGrantIssueRequest,
    SupervisorGrantRevokeRequest,
)
from aar.versions import PACKAGE_VERSION

DIGEST = canonical_sha256({"fixture": "hermes-host-adapter"})
PROCESS_IDENTITY = {"pid": 1234, "start_time": "test-process-start"}


def _grant_set_fixture(
    *,
    runtime_generation: int = 4,
    activation_generation: int = 3,
    route_catalog_digest: str | None = None,
) -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version="aar.workbench-grant-set.v1",
        runtime_generation=runtime_generation,
        activation_generation=activation_generation,
        profile_id="profile-current",
        profile_digest=canonical_sha256({"profile": "current"}),
        activation_authority_digest=canonical_sha256({"authority": "current"}),
        capability_digest=canonical_sha256({"capability": "current"}),
        route_catalog_digest=(
            route_catalog_digest
            if route_catalog_digest is not None
            else canonical_sha256({"route": "current"})
        ),
        principal_ids=("principal-local",),
        session_binding_policy="bind_exact_request_session",
        capabilities=("workspace.create",),
        budget_ceiling=GrantBudgetCeiling(
            wall_time_ms=60_000,
            model_requests=4,
            input_tokens=8_000,
            output_tokens=4_000,
            child_operations=2,
            artifact_bytes=2_000_000,
        ),
        max_ttl_ms=60_000,
    )


def _catalog(
    *,
    provider_driver: str = "reference-driver",
    provider: str = "reference",
    model: str = "deterministic",
) -> ModelRouteCatalog:
    return ModelRouteCatalog.issue(
        (
            ModelRouteProfile(
                profile_id="route-primary",
                provider_driver=provider_driver,
                provider=provider,
                model=model,
                reasoning_effort="high",
                max_output_tokens=8_192,
                fallback_policy="none",
                cache_policy="disabled",
            ),
        )
    )


def _capabilities(generation: int, catalog_digest: str) -> dict[str, object]:
    return {
        "package_version": PACKAGE_VERSION,
        "ready": {
            "runtime_generation": generation,
            "capabilities": {"digest": DIGEST},
        },
        "supervisor": {
            "mode": "attached-supervisor",
            "frontend_ephemeral": True,
            "dispatcher_generation": generation,
            "supervisor_version": f"aar-supervisor/{PACKAGE_VERSION}",
            "protocol_version": SUPERVISOR_PROTOCOL_VERSION,
            "protocol_digest": SUPERVISOR_PROTOCOL_DIGEST,
            "process_identity_digest": canonical_sha256(PROCESS_IDENTITY),
        },
        "model_broker": {
            "configured": True,
            "default_profile_id": "route-primary",
            "catalog_digest": catalog_digest,
        },
        "model_routes": {"catalog_digest": catalog_digest},
    }


def _workbench(generation: int) -> dict[str, object]:
    return {
        "methods": [
            {
                "method": declaration.method,
                "configured": True,
                "reference_only": False,
                "backend_kind": (
                    "caller_driver" if declaration.method == "model.request" else "native"
                ),
                "adapter_generation": generation,
            }
            for declaration in PACKAGE_FACTORY_DECLARATIONS
        ]
    }


def test_shipped_hermes_intent_authorizes_explicit_caller_servicing_capabilities() -> None:
    template = json.loads(
        (
            Path(__file__).parents[1] / "docs" / "examples" / "provider-ready-intent-template.json"
        ).read_text(encoding="utf-8")
    )
    assert template["grant_policy"]["capabilities"] == [
        "broker.caller.cancel",
        "broker.caller.claim",
        "broker.caller.commit",
        "broker.caller.reconcile",
        "broker.caller.send",
        "model.request",
        "rlm.workbench.execute",
        "rlm.workbench.read",
    ]


def test_verify_hermes_ready_joins_live_and_persisted_bindings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    catalog = _catalog()
    generation = 7
    workbench = _workbench(generation)
    grant_set = SimpleNamespace(
        runtime_generation=generation,
        capability_digest=canonical_sha256(workbench),
        route_catalog_digest=catalog.catalog_digest,
    )
    store = SimpleNamespace(read=lambda observed: grant_set if observed == generation else None)
    monkeypatch.setattr(hermes_mcp, "ProviderReadyActivationStore", lambda _path: store)
    monkeypatch.setattr(
        hermes_mcp,
        "_capabilities_probe",
        lambda _runtime_home, _discovery: (
            _capabilities(generation, catalog.catalog_digest),
            workbench,
        ),
    )
    discovery: Any = SimpleNamespace(
        runtime_generation=generation,
        dispatcher_generation=generation,
        capability_digest=DIGEST,
        supervisor_version=f"aar-supervisor/{PACKAGE_VERSION}",
        process_identity=PROCESS_IDENTITY,
    )

    result = hermes_mcp.verify_hermes_ready(
        tmp_path,
        discovery,
        route_catalog_digest=catalog.catalog_digest,
        default_route_profile="route-primary",
    )
    assert result["package_version"] == PACKAGE_VERSION

    drifted = _capabilities(generation, catalog.catalog_digest)
    drifted["model_broker"] = {
        "configured": True,
        "default_profile_id": "route-other",
        "catalog_digest": catalog.catalog_digest,
    }
    monkeypatch.setattr(
        hermes_mcp,
        "_capabilities_probe",
        lambda _runtime_home, _discovery: (drifted, workbench),
    )
    with pytest.raises(
        hermes_mcp.HermesMcpLauncherError,
        match="startup binding",
    ):
        hermes_mcp.verify_hermes_ready(
            tmp_path,
            discovery,
            route_catalog_digest=catalog.catalog_digest,
            default_route_profile="route-primary",
        )

    process_drifted = _capabilities(generation, catalog.catalog_digest)
    process_projection = process_drifted["supervisor"]
    assert isinstance(process_projection, dict)
    process_projection["process_identity_digest"] = DIGEST
    monkeypatch.setattr(
        hermes_mcp,
        "_capabilities_probe",
        lambda _runtime_home, _discovery: (process_drifted, workbench),
    )
    with pytest.raises(hermes_mcp.HermesMcpLauncherError, match="startup binding"):
        hermes_mcp.verify_hermes_ready(
            tmp_path,
            discovery,
            route_catalog_digest=catalog.catalog_digest,
            default_route_profile="route-primary",
        )

    workbench_drifted = _workbench(generation)
    rows = workbench_drifted["methods"]
    assert isinstance(rows, list) and isinstance(rows[0], dict)
    rows[0]["adapter_generation"] = generation + 1
    monkeypatch.setattr(
        hermes_mcp,
        "_capabilities_probe",
        lambda _runtime_home, _discovery: (
            _capabilities(generation, catalog.catalog_digest),
            workbench_drifted,
        ),
    )
    with pytest.raises(hermes_mcp.HermesMcpLauncherError, match="startup binding"):
        hermes_mcp.verify_hermes_ready(
            tmp_path,
            discovery,
            route_catalog_digest=catalog.catalog_digest,
            default_route_profile="route-primary",
        )


@pytest.mark.parametrize(
    ("mismatch_axis", "fixture_name", "path", "replacement"),
    (
        (
            "supervisor_protocol",
            "capabilities",
            ("supervisor", "protocol_digest"),
            canonical_sha256({"fixture": "mismatched-supervisor-protocol"}),
        ),
        (
            "ready_runtime_generation",
            "capabilities",
            ("ready", "runtime_generation"),
            8,
        ),
        (
            "supervisor_dispatcher_generation",
            "capabilities",
            ("supervisor", "dispatcher_generation"),
            8,
        ),
        (
            "ready_capability_digest",
            "capabilities",
            ("ready", "capabilities", "digest"),
            canonical_sha256({"fixture": "mismatched-ready-capability"}),
        ),
        (
            "activation_grant_set_capability_binding",
            "activation_binding",
            ("capability_digest",),
            canonical_sha256({"fixture": "mismatched-activation-capability"}),
        ),
        (
            "activation_route_catalog_binding",
            "activation_binding",
            ("route_catalog_digest",),
            canonical_sha256({"fixture": "mismatched-activation-route-catalog"}),
        ),
    ),
)
def test_verify_hermes_ready_rejects_each_live_owner_mismatch_without_handoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch_axis: str,
    fixture_name: str,
    path: tuple[str, ...],
    replacement: object,
) -> None:
    """AC-08: every omitted live-owner binding fails closed without replacement."""

    catalog = _catalog()
    generation = 7
    workbench = _workbench(generation)
    activation_binding: dict[str, object] = {
        "runtime_generation": generation,
        "capability_digest": canonical_sha256(workbench),
        "route_catalog_digest": catalog.catalog_digest,
    }
    capabilities = _capabilities(generation, catalog.catalog_digest)
    fixtures = {
        "capabilities": capabilities,
        "activation_binding": activation_binding,
    }
    target: dict[str, object] = fixtures[fixture_name]
    for segment in path[:-1]:
        nested = target[segment]
        assert isinstance(nested, dict)
        target = nested
    assert target[path[-1]] != replacement
    target[path[-1]] = replacement

    persisted_fixture_before_verify = canonical_json_bytes(activation_binding)
    capabilities_before_verify = canonical_json_bytes(capabilities)
    workbench_before_verify = canonical_json_bytes(workbench)
    read_generations: list[int] = []
    activation_handoffs: list[str] = []
    launcher_handoffs: list[object] = []

    class ReadOnlyActivationStore:
        def read(self, observed_generation: int) -> SimpleNamespace:
            read_generations.append(observed_generation)
            return SimpleNamespace(**activation_binding)

        def activate(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            activation_handoffs.append("activate")
            raise AssertionError("verification must not activate a replacement owner")

        def publish(self, *args: object, **kwargs: object) -> None:
            del args, kwargs
            activation_handoffs.append("publish")
            raise AssertionError("verification must not publish a replacement owner")

    store = ReadOnlyActivationStore()
    authority_roots: list[Path] = []

    def activation_store_factory(authority_root: Path) -> ReadOnlyActivationStore:
        authority_roots.append(authority_root)
        return store

    def capabilities_probe(
        observed_runtime_home: Path, observed_discovery: object
    ) -> tuple[dict[str, object], dict[str, object]]:
        assert observed_runtime_home == tmp_path
        assert observed_discovery is discovery
        return capabilities, workbench

    def forbidden_launcher_handoff(*args: object, **kwargs: object) -> None:
        del args, kwargs
        launcher_handoffs.append(mismatch_axis)
        raise AssertionError("verification must not hand off to a replacement launcher")

    monkeypatch.setattr(hermes_mcp, "ProviderReadyActivationStore", activation_store_factory)
    monkeypatch.setattr(hermes_mcp, "_capabilities_probe", capabilities_probe)
    monkeypatch.setattr(hermes_mcp, "ensure_codex_supervisor", forbidden_launcher_handoff)
    discovery: Any = SimpleNamespace(
        runtime_generation=generation,
        dispatcher_generation=generation,
        capability_digest=DIGEST,
        supervisor_version=f"aar-supervisor/{PACKAGE_VERSION}",
        process_identity=PROCESS_IDENTITY,
    )

    with pytest.raises(
        hermes_mcp.HermesMcpLauncherError,
        match="startup binding",
    ):
        hermes_mcp.verify_hermes_ready(
            tmp_path,
            discovery,
            route_catalog_digest=catalog.catalog_digest,
            default_route_profile="route-primary",
        )

    assert authority_roots == [tmp_path / "authority"]
    assert read_generations == [generation]
    assert activation_handoffs == []
    assert launcher_handoffs == []
    assert canonical_json_bytes(activation_binding) == persisted_fixture_before_verify
    assert canonical_json_bytes(capabilities) == capabilities_before_verify
    assert canonical_json_bytes(workbench) == workbench_before_verify


def test_verify_hermes_ready_rejects_generation_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hermes_mcp,
        "ProviderReadyActivationStore",
        lambda _path: SimpleNamespace(read=lambda _generation: None),
    )
    discovery: Any = SimpleNamespace(
        runtime_generation=3,
        dispatcher_generation=3,
        capability_digest=DIGEST,
        supervisor_version=f"aar-supervisor/{PACKAGE_VERSION}",
        process_identity=PROCESS_IDENTITY,
    )
    with pytest.raises(hermes_mcp.HermesMcpLauncherError, match="no provider-ready grant set"):
        hermes_mcp.verify_hermes_ready(
            tmp_path,
            discovery,
            route_catalog_digest=DIGEST,
            default_route_profile="route-primary",
        )


def test_caller_delegated_registry_binds_route_but_blocks_service_owned_send() -> None:
    catalog = _catalog(
        provider_driver="host-caller-driver-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
    )
    registry = build_hermes_model_registry(catalog)
    binding = registry.bind("route-primary")
    assert registry.describe() == catalog
    with pytest.raises(ModelProviderFailure, match="claim/mark-send/commit"):
        registry.resolve(binding).request(object(), object(), binding)  # type: ignore[arg-type]


def test_unknown_hermes_route_driver_is_rejected() -> None:
    with pytest.raises(ProviderReadyStartupError, match="unsupported route drivers"):
        build_hermes_model_registry(
            _catalog(
                provider_driver="unknown-driver",
                provider="openai-codex",
                model="gpt-5.6-luna",
            )
        )


def test_stale_discovery_cannot_read_predecessor_grant_authority(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    authority.mkdir(mode=0o700)
    store = ProviderReadyActivationStore(authority)
    store.publish(_grant_set_fixture(runtime_generation=4, activation_generation=3))
    store.publish(_grant_set_fixture(runtime_generation=5, activation_generation=4))
    client = SupervisorClient(tmp_path)
    discovery: Any = SimpleNamespace(runtime_generation=4)

    with pytest.raises(
        SupervisorClientError,
        match="current persisted grant authority does not match discovery",
    ):
        client._load_current_grant_set(discovery)


def test_sent_grant_control_without_terminal_receipt_is_indeterminate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class LostResponseStream:
        async def send(self, _payload: bytes) -> None:
            return None

        async def receive(self, _max_bytes: int) -> bytes:
            raise anyio.EndOfStream

        async def aclose(self) -> None:
            return None

    grant_set = _grant_set_fixture()
    discovery: Any = SimpleNamespace(
        runtime_generation=4,
        dispatcher_generation=4,
        runtime_home_digest=DIGEST,
        attachment_credential_digest=DIGEST,
        capability_digest=grant_set.capability_digest,
    )
    client = SupervisorClient(tmp_path)
    monkeypatch.setattr(client, "_load_discovery", lambda: discovery)
    monkeypatch.setattr(client, "_load_credential", lambda _discovery: b"credential")
    monkeypatch.setattr(client, "_load_current_grant_set", lambda _discovery: grant_set)

    async def connect(_discovery):
        return LostResponseStream()

    monkeypatch.setattr(client, "_connect", connect)
    request = SupervisorGrantIssueRequest(
        principal_id="principal-local",
        session_id="session-local",
        capability="workspace.create",
        ttl_ms=60_000,
        grant_id="grant-lost-response",
    )
    with pytest.raises(SupervisorControlOutcomeIndeterminate) as raised:
        anyio.run(client.issue_session_grant, request)
    assert raised.value.grant_id == request.grant_id


@pytest.mark.parametrize(
    "mismatch_axis",
    (
        "request_id",
        "trace_id",
        "authority_digest",
        "deadline_unix_ms",
        "payload",
        "noncanonical_payload",
    ),
)
def test_sent_grant_control_with_malformed_or_mismatched_error_is_indeterminate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mismatch_axis: str,
) -> None:
    class MismatchedErrorStream:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send(self, payload: bytes) -> None:
            self.sent.append(payload.rstrip(b"\n"))

        async def receive(self, _max_bytes: int) -> bytes:
            sent = PrivateFrame.model_validate_json(self.sent[-1], strict=True)
            values = {
                "request_id": sent.request_id,
                "trace_id": sent.trace_id,
                "authority_digest": sent.authority_digest,
                "deadline_unix_ms": sent.deadline_unix_ms,
                "payload": canonical_json_bytes({"error": "rejected"}),
            }
            replacements = {
                "request_id": "unrelated-request",
                "trace_id": "trace-unrelated",
                "authority_digest": DIGEST,
                "deadline_unix_ms": sent.deadline_unix_ms + 1,
                "payload": b"not-json",
            }
            if mismatch_axis == "noncanonical_payload":
                values["payload"] = b'{"error": "rejected"}'
            else:
                values[mismatch_axis] = replacements[mismatch_axis]
            frame = PrivateFrame.issue(
                kind="error",
                request_id=values["request_id"],
                trace_id=values["trace_id"],
                runtime_generation=sent.runtime_generation,
                dispatcher_generation=sent.dispatcher_generation,
                authority_digest=values["authority_digest"],
                deadline_unix_ms=values["deadline_unix_ms"],
                attachment_digest=sent.attachment_digest,
                payload=values["payload"],
            )
            return canonical_json_bytes(frame) + b"\n"

        async def aclose(self) -> None:
            return None

    grant_set = _grant_set_fixture()
    discovery: Any = SimpleNamespace(
        runtime_generation=4,
        dispatcher_generation=4,
        runtime_home_digest=DIGEST,
        attachment_credential_digest=DIGEST,
        capability_digest=grant_set.capability_digest,
    )
    client = SupervisorClient(tmp_path)
    client.discovery = discovery
    monkeypatch.setattr(client, "_load_discovery", lambda: discovery)
    monkeypatch.setattr(client, "_load_credential", lambda _discovery: b"credential")
    monkeypatch.setattr(client, "_load_current_grant_set", lambda _discovery: grant_set)
    stream = MismatchedErrorStream()

    async def connect(_discovery):
        return stream

    monkeypatch.setattr(client, "_connect", connect)
    request = SupervisorGrantIssueRequest(
        principal_id="principal-local",
        session_id="session-local",
        capability="workspace.create",
        ttl_ms=60_000,
        grant_id=f"grant-{mismatch_axis}",
    )
    with pytest.raises(SupervisorControlOutcomeIndeterminate) as raised:
        anyio.run(client.issue_session_grant, request)
    assert raised.value.grant_id == request.grant_id
    assert len(stream.sent) == 2


@pytest.mark.parametrize(
    ("command", "mismatch_axis"),
    (
        *(
            ("issue", axis)
            for axis in (
                "noncanonical_payload",
                "malformed_payload",
                "authority_replaced_during_request",
                "deadline_expires_during_authority_read",
                "grant_set_digest",
                "capability",
                "principal_id",
                "session_id",
                "runtime_generation",
                "activation_generation",
                "profile_digest",
                "activation_authority_digest",
                "capability_digest",
                "route_catalog_digest",
                "wall_time_ms",
                "model_requests",
                "input_tokens",
                "output_tokens",
                "child_operations",
                "artifact_bytes",
                "issued_before_request",
                "issued_after_response",
                "ttl_relation",
            )
        ),
        *(
            ("revoke", axis)
            for axis in (
                "noncanonical_payload",
                "malformed_payload",
                "grant_set_digest",
                "principal_not_current",
                "capability_not_current",
                "runtime_generation",
                "activation_generation",
                "profile_digest",
                "activation_authority_digest",
                "capability_digest",
                "route_catalog_digest",
                "wall_time_ms",
                "model_requests",
                "input_tokens",
                "output_tokens",
                "child_operations",
                "artifact_bytes",
            )
        ),
    ),
)
def test_sent_grant_success_body_mismatch_is_indeterminate_without_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    mismatch_axis: str,
) -> None:
    alternate_digest = canonical_sha256({"mismatch_axis": mismatch_axis})
    grant_set = _grant_set_fixture()
    budget = grant_set.budget_ceiling
    discovery: Any = SimpleNamespace(
        runtime_generation=4,
        dispatcher_generation=4,
        runtime_home_digest=DIGEST,
        attachment_credential_digest=DIGEST,
        capability_digest=grant_set.capability_digest,
    )
    grant_id = f"grant-{command}-{mismatch_axis}"
    base_grant = IssuedWorkbenchGrant(
        grant_id=grant_id,
        grant_set_digest=grant_set.grant_set_digest,
        capability="workspace.create",
        principal_id="principal-local",
        session_id="session-local",
        runtime_generation=grant_set.runtime_generation,
        activation_generation=grant_set.activation_generation,
        profile_digest=grant_set.profile_digest,
        activation_authority_digest=grant_set.activation_authority_digest,
        capability_digest=grant_set.capability_digest,
        route_catalog_digest=grant_set.route_catalog_digest,
        wall_time_ms=budget.wall_time_ms,
        model_requests=budget.model_requests,
        input_tokens=budget.input_tokens,
        output_tokens=budget.output_tokens,
        child_operations=budget.child_operations,
        artifact_bytes=budget.artifact_bytes,
        issued_at_unix_ms=1_000_000,
        expires_at_unix_ms=1_060_000,
        revoked=command == "revoke",
    )

    updates: dict[str, object] = {
        "grant_set_digest": alternate_digest,
        "capability": "workspace.execute",
        "principal_id": "principal-other",
        "principal_not_current": "principal-other",
        "session_id": "session-other",
        "capability_not_current": "workspace.execute",
        "runtime_generation": 5,
        "activation_generation": 4,
        "profile_digest": alternate_digest,
        "activation_authority_digest": alternate_digest,
        "capability_digest": alternate_digest,
        "route_catalog_digest": alternate_digest,
        "wall_time_ms": budget.wall_time_ms - 1,
        "model_requests": budget.model_requests - 1,
        "input_tokens": budget.input_tokens - 1,
        "output_tokens": budget.output_tokens - 1,
        "child_operations": budget.child_operations - 1,
        "artifact_bytes": budget.artifact_bytes - 1,
    }
    grant_values = base_grant.model_dump(mode="python")
    if mismatch_axis == "issued_before_request":
        grant_values.update(issued_at_unix_ms=999_999, expires_at_unix_ms=1_059_999)
    elif mismatch_axis == "issued_after_response":
        grant_values.update(issued_at_unix_ms=1_000_001, expires_at_unix_ms=1_060_001)
    elif mismatch_axis == "ttl_relation":
        grant_values["expires_at_unix_ms"] = 1_060_001
    elif mismatch_axis == "principal_not_current":
        grant_values["principal_id"] = updates[mismatch_axis]
    elif mismatch_axis == "capability_not_current":
        grant_values["capability"] = updates[mismatch_axis]
    elif mismatch_axis in {
        "authority_replaced_during_request",
        "deadline_expires_during_authority_read",
        "malformed_payload",
    }:
        pass
    elif mismatch_axis != "noncanonical_payload":
        grant_values[mismatch_axis] = updates[mismatch_axis]
    response_grant = IssuedWorkbenchGrant(**grant_values)

    class MismatchedSuccessStream:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        async def send(self, payload: bytes) -> None:
            self.sent.append(payload.rstrip(b"\n"))

        async def receive(self, _max_bytes: int) -> bytes:
            sent = PrivateFrame.model_validate_json(self.sent[-1], strict=True)
            payload = canonical_json_bytes(response_grant)
            if mismatch_axis == "noncanonical_payload":
                payload = json.dumps(
                    response_grant.model_dump(mode="json"),
                    sort_keys=True,
                    separators=(", ", ": "),
                ).encode()
            elif mismatch_axis == "malformed_payload":
                payload = b"not-json"
            response = PrivateFrame.issue(
                kind="grant_issued" if command == "issue" else "grant_revoked",
                request_id=sent.request_id,
                trace_id=sent.trace_id,
                runtime_generation=sent.runtime_generation,
                dispatcher_generation=sent.dispatcher_generation,
                authority_digest=sent.authority_digest,
                deadline_unix_ms=sent.deadline_unix_ms,
                attachment_digest=sent.attachment_digest,
                payload=payload,
            )
            return canonical_json_bytes(response) + b"\n"

        async def aclose(self) -> None:
            return None

    client = SupervisorClient(tmp_path)
    client.discovery = discovery
    monkeypatch.setattr(client, "_load_discovery", lambda: discovery)
    monkeypatch.setattr(client, "_load_credential", lambda _discovery: b"credential")
    if mismatch_axis == "authority_replaced_during_request":
        grant_sets = iter(
            (
                grant_set,
                _grant_set_fixture(route_catalog_digest=alternate_digest),
            )
        )
        monkeypatch.setattr(
            client,
            "_load_current_grant_set",
            lambda _discovery: next(grant_sets),
        )
    else:
        monkeypatch.setattr(
            client,
            "_load_current_grant_set",
            lambda _discovery: grant_set,
        )
    stream = MismatchedSuccessStream()

    async def connect(_discovery):
        return stream

    monkeypatch.setattr(client, "_connect", connect)
    if mismatch_axis == "deadline_expires_during_authority_read":
        clock = iter((1_000.0, 1_000.0, 1_061.0))
        monkeypatch.setattr(supervisor_client_module.time, "time", lambda: next(clock))
    else:
        monkeypatch.setattr(supervisor_client_module.time, "time", lambda: 1_000.0)
    request = (
        SupervisorGrantIssueRequest(
            principal_id="principal-local",
            session_id="session-local",
            capability="workspace.create",
            ttl_ms=60_000,
            grant_id=grant_id,
        )
        if command == "issue"
        else SupervisorGrantRevokeRequest(grant_id=grant_id)
    )
    SupervisorClient._validate_grant_success_body(
        grant=base_grant,
        payload=canonical_json_bytes(base_grant),
        request=request,
        response_kind="grant_issued" if command == "issue" else "grant_revoked",
        grant_set=grant_set,
        request_started_unix_ms=1_000_000,
        response_received_unix_ms=1_000_000,
    )

    async def run_operation() -> IssuedWorkbenchGrant:
        if isinstance(request, SupervisorGrantIssueRequest):
            return await client.issue_session_grant(request)
        return await client.revoke_session_grant(request)

    with pytest.raises(SupervisorControlOutcomeIndeterminate) as raised:
        anyio.run(run_operation)

    assert raised.value.grant_id == grant_id
    cause = raised.value.__cause__
    assert isinstance(cause, SupervisorClientError)
    assert str(cause) in {
        "current grant authority changed during grant control",
        "grant control success body is malformed",
        "grant control success body does not match current grant authority",
        "grant issue success body does not match request",
        "grant revoke success body does not match request",
        "supervisor response deadline expired",
    }
    assert len(stream.sent) == 2


@pytest.mark.parametrize(
    ("error", "expected_exit", "expected_outcome"),
    (
        (
            SupervisorControlOutcomeIndeterminate(
                "indeterminate",
                grant_id="grant-cli-indeterminate",
            ),
            3,
            "indeterminate",
        ),
        (SupervisorControlRejected("rejected"), 2, "rejected"),
    ),
)
def test_hermes_authority_cli_preserves_control_certainty(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
    expected_exit: int,
    expected_outcome: str,
) -> None:
    async def fail(_args):
        raise error

    monkeypatch.setattr(hermes_authority, "_run", fail)
    exit_code = hermes_authority.main(
        [
            "issue",
            "--principal-id",
            "principal-local",
            "--session-id",
            "session-local",
            "--capability",
            "workspace.create",
            "--ttl-ms",
            "60000",
            "--grant-id",
            "grant-cli-indeterminate",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == expected_exit
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["outcome"] == expected_outcome
    if expected_outcome == "indeterminate":
        assert payload["grant_id"] == "grant-cli-indeterminate"


def test_route_catalog_loader_rejects_symlink_authority(
    tmp_path: Path,
) -> None:
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(_catalog().model_dump_json(), encoding="utf-8")
    link = tmp_path / "catalog-link.json"
    try:
        link.symlink_to(catalog_path)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(ProviderReadyStartupError, match="symlink"):
        load_hermes_route_catalog(link)
