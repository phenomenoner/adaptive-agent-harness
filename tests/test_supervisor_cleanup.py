from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import anyio
import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.compat import codex_mcp
from aar.compat.codex_mcp import SHUTDOWN_REQUEST_SCHEMA_VERSION
from aar.mcp.server import build_server
from aar.runtime import process_identity as process_identity_module
from aar.runtime import supervisor as supervisor_module
from aar.runtime.ownership import RuntimeOwnershipConflict
from aar.runtime.process_identity import (
    ProcessIdentityObservation,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
)
from aar.runtime.supervisor import SupervisorError, SupervisorService

DIGEST = canonical_sha256({"fixture": "supervisor-cleanup"})


def test_supervisor_reuses_database_runtime_lock_before_host_mutation(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime"
    database = runtime_home / "reference.sqlite3"
    owner = build_server(
        database,
        programmable_backend="plain",
        enable_durable_dispatch=False,
    )
    try:
        generation = owner.host.runtime_generation
        contender = SupervisorService(
            runtime_home,
            programmable_backend="plain",
            transport="tcp",
        )
        with pytest.raises(RuntimeOwnershipConflict, match="already has a live owner"):
            anyio.run(contender._start)
        assert contender.application is None
        assert contender.discovery is None
        with sqlite3.connect(database) as connection:
            assert connection.execute(
                "SELECT generation FROM runtime_meta WHERE singleton = 1"
            ).fetchone() == (generation,)
    finally:
        owner.close()

    successor = SupervisorService(
        runtime_home,
        programmable_backend="plain",
        transport="tcp",
    )
    anyio.run(successor._start)
    try:
        assert successor.application is not None
        assert successor.application.host.runtime_generation == generation + 1
    finally:
        anyio.run(successor._shutdown)


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
    publication_id = f"{identity.pid:032x}"
    return SupervisorDiscoveryRecord.issue(
        publication_id=publication_id,
        endpoint_kind="unix",
        endpoint_ref=str(endpoint_ref),
        credential_file=f"attachment-{publication_id}.key",
        shutdown_request_file=f"shutdown-{publication_id}.request",
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


def test_supervisor_control_publications_use_unique_generation_paths(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    first = SupervisorService(runtime_home, transport="unix")
    second = SupervisorService(runtime_home, transport="unix")

    assert first.publication_id != second.publication_id
    assert first.credential_path != second.credential_path
    assert first.shutdown_request_path != second.shutdown_request_path
    assert first.socket_path != second.socket_path
    for service in (first, second):
        assert service.publication_id in service.credential_path.name
        assert service.publication_id in service.shutdown_request_path.name
        assert service.publication_id in service.socket_path.name


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
    delete_calls: list[Path] = []
    monkeypatch.setattr(Path, "unlink", lambda path, *_a, **_k: delete_calls.append(path))
    monkeypatch.setattr(
        supervisor_module,
        "observe_process_identity",
        lambda _identity: ProcessIdentityObservation(state=ProcessIdentityState.MISMATCH),
    )

    service.discovery_path.write_bytes(canonical_json_bytes(replacement))
    service.credential_path.write_bytes(replacement_credential)
    endpoint.write_bytes(b"replacement-endpoint")
    service.shutdown_request_path.write_bytes(_request_bytes(replacement))
    service._cleanup_stale_discovery()

    assert delete_calls == []
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
    credential_path = private / predecessor.credential_file
    request_path = private / predecessor.shutdown_request_file
    discovery_path.write_bytes(canonical_json_bytes(predecessor))
    credential_path.write_bytes(predecessor_credential)
    endpoint.write_bytes(b"predecessor-endpoint")
    request_path.write_bytes(_request_bytes(predecessor))
    delete_calls: list[Path] = []
    monkeypatch.setattr(Path, "unlink", lambda path, *_a, **_k: delete_calls.append(path))
    discovery_path.write_bytes(canonical_json_bytes(replacement))
    credential_path.write_bytes(replacement_credential)
    endpoint.write_bytes(b"replacement-endpoint")
    request_path.write_bytes(_request_bytes(replacement))

    codex_mcp._remove_stale_private_files(runtime_home, predecessor)

    assert delete_calls == []
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


def _exercise_successor_publication_at_delete(
    monkeypatch: pytest.MonkeyPatch,
    *,
    target: Path,
    publish_successor,
    retire_predecessor,
) -> None:
    """Publish a successor at the baseline's final pathname-delete seam."""

    original_unlink = Path.unlink
    delete_calls: list[Path] = []
    published = False

    def unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal published
        delete_calls.append(path)
        if path == target and not published:
            published = True
            publish_successor()
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", unlink)
    retire_predecessor()
    if not published:
        publish_successor()

    assert delete_calls == [], "normal lifecycle retirement must not delete pathnames"
    assert target.exists(), "the successor artifact must survive predecessor retirement"


def _supervisor_cleanup_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact: str,
    owned: bool,
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    predecessor_credential = b"predecessor-credential"
    successor_credential = b"successor-credential"
    predecessor = _discovery(
        runtime_home,
        _identity(970),
        endpoint_ref=service.private_dir / "shared.sock",
        credential=predecessor_credential,
    )
    successor = _discovery(
        runtime_home,
        _identity(971, start_time=12, boot_id="boot-b"),
        endpoint_ref=service.private_dir / "shared.sock",
        credential=successor_credential,
    )
    files = _populate_private_files(service, predecessor, predecessor_credential)
    service.discovery = predecessor if owned else None
    service._credential = predecessor_credential if owned else None
    target = files[artifact]

    def publish_successor() -> None:
        service.private_dir.mkdir(parents=True, exist_ok=True)
        files["discovery"].write_bytes(canonical_json_bytes(successor))
        files["credential"].write_bytes(successor_credential)
        files["endpoint"].write_bytes(b"successor-endpoint")
        files["request"].write_bytes(_request_bytes(successor))

    if not owned:
        monkeypatch.setattr(
            supervisor_module,
            "observe_process_identity",
            lambda _identity: ProcessIdentityObservation(state=ProcessIdentityState.MISMATCH),
        )
    _exercise_successor_publication_at_delete(
        monkeypatch,
        target=target,
        publish_successor=publish_successor,
        retire_predecessor=(
            service._remove_owned_private_files if owned else service._cleanup_stale_discovery
        ),
    )
    assert files["discovery"].read_bytes() == canonical_json_bytes(successor)
    assert files["credential"].read_bytes() == successor_credential


def _launcher_cleanup_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    artifact: str,
) -> None:
    runtime_home = tmp_path / "runtime"
    service = SupervisorService(runtime_home, transport="unix")
    predecessor_credential = b"predecessor-credential"
    successor_credential = b"successor-credential"
    predecessor = _discovery(
        runtime_home,
        _identity(972),
        endpoint_ref=service.private_dir / "shared.sock",
        credential=predecessor_credential,
    )
    successor = _discovery(
        runtime_home,
        _identity(973, start_time=13, boot_id="boot-c"),
        endpoint_ref=service.private_dir / "shared.sock",
        credential=successor_credential,
    )
    files = _populate_private_files(service, predecessor, predecessor_credential)
    target = files[artifact]

    def publish_successor() -> None:
        files["discovery"].write_bytes(canonical_json_bytes(successor))
        files["credential"].write_bytes(successor_credential)
        files["endpoint"].write_bytes(b"successor-endpoint")
        files["request"].write_bytes(_request_bytes(successor))

    _exercise_successor_publication_at_delete(
        monkeypatch,
        target=target,
        publish_successor=publish_successor,
        retire_predecessor=lambda: codex_mcp._remove_stale_private_files(
            runtime_home, predecessor
        ),
    )
    assert files["discovery"].read_bytes() == canonical_json_bytes(successor)


def test_stale_cleanup_retains_endpoint_when_successor_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="endpoint", owned=False)


def test_owned_cleanup_retains_endpoint_when_successor_publishes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="endpoint", owned=True)


def test_owned_listener_cleanup_is_non_destructive_without_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SupervisorService(tmp_path / "runtime", transport="unix")
    service.private_dir.mkdir(parents=True, exist_ok=True)
    service.socket_path = service.private_dir / "shared-listener.sock"
    service.socket_path.write_bytes(b"predecessor-listener")
    service._listener = object()

    def publish_successor() -> None:
        service.socket_path.write_bytes(b"successor-listener")

    _exercise_successor_publication_at_delete(
        monkeypatch,
        target=service.socket_path,
        publish_successor=publish_successor,
        retire_predecessor=service._remove_owned_private_files,
    )
    assert service.socket_path.read_bytes() == b"successor-listener"


def test_launcher_stale_cleanup_retains_successor_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _launcher_cleanup_case(tmp_path, monkeypatch, artifact="endpoint")


def test_launcher_stale_cleanup_retains_successor_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _launcher_cleanup_case(tmp_path, monkeypatch, artifact="request")


def test_launcher_stale_cleanup_retains_successor_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _launcher_cleanup_case(tmp_path, monkeypatch, artifact="credential")


def test_launcher_stale_cleanup_retains_successor_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _launcher_cleanup_case(tmp_path, monkeypatch, artifact="discovery")


def test_supervisor_stale_cleanup_retains_successor_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="request", owned=False)


def test_supervisor_stale_cleanup_retains_successor_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="credential", owned=False)


def test_supervisor_stale_cleanup_retains_successor_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="discovery", owned=False)


def test_owned_cleanup_retains_successor_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="request", owned=True)


def test_owned_cleanup_retains_successor_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="credential", owned=True)


def test_owned_cleanup_retains_successor_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _supervisor_cleanup_case(tmp_path, monkeypatch, artifact="discovery", owned=True)


def test_shutdown_consumer_is_non_destructive_across_successor_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = SupervisorService(tmp_path / "runtime", transport="unix")
    predecessor = _discovery(
        service.runtime_home,
        _identity(974),
        endpoint_ref=service.private_dir / "predecessor.sock",
        credential=b"predecessor-credential",
    )
    successor = _discovery(
        service.runtime_home,
        _identity(975, start_time=14, boot_id="boot-d"),
        endpoint_ref=service.private_dir / "successor.sock",
        credential=b"successor-credential",
    )
    service.discovery = predecessor
    service.process_identity = predecessor.process_identity
    service.private_dir.mkdir(parents=True, exist_ok=True)
    service.shutdown_request_path.write_bytes(_request_bytes(predecessor))

    def publish_successor() -> None:
        service.shutdown_request_path.write_bytes(_request_bytes(successor))

    accepted: list[bool] = []
    _exercise_successor_publication_at_delete(
        monkeypatch,
        target=service.shutdown_request_path,
        publish_successor=publish_successor,
        retire_predecessor=lambda: accepted.append(service._consume_shutdown_request()),
    )
    assert accepted == [True]
    assert service.shutdown_request_path.read_bytes() == _request_bytes(successor)
