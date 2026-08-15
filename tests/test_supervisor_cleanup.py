from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.compat import codex_mcp
from aar.compat.codex_mcp import SHUTDOWN_REQUEST_FILE_NAME, SHUTDOWN_REQUEST_SCHEMA_VERSION
from aar.runtime import process_identity as process_identity_module
from aar.runtime import supervisor as supervisor_module
from aar.runtime.process_identity import (
    ProcessIdentityObservation,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
)
from aar.runtime.supervisor import SupervisorError, SupervisorService

DIGEST = canonical_sha256({"fixture": "supervisor-cleanup"})


def _identity(pid: int, *, start_time: int = 11, boot_id: str = "boot-a") -> ProcessStartIdentity:
    return ProcessStartIdentity(
        pid=pid,
        platform="linux",
        start_time=start_time,
        boot_id=boot_id,
    )


def _discovery(
    runtime_home: Path,
    identity: ProcessStartIdentity,
    *,
    endpoint_ref: Path,
    credential: bytes,
) -> SupervisorDiscoveryRecord:
    credential_digest = f"sha256:{hashlib.sha256(credential).hexdigest()}"
    return SupervisorDiscoveryRecord.issue(
        endpoint_kind="unix",
        endpoint_ref=str(endpoint_ref),
        process_identity=identity,
        runtime_generation=1,
        dispatcher_generation=1,
        capability_digest=DIGEST,
        runtime_home_digest=canonical_sha256({"runtime": str(runtime_home)}),
        ready_at_unix_ms=1,
        attachment_credential_digest=credential_digest,
        supervisor_version=codex_mcp.EXPECTED_SUPERVISOR_VERSION,
    )


def _request_bytes(discovery: SupervisorDiscoveryRecord) -> bytes:
    return canonical_json_bytes(
        {
            "discovery_digest": discovery.discovery_digest,
            "process_identity": discovery.process_identity.model_dump(mode="json"),
            "schema_version": SHUTDOWN_REQUEST_SCHEMA_VERSION,
        }
    )


def _populate_private_files(
    service: SupervisorService,
    discovery: SupervisorDiscoveryRecord,
    credential: bytes,
) -> dict[str, Path]:
    service.private_dir.mkdir(parents=True, exist_ok=True)
    endpoint = Path(discovery.endpoint_ref)
    endpoint.write_bytes(b"unix-endpoint")
    service.credential_path.write_bytes(credential)
    service.shutdown_request_path.write_bytes(_request_bytes(discovery))
    service.discovery_path.write_bytes(canonical_json_bytes(discovery))
    return {
        "discovery": service.discovery_path,
        "credential": service.credential_path,
        "endpoint": endpoint,
        "request": service.shutdown_request_path,
    }


def test_stale_cleanup_identity_unavailable_leaves_all_control_files_intact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    credential = b"predecessor-credential"
    discovery = _discovery(
        runtime_home,
        _identity(951),
        endpoint_ref=service.private_dir / "predecessor.sock",
        credential=credential,
    )
    files = _populate_private_files(service, discovery, credential)

    def unavailable(_pid: int) -> ProcessStartIdentity:
        raise ProcessIdentityUnavailable("identity query temporarily unavailable")

    monkeypatch.setattr(process_identity_module, "process_identity", unavailable)

    with pytest.raises(SupervisorError, match="identity unavailable"):
        service._cleanup_stale_discovery()

    assert all(path.exists() for path in files.values())


def test_predecessor_discovery_release_does_not_clobber_replacement_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    endpoint = service.private_dir / "replacement.sock"
    predecessor_credential = b"predecessor-credential"
    replacement_credential = b"replacement-credential"
    predecessor = _discovery(
        runtime_home,
        _identity(952),
        endpoint_ref=endpoint,
        credential=predecessor_credential,
    )
    replacement = _discovery(
        runtime_home,
        _identity(953, start_time=12, boot_id="boot-b"),
        endpoint_ref=endpoint,
        credential=replacement_credential,
    )
    files = _populate_private_files(service, predecessor, predecessor_credential)
    original_unlink = Path.unlink
    injected = False

    def unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal injected
        original_unlink(path, *args, **kwargs)
        if path == service.discovery_path and not injected:
            injected = True
            service.discovery_path.write_bytes(canonical_json_bytes(replacement))
            service.credential_path.write_bytes(replacement_credential)
            endpoint.write_bytes(b"replacement-endpoint")
            service.shutdown_request_path.write_bytes(_request_bytes(replacement))

    monkeypatch.setattr(Path, "unlink", unlink)
    monkeypatch.setattr(
        supervisor_module,
        "observe_process_identity",
        lambda _identity: ProcessIdentityObservation(state=ProcessIdentityState.MISMATCH),
    )

    service._cleanup_stale_discovery()

    assert injected
    assert files["discovery"].read_bytes() == canonical_json_bytes(replacement)
    assert files["credential"].read_bytes() == replacement_credential
    assert files["endpoint"].read_bytes() == b"replacement-endpoint"
    assert files["request"].read_bytes() == _request_bytes(replacement)


def test_owned_cleanup_compare_fences_replacement_endpoint_and_shutdown_request(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    endpoint = service.private_dir / "replacement.sock"
    predecessor_credential = b"predecessor-credential"
    replacement_credential = b"replacement-credential"
    predecessor = _discovery(
        runtime_home,
        _identity(954),
        endpoint_ref=endpoint,
        credential=predecessor_credential,
    )
    replacement = _discovery(
        runtime_home,
        _identity(955, start_time=12, boot_id="boot-b"),
        endpoint_ref=endpoint,
        credential=replacement_credential,
    )
    service.discovery = predecessor
    service._credential = predecessor_credential
    service.private_dir.mkdir(parents=True, exist_ok=True)
    service.discovery_path.write_bytes(canonical_json_bytes(replacement))
    service.credential_path.write_bytes(replacement_credential)
    endpoint.write_bytes(b"replacement-endpoint")
    service.shutdown_request_path.write_bytes(_request_bytes(replacement))

    service._remove_owned_private_files()

    assert service.discovery_path.read_bytes() == canonical_json_bytes(replacement)
    assert service.credential_path.read_bytes() == replacement_credential
    assert endpoint.read_bytes() == b"replacement-endpoint"
    assert service.shutdown_request_path.read_bytes() == _request_bytes(replacement)


def test_stale_launcher_cleanup_does_not_unlink_replacement_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    private = runtime_home / "supervisor"
    private.mkdir(parents=True, exist_ok=True)
    endpoint = private / "replacement.sock"
    predecessor_credential = b"predecessor-credential"
    replacement_credential = b"replacement-credential"
    predecessor = _discovery(
        runtime_home,
        _identity(956),
        endpoint_ref=endpoint,
        credential=predecessor_credential,
    )
    replacement = _discovery(
        runtime_home,
        _identity(957, start_time=12, boot_id="boot-b"),
        endpoint_ref=endpoint,
        credential=replacement_credential,
    )
    discovery_path = private / "discovery.json"
    credential_path = private / "attachment.key"
    request_path = private / SHUTDOWN_REQUEST_FILE_NAME
    discovery_path.write_bytes(canonical_json_bytes(predecessor))
    credential_path.write_bytes(predecessor_credential)
    endpoint.write_bytes(b"predecessor-endpoint")
    request_path.write_bytes(_request_bytes(predecessor))
    original_read_bytes = Path.read_bytes
    injected = False

    def read_bytes(path: Path, *args: object, **kwargs: object) -> bytes:
        nonlocal injected
        result = original_read_bytes(path, *args, **kwargs)
        if path == discovery_path and not injected:
            injected = True
            discovery_path.write_bytes(canonical_json_bytes(replacement))
            credential_path.write_bytes(replacement_credential)
            endpoint.write_bytes(b"replacement-endpoint")
            request_path.write_bytes(_request_bytes(replacement))
        return result

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    codex_mcp._remove_stale_private_files(runtime_home, predecessor)

    assert injected
    assert discovery_path.read_bytes() == canonical_json_bytes(replacement)
    assert credential_path.read_bytes() == replacement_credential
    assert endpoint.read_bytes() == b"replacement-endpoint"
    assert request_path.read_bytes() == _request_bytes(replacement)


def test_shutdown_request_compare_delete_preserves_aba_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    endpoint = service.private_dir / "supervisor.sock"
    predecessor = _discovery(
        runtime_home,
        _identity(958),
        endpoint_ref=endpoint,
        credential=b"predecessor-credential",
    )
    replacement = _discovery(
        runtime_home,
        _identity(959, start_time=12, boot_id="boot-b"),
        endpoint_ref=endpoint,
        credential=b"replacement-credential",
    )
    service.discovery = predecessor
    service.process_identity = predecessor.process_identity
    service.private_dir.mkdir(parents=True, exist_ok=True)
    service.shutdown_request_path.write_bytes(_request_bytes(predecessor))
    replacement_bytes = _request_bytes(replacement)
    original_read_text = Path.read_text
    injected = False

    def read_text(path: Path, *args: object, **kwargs: object) -> str:
        nonlocal injected
        result = original_read_text(path, *args, **kwargs)
        if path == service.shutdown_request_path and not injected:
            injected = True
            service.shutdown_request_path.write_bytes(replacement_bytes)
        return result

    monkeypatch.setattr(Path, "read_text", read_text)

    assert service._consume_shutdown_request() is True
    assert injected
    assert service.shutdown_request_path.read_bytes() == replacement_bytes
