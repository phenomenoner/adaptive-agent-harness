from __future__ import annotations

import hashlib
import os
from pathlib import Path

import anyio
import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime import supervisor_client as supervisor_client_module
from aar.runtime.process_identity import (
    ProcessIdentityObservation,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
)
from aar.runtime.supervisor_client import SupervisorClient, SupervisorClientError
from aar.versions import PACKAGE_VERSION


def test_bridge_stdio_identity_unavailable_refuses_before_credential_and_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    private = runtime_home / "supervisor"
    private.mkdir(parents=True)
    credential = b"c" * 32
    digest = f"sha256:{hashlib.sha256(credential).hexdigest()}"
    identity = ProcessStartIdentity(
        pid=981,
        platform="linux",
        start_time=11,
        boot_id="boot-client",
    )
    publication_id = f"{identity.pid:032x}"
    discovery = SupervisorDiscoveryRecord.issue(
        publication_id=publication_id,
        endpoint_kind="unix",
        endpoint_ref=os.fspath(private / "supervisor.sock"),
        credential_file=f"attachment-{publication_id}.key",
        shutdown_request_file=f"shutdown-{publication_id}.request",
        process_identity=identity,
        runtime_generation=1,
        dispatcher_generation=1,
        capability_digest=canonical_sha256({"fixture": "client-unavailable"}),
        runtime_home_digest=canonical_sha256({"runtime": str(runtime_home)}),
        ready_at_unix_ms=1,
        attachment_credential_digest=digest,
        supervisor_version=f"aar-supervisor/{PACKAGE_VERSION}",
    )
    (private / "discovery.json").write_bytes(canonical_json_bytes(discovery))
    credential_path = private / discovery.credential_file
    credential_path.write_bytes(credential)
    client = SupervisorClient(runtime_home)
    reads: list[Path] = []
    connects: list[str] = []
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path, *args: object, **kwargs: object) -> bytes:
        if path == credential_path:
            reads.append(path)
        return original_read_bytes(path, *args, **kwargs)

    async def connect(_discovery: SupervisorDiscoveryRecord):
        connects.append("connect")
        raise AssertionError("identity UNAVAILABLE must refuse before connect")

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr(
        supervisor_client_module,
        "observe_process_identity",
        lambda _identity: ProcessIdentityObservation(
            state=ProcessIdentityState.UNAVAILABLE,
            error=ProcessIdentityUnavailable("injected attach observation gap"),
        ),
    )
    monkeypatch.setattr(client, "_connect", connect)

    with pytest.raises(SupervisorClientError, match="temporarily unavailable"):
        anyio.run(client.bridge_stdio)

    assert client.discovery is None
    assert client._credential is None
    assert reads == []
    assert connects == []
