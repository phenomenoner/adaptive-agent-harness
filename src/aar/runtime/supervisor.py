"""Foreground durable AAR supervisor with authenticated private MCP attachment."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import signal
import socket
import stat
import struct
import threading
import time
from pathlib import Path
from typing import Any, Literal

import anyio
import mcp_types as types
from anyio.abc import SocketAttribute, SocketStream
from mcp.shared.message import SessionMessage

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.server import AarMcpApplication, build_server
from aar.runtime.model_broker import ModelBrokerRegistry
from aar.runtime.process_identity import (
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    current_process_identity,
    process_identity_matches,
)
from aar.runtime.supervisor_protocol import (
    MAX_PRIVATE_PAYLOAD_BYTES,
    SUPERVISOR_PROTOCOL_DIGEST,
    SUPERVISOR_PROTOCOL_VERSION,
    PrivateFrame,
    SupervisorAttachAck,
    SupervisorAttachPayload,
    SupervisorLifecycleReceipt,
    SupervisorProtocolError,
    extract_mcp_binding,
)
from aar.versions import PACKAGE_VERSION

SUPERVISOR_VERSION = f"aar-supervisor/{PACKAGE_VERSION}"
PRIVATE_DIR_NAME = "supervisor"
DISCOVERY_FILE_NAME = "discovery.json"
CREDENTIAL_FILE_NAME = "attachment.key"
LIFECYCLE_FILE_NAME = "lifecycle.jsonl"
SOCKET_FILE_NAME = "supervisor.sock"
MAX_FRAME_LINE_BYTES = ((MAX_PRIVATE_PAYLOAD_BYTES + 2) // 3) * 4 + 65_536
BOOTSTRAP_AUTHORITY_DIGEST = canonical_sha256({"authority": "mcp-transport-bootstrap"})


class SupervisorError(RuntimeError):
    pass


class SupervisorAttachRejected(SupervisorError):
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
                    raise SupervisorProtocolError("private transport line exceeds disclosure bound")
                return line
            if len(self._buffer) > max_bytes:
                raise SupervisorProtocolError("private transport line exceeds disclosure bound")
            try:
                chunk = await self.stream.receive(min(65_536, max_bytes - len(self._buffer) + 1))
            except anyio.EndOfStream as error:
                if self._buffer:
                    raise SupervisorProtocolError("private transport ended mid-frame") from error
                raise
            if not chunk:
                if self._buffer:
                    raise SupervisorProtocolError("private transport ended mid-frame")
                raise anyio.EndOfStream
            self._buffer.extend(chunk)

    async def send(self, payload: bytes) -> None:
        if b"\n" in payload:
            raise SupervisorProtocolError("private transport payload contains a newline")
        await self.stream.send(payload + b"\n")

    async def send_frame(self, frame: PrivateFrame) -> None:
        await self.send(canonical_json_bytes(frame))

    async def receive_frame(self) -> PrivateFrame:
        return PrivateFrame.model_validate_json(await self.receive(), strict=True)


class SupervisorService:
    """One foreground owner for registry, dispatcher, workers and private MCP sessions."""

    def __init__(
        self,
        runtime_home: Path,
        *,
        database_path: Path | None = None,
        programmable_backend: Literal["plain", "ipython"] = "ipython",
        transport: Literal["unix", "tcp"] | None = None,
        dispatcher_concurrency: int = 2,
        model_broker_registry: ModelBrokerRegistry | None = None,
        default_model_route_profile: str | None = None,
    ) -> None:
        self.runtime_home = runtime_home.resolve()
        self.database_path = (
            (self.runtime_home / "reference.sqlite3")
            if database_path is None
            else database_path.resolve()
        )
        self.private_dir = self.runtime_home / PRIVATE_DIR_NAME
        self.discovery_path = self.private_dir / DISCOVERY_FILE_NAME
        self.credential_path = self.private_dir / CREDENTIAL_FILE_NAME
        self.lifecycle_path = self.private_dir / LIFECYCLE_FILE_NAME
        self.socket_path = self.private_dir / SOCKET_FILE_NAME
        self.programmable_backend = programmable_backend
        self.transport = transport or ("tcp" if os.name == "nt" else "unix")
        self.dispatcher_concurrency = dispatcher_concurrency
        self.model_broker_registry = model_broker_registry
        self.default_model_route_profile = default_model_route_profile
        self.application: AarMcpApplication | None = None
        self.discovery: SupervisorDiscoveryRecord | None = None
        self.process_identity: ProcessStartIdentity | None = None
        self._credential: bytes | None = None
        self._listener: Any = None
        self._runtime_home_digest = canonical_sha256(
            {"database": str(self.database_path), "runtime_home": str(self.runtime_home)}
        )
        self._stop_reason = "requested"

    @property
    def ready(self) -> SupervisorDiscoveryRecord:
        if self.discovery is None:
            raise SupervisorError("supervisor is not ready")
        return self.discovery

    async def run(self, stop_event: threading.Event | None = None) -> None:
        stop = stop_event or threading.Event()
        try:
            await self._start()
            assert self._listener is not None
            async with self._listener, anyio.create_task_group() as task_group:
                task_group.start_soon(self._listener.serve, self._handle_client)
                while not stop.is_set():
                    await anyio.sleep(0.05)
                task_group.cancel_scope.cancel()
        except BaseException:
            self._stop_reason = "startup_or_runtime_failure"
            raise
        finally:
            await self._shutdown()

    async def _start(self) -> None:
        self._prepare_private_dir()
        self.process_identity = current_process_identity()
        self._cleanup_stale_discovery()
        self.application = build_server(
            self.database_path,
            programmable_backend=self.programmable_backend,
            enable_durable_dispatch=False,
            dispatcher_concurrency=self.dispatcher_concurrency,
            runtime_owner_mode="attached-supervisor",
            supervisor_version=SUPERVISOR_VERSION,
            supervisor_protocol_version=SUPERVISOR_PROTOCOL_VERSION,
            supervisor_protocol_digest=SUPERVISOR_PROTOCOL_DIGEST,
            supervisor_process_identity_digest=canonical_sha256(self.process_identity),
            model_broker_registry=self.model_broker_registry,
            default_model_route_profile=self.default_model_route_profile,
        )
        host = self.application.host
        self._append_lifecycle("starting", reason="ownership_acquired")
        self._append_lifecycle("migrating", reason="migration_complete")
        self._append_lifecycle("recovering")
        host.recover_durable_rlm()
        host.registry.reconcile_predecessor_supervisor_runs(host.runtime_generation)
        self._credential = secrets.token_bytes(32)
        self._write_private_file(self.credential_path, self._credential)
        self._listener, endpoint_kind, endpoint_ref = await self._create_listener()
        attachment_digest = _bytes_digest(self._credential)
        discovery = SupervisorDiscoveryRecord.issue(
            endpoint_kind=endpoint_kind,
            endpoint_ref=endpoint_ref,
            process_identity=self.process_identity,
            runtime_generation=host.runtime_generation,
            dispatcher_generation=host.runtime_generation,
            capability_digest=host.capabilities.digest,
            runtime_home_digest=self._runtime_home_digest,
            ready_at_unix_ms=host.now_ms(),
            attachment_credential_digest=attachment_digest,
            supervisor_version=SUPERVISOR_VERSION,
        )
        host.registry.record_supervisor_ready(
            runtime_generation=discovery.runtime_generation,
            dispatcher_generation=discovery.dispatcher_generation,
            pid=discovery.pid,
            process_start_identity=discovery.process_identity.model_dump_json(),
            capability_digest=discovery.capability_digest,
            runtime_home_digest=discovery.runtime_home_digest,
            endpoint_kind=discovery.endpoint_kind,
            discovery_digest=discovery.discovery_digest,
        )
        self._write_private_file(self.discovery_path, canonical_json_bytes(discovery))
        self.discovery = discovery
        self._append_lifecycle("ready")
        host.start_durable_dispatch()

    async def _shutdown(self) -> None:
        if self._listener is not None:
            with contextlib.suppress(BaseException):
                await self._listener.aclose()
        application = self.application
        discovery = self.discovery
        if application is None:
            self._remove_owned_private_files()
            return
        host = application.host
        if discovery is not None:
            with contextlib.suppress(Exception):
                host.registry.transition_supervisor_run(
                    runtime_generation=discovery.runtime_generation,
                    pid=discovery.pid,
                    process_start_identity=discovery.process_identity.model_dump_json(),
                    state="draining",
                    terminal_reason=self._stop_reason,
                )
            with contextlib.suppress(Exception):
                self._append_lifecycle("draining", reason=self._stop_reason)
        host.drain_runtime_resources()
        self._remove_owned_private_files()
        if discovery is not None:
            terminal_state = "stopped" if self._stop_reason == "requested" else "reconcile-required"
            with contextlib.suppress(Exception):
                host.registry.transition_supervisor_run(
                    runtime_generation=discovery.runtime_generation,
                    pid=discovery.pid,
                    process_start_identity=discovery.process_identity.model_dump_json(),
                    state=terminal_state,
                    terminal_reason=self._stop_reason,
                )
            with contextlib.suppress(Exception):
                self._append_lifecycle(terminal_state, reason=self._stop_reason)
        application.close()
        self.application = None

    async def _create_listener(self) -> tuple[Any, str, str]:
        if self.transport == "unix":
            if os.name == "nt":
                raise SupervisorError("Unix-domain supervisor transport is unavailable on Windows")
            path = self.socket_path
            if len(os.fsencode(path)) >= 104:
                digest = self._runtime_home_digest.removeprefix("sha256:")[:24]
                path = Path("/tmp") / f"aar-{digest}.sock"
                self.socket_path = path
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
            listener = await anyio.create_unix_listener(path, mode=stat.S_IRUSR | stat.S_IWUSR)
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
            return listener, "unix", str(path)
        if self.transport == "tcp":
            listener = await anyio.create_tcp_listener(local_host="127.0.0.1", local_port=0)
            address = listener.extra(SocketAttribute.local_address)
            host, port = str(address[0]), int(address[1])
            if not ipaddress.ip_address(host).is_loopback:
                await listener.aclose()
                raise SupervisorError("fallback transport did not bind loopback")
            return listener, "tcp", f"{host}:{port}"
        raise SupervisorError(f"unsupported supervisor transport: {self.transport}")

    async def _handle_client(self, stream: SocketStream) -> None:
        lines = _SocketLines(stream)
        connection_authority = BOOTSTRAP_AUTHORITY_DIGEST
        try:
            self._verify_peer(stream)
            connection_authority = await self._authenticate(lines)
            await self._run_mcp_session(lines, connection_authority)
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return
        except BaseException as error:
            with contextlib.suppress(BaseException):
                await self._send_error(lines, type(error).__name__, connection_authority)
        finally:
            await stream.aclose()

    def _verify_peer(self, stream: SocketStream) -> None:
        if self.transport == "tcp":
            remote = stream.extra(SocketAttribute.remote_address)
            if not ipaddress.ip_address(str(remote[0])).is_loopback:
                raise SupervisorAttachRejected("private TCP peer is not loopback")
            return
        raw = stream.extra(SocketAttribute.raw_socket)
        if not hasattr(socket, "SO_PEERCRED"):
            raise SupervisorAttachRejected("Unix peer credential enforcement is unavailable")
        credentials = raw.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", credentials)
        if uid != os.geteuid():
            raise SupervisorAttachRejected("Unix peer UID does not own the supervisor")

    async def _authenticate(self, lines: _SocketLines) -> str:
        assert self._credential is not None
        discovery = self.ready
        auth = await lines.receive(max_bytes=512)
        if not auth.startswith(b"AUTH ") or not hmac.compare_digest(
            auth.removeprefix(b"AUTH "), self._credential.hex().encode("ascii")
        ):
            raise SupervisorAttachRejected("attachment credential rejected")
        frame = await lines.receive_frame()
        self._validate_frame(frame, expected_kind="attach")
        payload = SupervisorAttachPayload.model_validate_json(frame.decoded_payload(), strict=True)
        if (
            payload.runtime_generation != discovery.runtime_generation
            or payload.dispatcher_generation != discovery.dispatcher_generation
            or payload.authority_digest != frame.authority_digest
            or payload.deadline_unix_ms != frame.deadline_unix_ms
            or payload.attachment_digest != discovery.attachment_credential_digest
            or payload.capability_digest != discovery.capability_digest
            or payload.runtime_home_digest != discovery.runtime_home_digest
        ):
            raise SupervisorAttachRejected("attachment payload does not match discovery record")
        ack = SupervisorAttachAck(
            accepted=True,
            runtime_generation=discovery.runtime_generation,
            dispatcher_generation=discovery.dispatcher_generation,
            authority_digest=payload.authority_digest,
            deadline_unix_ms=payload.deadline_unix_ms,
            attachment_digest=discovery.attachment_credential_digest,
            capability_digest=discovery.capability_digest,
            runtime_home_digest=discovery.runtime_home_digest,
            process_identity=discovery.process_identity,
            supervisor_version=discovery.supervisor_version,
            ready_at_unix_ms=discovery.ready_at_unix_ms,
        )
        await lines.send_frame(
            PrivateFrame.issue(
                kind="attached",
                request_id=frame.request_id,
                trace_id=frame.trace_id,
                runtime_generation=discovery.runtime_generation,
                dispatcher_generation=discovery.dispatcher_generation,
                authority_digest=payload.authority_digest,
                deadline_unix_ms=payload.deadline_unix_ms,
                attachment_digest=discovery.attachment_credential_digest,
                payload=canonical_json_bytes(ack),
            )
        )
        return payload.authority_digest

    async def _run_mcp_session(
        self, lines: _SocketLines, connection_authority: str
    ) -> None:
        assert self.application is not None
        to_server_send, to_server_receive = anyio.create_memory_object_stream[
            SessionMessage | Exception
        ](0)
        from_server_send, from_server_receive = anyio.create_memory_object_stream[SessionMessage](0)
        bindings: dict[str, tuple[str, int, str, str]] = {}

        async def socket_reader() -> None:
            async with to_server_send:
                while True:
                    frame = await lines.receive_frame()
                    self._validate_frame(frame, expected_kind="mcp_request")
                    payload = frame.decoded_payload()
                    authority, deadline = _mcp_binding(
                        payload,
                        default_authority=connection_authority,
                        default_deadline=frame.deadline_unix_ms,
                    )
                    if authority != frame.authority_digest or deadline != frame.deadline_unix_ms:
                        raise SupervisorAttachRejected(
                            "MCP frame authority or deadline binding drifted"
                        )
                    message = types.jsonrpc_message_adapter.validate_json(payload, by_name=False)
                    key = _jsonrpc_key(payload)
                    bindings[key] = (
                        authority,
                        deadline,
                        frame.request_id,
                        frame.trace_id,
                    )
                    await to_server_send.send(SessionMessage(message))

        async def socket_writer() -> None:
            async with from_server_receive:
                async for session_message in from_server_receive:
                    payload = session_message.message.model_dump_json(
                        by_alias=True, exclude_unset=True
                    ).encode("utf-8")
                    key = _jsonrpc_key(payload)
                    authority, deadline, request_id, trace_id = bindings.pop(
                        key,
                        (
                            connection_authority,
                            int(time.time() * 1000) + 60_000,
                            f"response-{_bytes_digest(payload)[7:31]}",
                            f"trace-{_bytes_digest(payload)[7:39]}",
                        ),
                    )
                    await lines.send_frame(
                        PrivateFrame.issue(
                            kind="mcp_response",
                            request_id=request_id,
                            trace_id=trace_id,
                            runtime_generation=self.ready.runtime_generation,
                            dispatcher_generation=self.ready.dispatcher_generation,
                            authority_digest=authority,
                            deadline_unix_ms=deadline,
                            attachment_digest=self.ready.attachment_credential_digest,
                            payload=payload,
                        )
                    )

        async with anyio.create_task_group() as task_group:
            task_group.start_soon(socket_reader)
            task_group.start_soon(socket_writer)
            try:
                await self.application.server._lowlevel_server.run(
                    to_server_receive,
                    from_server_send,
                    self.application.server._lowlevel_server.create_initialization_options(),
                )
            finally:
                task_group.cancel_scope.cancel()

    def _validate_frame(self, frame: PrivateFrame, *, expected_kind: str) -> None:
        discovery = self.ready
        if frame.kind != expected_kind:
            raise SupervisorAttachRejected(f"expected {expected_kind} private frame")
        if (
            frame.runtime_generation != discovery.runtime_generation
            or frame.dispatcher_generation != discovery.dispatcher_generation
            or frame.attachment_digest != discovery.attachment_credential_digest
        ):
            raise SupervisorAttachRejected("private frame generation or attachment fence is stale")
        if frame.deadline_unix_ms < int(time.time() * 1000):
            raise SupervisorAttachRejected("private frame deadline expired")

    async def _send_error(
        self, lines: _SocketLines, reason: str, connection_authority: str
    ) -> None:
        discovery = self.discovery
        if discovery is None:
            return
        now = int(time.time() * 1000)
        payload = canonical_json_bytes({"error": reason})
        await lines.send_frame(
            PrivateFrame.issue(
                kind="error",
                request_id=f"error-{_bytes_digest(payload)[7:31]}",
                trace_id=f"trace-{_bytes_digest(payload)[7:39]}",
                runtime_generation=discovery.runtime_generation,
                dispatcher_generation=discovery.dispatcher_generation,
                authority_digest=connection_authority,
                deadline_unix_ms=now + 60_000,
                attachment_digest=discovery.attachment_credential_digest,
                payload=payload,
            )
        )

    def _prepare_private_dir(self) -> None:
        self.runtime_home.mkdir(parents=True, exist_ok=True)
        self.private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        if os.name != "nt":
            mode = stat.S_IMODE(self.private_dir.stat().st_mode)
            if mode & 0o077:
                raise SupervisorError("supervisor private directory is not owner-only")

    def _cleanup_stale_discovery(self) -> None:
        if not self.discovery_path.exists():
            return
        try:
            prior = SupervisorDiscoveryRecord.model_validate_json(
                self.discovery_path.read_bytes(), strict=True
            )
        except ValueError as error:
            raise SupervisorError("existing supervisor discovery record is invalid") from error
        if process_identity_matches(prior.process_identity):
            raise SupervisorError("existing discovery record still names a live exact process")
        self.discovery_path.unlink()
        with contextlib.suppress(FileNotFoundError):
            self.credential_path.unlink()
        if prior.endpoint_kind == "unix":
            endpoint = Path(prior.endpoint_ref)
            with contextlib.suppress(FileNotFoundError):
                endpoint.unlink()

    def _remove_owned_private_files(self) -> None:
        discovery = self.discovery
        if discovery is not None and self.discovery_path.exists():
            with contextlib.suppress(ValueError, OSError):
                observed = SupervisorDiscoveryRecord.model_validate_json(
                    self.discovery_path.read_bytes(), strict=True
                )
                if observed.discovery_digest == discovery.discovery_digest:
                    self.discovery_path.unlink()
        if self._credential is not None and self.credential_path.exists():
            with contextlib.suppress(OSError):
                if hmac.compare_digest(self.credential_path.read_bytes(), self._credential):
                    self.credential_path.unlink()
        if discovery is not None and discovery.endpoint_kind == "unix":
            endpoint = Path(discovery.endpoint_ref)
            with contextlib.suppress(FileNotFoundError):
                endpoint.unlink()
        elif discovery is None and self.transport == "unix" and self._listener is not None:
            with contextlib.suppress(FileNotFoundError):
                self.socket_path.unlink()

    def _append_lifecycle(self, state: str, *, reason: str | None = None) -> None:
        identity = self.process_identity
        application = self.application
        if identity is None or application is None:
            return
        generation = application.host.runtime_generation
        capability_digest = application.host.capabilities.digest
        receipt = SupervisorLifecycleReceipt.issue(
            state=state,
            process_identity=identity,
            runtime_generation=generation,
            dispatcher_generation=generation,
            capability_digest=capability_digest,
            runtime_home_digest=self._runtime_home_digest,
            at_unix_ms=int(time.time() * 1000),
            supervisor_version=SUPERVISOR_VERSION,
            reason=reason,
        )
        self.private_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with self.lifecycle_path.open("ab") as stream:
            stream.write(canonical_json_bytes(receipt) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(self.lifecycle_path, 0o600)

    @staticmethod
    def _write_private_file(path: Path, content: bytes) -> None:
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                os.chmod(path, 0o600)
        finally:
            with contextlib.suppress(FileNotFoundError):
                temporary.unlink()


def _bytes_digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _jsonrpc_key(payload: bytes) -> str:
    try:
        document = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return f"invalid-{_bytes_digest(payload)}"
    if not isinstance(document, dict) or "id" not in document:
        return f"notification-{_bytes_digest(payload)}"
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
        message = str(error)
        if "is missing" not in message:
            raise
        return default_authority, default_deadline


def _default_runtime_home() -> Path:
    configured = os.environ.get("AAR_RUNTIME_HOME")
    if configured:
        return Path(configured)
    database = os.environ.get("AAR_DATABASE")
    if database:
        return Path(database).resolve().parent
    return Path.home() / ".aar"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path, default=_default_runtime_home())
    parser.add_argument("--database", type=Path)
    parser.add_argument(
        "--programmable-backend", choices=("plain", "ipython"), default="ipython"
    )
    parser.add_argument("--transport", choices=("unix", "tcp"))
    parser.add_argument("--dispatcher-concurrency", type=int, default=2)
    args = parser.parse_args(argv)
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    service = SupervisorService(
        args.runtime_home,
        database_path=args.database,
        programmable_backend=args.programmable_backend,
        transport=args.transport,
        dispatcher_concurrency=args.dispatcher_concurrency,
    )
    try:
        anyio.run(service.run, stop)
    except Exception as error:
        print(f"aar-supervisor failed: {type(error).__name__}: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
