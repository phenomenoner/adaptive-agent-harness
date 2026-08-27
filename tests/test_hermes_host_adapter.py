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
from aar.provider_ready_package_factory import PACKAGE_FACTORY_DECLARATIONS
from aar.runtime.hermes_host import build_hermes_model_registry, load_hermes_route_catalog
from aar.runtime.model_broker import ModelProviderFailure
from aar.runtime.provider_ready_startup import ProviderReadyStartupError
from aar.runtime.supervisor_client import (
    SupervisorClient,
    SupervisorControlOutcomeIndeterminate,
    SupervisorControlRejected,
)
from aar.runtime.supervisor_protocol import (
    SUPERVISOR_PROTOCOL_DIGEST,
    SUPERVISOR_PROTOCOL_VERSION,
    PrivateFrame,
    SupervisorGrantIssueRequest,
)
from aar.versions import PACKAGE_VERSION

DIGEST = canonical_sha256({"fixture": "hermes-host-adapter"})
PROCESS_IDENTITY = {"pid": 1234, "start_time": "test-process-start"}


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
            Path(__file__).parents[1]
            / "docs"
            / "examples"
            / "provider-ready-intent-template.json"
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

    discovery: Any = SimpleNamespace(
        runtime_generation=4,
        dispatcher_generation=4,
        runtime_home_digest=DIGEST,
        attachment_credential_digest=DIGEST,
    )
    client = SupervisorClient(tmp_path)
    monkeypatch.setattr(client, "_load_discovery", lambda: discovery)
    monkeypatch.setattr(client, "_load_credential", lambda _discovery: b"credential")

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

    discovery: Any = SimpleNamespace(
        runtime_generation=4,
        dispatcher_generation=4,
        runtime_home_digest=DIGEST,
        attachment_credential_digest=DIGEST,
    )
    client = SupervisorClient(tmp_path)
    client.discovery = discovery
    monkeypatch.setattr(client, "_load_discovery", lambda: discovery)
    monkeypatch.setattr(client, "_load_credential", lambda _discovery: b"credential")
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
