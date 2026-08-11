"""Ephemeral stdio frontend for an exact host-managed AAR supervisor."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import sys
import time
from pathlib import Path

import anyio
from anyio.abc import SocketStream

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.process_identity import (
    SupervisorDiscoveryRecord,
    current_process_identity,
    process_identity_matches,
)
from aar.runtime.supervisor_protocol import (
    MAX_PRIVATE_PAYLOAD_BYTES,
    PrivateFrame,
    SupervisorAttachAck,
    SupervisorAttachPayload,
    SupervisorProtocolError,
    extract_mcp_binding,
)
from aar.versions import PACKAGE_VERSION

PRIVATE_DIR_NAME = "supervisor"
DISCOVERY_FILE_NAME = "discovery.json"
CREDENTIAL_FILE_NAME = "attachment.key"
MAX_FRAME_LINE_BYTES = ((MAX_PRIVATE_PAYLOAD_BYTES + 2) // 3) * 4 + 65_536


class SupervisorClientError(RuntimeError):
    pass


class _SocketLines:
    def __init__(self, stream: SocketStream) -> None:
        self.stream = stream
        self._buffer = bytearray()

    async def receive(self, *, max_bytes: int = MAX_FRAME_LINE_BYTES) -> bytes:
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline])
                del self._buffer[: newline + 1]
                if len(line) > max_bytes:
                    raise SupervisorClientError("private response exceeds disclosure bound")
                return line
            if len(self._buffer) > max_bytes:
                raise SupervisorClientError("private response exceeds disclosure bound")
            try:
                chunk = await self.stream.receive(min(65_536, max_bytes - len(self._buffer) + 1))
            except anyio.EndOfStream as error:
                if self._buffer:
                    raise SupervisorClientError("private response ended mid-frame") from error
                raise
            if not chunk:
                if self._buffer:
                    raise SupervisorClientError("private response ended mid-frame")
                raise anyio.EndOfStream
            self._buffer.extend(chunk)

    async def send(self, payload: bytes) -> None:
        if b"\n" in payload:
            raise SupervisorClientError("private request contains a newline")
        await self.stream.send(payload + b"\n")

    async def send_frame(self, frame: PrivateFrame) -> None:
        await self.send(canonical_json_bytes(frame))

    async def receive_frame(self) -> PrivateFrame:
        return PrivateFrame.model_validate_json(await self.receive(), strict=True)


class SupervisorClient:
    def __init__(self, runtime_home: Path) -> None:
        self.runtime_home = runtime_home.resolve()
        self.private_dir = self.runtime_home / PRIVATE_DIR_NAME
        self.discovery_path = self.private_dir / DISCOVERY_FILE_NAME
        self.credential_path = self.private_dir / CREDENTIAL_FILE_NAME
        self.discovery: SupervisorDiscoveryRecord | None = None
        self._credential: bytes | None = None
        self._authority_digest: str | None = None

    async def bridge_stdio(self) -> None:
        discovery = self._load_discovery()
        credential = self._load_credential(discovery)
        stream = await self._connect(discovery)
        lines = _SocketLines(stream)
        try:
            await lines.send(b"AUTH " + credential.hex().encode("ascii"))
            authority = canonical_sha256(
                {
                    "adapter": "aar-mcp",
                    "package_version": PACKAGE_VERSION,
                    "process_identity": current_process_identity().model_dump(mode="json"),
                    "runtime_home_digest": discovery.runtime_home_digest,
                }
            )
            self._authority_digest = authority
            now = int(time.time() * 1000)
            attach = SupervisorAttachPayload(
                runtime_generation=discovery.runtime_generation,
                dispatcher_generation=discovery.dispatcher_generation,
                authority_digest=authority,
                deadline_unix_ms=now + 60_000,
                attachment_digest=discovery.attachment_credential_digest,
                capability_digest=discovery.capability_digest,
                runtime_home_digest=discovery.runtime_home_digest,
                client_id=f"aar-mcp-{os.getpid()}",
                adapter_version=f"aar-mcp/{PACKAGE_VERSION}",
            )
            request_id = f"attach-{os.getpid()}-{now}"
            trace_id = f"trace-{hashlib.sha256(canonical_json_bytes(attach)).hexdigest()[:32]}"
            await lines.send_frame(
                PrivateFrame.issue(
                    kind="attach",
                    request_id=request_id,
                    trace_id=trace_id,
                    runtime_generation=discovery.runtime_generation,
                    dispatcher_generation=discovery.dispatcher_generation,
                    authority_digest=authority,
                    deadline_unix_ms=attach.deadline_unix_ms,
                    attachment_digest=discovery.attachment_credential_digest,
                    payload=canonical_json_bytes(attach),
                )
            )
            frame = await lines.receive_frame()
            if frame.kind == "error":
                raise SupervisorClientError("supervisor rejected attachment")
            if frame.kind != "attached":
                raise SupervisorClientError("supervisor did not return attached receipt")
            ack = SupervisorAttachAck.model_validate_json(frame.decoded_payload(), strict=True)
            if (
                not ack.accepted
                or ack.runtime_generation != discovery.runtime_generation
                or ack.dispatcher_generation != discovery.dispatcher_generation
                or ack.process_identity != discovery.process_identity
                or ack.attachment_digest != discovery.attachment_credential_digest
                or ack.capability_digest != discovery.capability_digest
                or ack.runtime_home_digest != discovery.runtime_home_digest
            ):
                raise SupervisorClientError("supervisor attached receipt does not match discovery")
            await self._bridge(lines)
        finally:
            await stream.aclose()

    async def _bridge(self, lines: _SocketLines) -> None:
        assert self.discovery is not None
        assert self._authority_digest is not None
        pending: set[str] = set()

        async def stdin_to_supervisor() -> None:
            stdin = anyio.wrap_file(sys.stdin.buffer)
            while True:
                line = await stdin.readline()
                if not line:
                    return
                payload = bytes(line).rstrip(b"\r\n")
                if not payload:
                    continue
                authority, deadline = _mcp_binding(
                    payload,
                    default_authority=self._authority_digest,
                    default_deadline=int(time.time() * 1000) + 60_000,
                )
                payload_digest = _bytes_digest(payload)
                response_key = _jsonrpc_key(payload)
                if response_key is not None:
                    pending.add(response_key)
                await lines.send_frame(
                    PrivateFrame.issue(
                        kind="mcp_request",
                        request_id=_request_id(payload, payload_digest),
                        trace_id=f"trace-{payload_digest.removeprefix('sha256:')[:32]}",
                        runtime_generation=self.discovery.runtime_generation,
                        dispatcher_generation=self.discovery.dispatcher_generation,
                        authority_digest=authority,
                        deadline_unix_ms=deadline,
                        attachment_digest=self.discovery.attachment_credential_digest,
                        payload=payload,
                    )
                )

        async def supervisor_to_stdout() -> None:
            while True:
                frame = await lines.receive_frame()
                self._validate_response_frame(frame)
                if frame.kind == "error":
                    raise SupervisorClientError("supervisor private session failed")
                if frame.kind != "mcp_response":
                    raise SupervisorClientError("unexpected private supervisor response kind")
                payload = frame.decoded_payload()
                response_key = _jsonrpc_key(payload)
                await anyio.to_thread.run_sync(_write_stdout, payload + b"\n")
                if response_key is not None:
                    pending.discard(response_key)

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(supervisor_to_stdout)
            await stdin_to_supervisor()
            with anyio.fail_after(65):
                while pending:
                    await anyio.sleep(0.01)
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await lines.stream.send_eof()
            task_group.cancel_scope.cancel()

    def _validate_response_frame(self, frame: PrivateFrame) -> None:
        assert self.discovery is not None
        if (
            frame.runtime_generation != self.discovery.runtime_generation
            or frame.dispatcher_generation != self.discovery.dispatcher_generation
            or frame.attachment_digest != self.discovery.attachment_credential_digest
        ):
            raise SupervisorClientError(
                "supervisor response generation or attachment fence is stale"
            )
        if frame.deadline_unix_ms < int(time.time() * 1000):
            raise SupervisorClientError("supervisor response deadline expired")

    def _load_discovery(self) -> SupervisorDiscoveryRecord:
        try:
            discovery = SupervisorDiscoveryRecord.model_validate_json(
                self.discovery_path.read_bytes(), strict=True
            )
        except (OSError, ValueError) as error:
            raise SupervisorClientError("valid supervisor discovery is unavailable") from error
        if not process_identity_matches(discovery.process_identity):
            raise SupervisorClientError("supervisor discovery names an absent or reused process")
        self.discovery = discovery
        return discovery

    def _load_credential(self, discovery: SupervisorDiscoveryRecord) -> bytes:
        try:
            if os.name != "nt" and self.credential_path.stat().st_mode & 0o077:
                raise SupervisorClientError("attachment credential is not owner-only")
            credential = self.credential_path.read_bytes()
        except OSError as error:
            raise SupervisorClientError("attachment credential is unavailable") from error
        if not hmac.compare_digest(
            _bytes_digest(credential), discovery.attachment_credential_digest
        ):
            raise SupervisorClientError("attachment credential digest does not match discovery")
        if len(credential) != 32:
            raise SupervisorClientError("attachment credential length is invalid")
        self._credential = credential
        return credential

    @staticmethod
    async def _connect(discovery: SupervisorDiscoveryRecord) -> SocketStream:
        try:
            if discovery.endpoint_kind == "unix":
                return await anyio.connect_unix(discovery.endpoint_ref)
            host, raw_port = discovery.endpoint_ref.rsplit(":", 1)
            if not ipaddress.ip_address(host).is_loopback:
                raise SupervisorClientError("fallback supervisor endpoint is not loopback")
            return await anyio.connect_tcp(host, int(raw_port))
        except (OSError, ValueError) as error:
            raise SupervisorClientError("supervisor private endpoint is unavailable") from error


def _write_stdout(payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(1, view)
        view = view[written:]


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _request_id(payload: bytes, payload_digest: str) -> str:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"request-{payload_digest.removeprefix('sha256:')[:24]}"
    if isinstance(document, dict) and "id" in document:
        material = json.dumps(document["id"], sort_keys=True, separators=(",", ":"))
        return f"request-{hashlib.sha256(material.encode()).hexdigest()[:24]}"
    return f"notification-{payload_digest.removeprefix('sha256:')[:24]}"


def _jsonrpc_key(payload: bytes) -> str | None:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(document, dict) or "id" not in document:
        return None
    return json.dumps(document["id"], sort_keys=True, separators=(",", ":"))


def _mcp_binding(
    payload: bytes,
    *,
    default_authority: str,
    default_deadline: int,
) -> tuple[str, int]:
    try:
        binding = extract_mcp_binding(payload)
        return binding.authority_digest, binding.deadline_unix_ms
    except SupervisorProtocolError as error:
        if "is missing" not in str(error):
            raise
        return default_authority, default_deadline


def _default_runtime_home() -> Path:
    configured = os.environ.get("AAR_RUNTIME_HOME")
    if configured:
        return Path(configured)
    database = os.environ.get("AAR_DATABASE")
    if database:
        return Path(database).resolve().parent
    return Path.cwd() / ".aar"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path, default=_default_runtime_home())
    args = parser.parse_args(argv)
    try:
        anyio.run(SupervisorClient(args.runtime_home).bridge_stdio)
    except Exception as error:
        print(f"aar-mcp attach failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
