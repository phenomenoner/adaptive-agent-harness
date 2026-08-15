"""Codex host adapter that ensures one durable AAR supervisor before stdio attach."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import os
import signal
import stat
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from aar.canonical import canonical_json_bytes
from aar.runtime.process_identity import (
    ProcessIdentityMismatch,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    SupervisorDiscoveryRecord,
    observe_process_identity,
    open_exact_process,
)
from aar.runtime.python_child import exact_module_command
from aar.runtime.supervisor_client import (
    CREDENTIAL_FILE_NAME,
    DISCOVERY_FILE_NAME,
    PRIVATE_DIR_NAME,
    SupervisorClient,
)
from aar.versions import PACKAGE_VERSION

RUNTIME_HOME_ENV = "AAR_CODEX_RUNTIME_HOME"
PROGRAMMABLE_BACKEND_ENV = "AAR_CODEX_PROGRAMMABLE_BACKEND"
DEFAULT_STARTUP_TIMEOUT_SEC = 30.0
MAX_STARTUP_TIMEOUT_SEC = 120.0
STARTUP_LOCK_FILE_NAME = "codex-launch.lock"
LAUNCH_LOG_FILE_NAME = "codex-supervisor.log"
SHUTDOWN_REQUEST_FILE_NAME = "shutdown.request"
SHUTDOWN_REQUEST_SCHEMA_VERSION = "aar.supervisor.shutdown-request.v1"
MAX_LAUNCH_LOG_BYTES = 262_144
EXPECTED_SUPERVISOR_VERSION = f"aar-supervisor/{PACKAGE_VERSION}"


class CodexMcpLauncherError(RuntimeError):
    pass


def default_runtime_home() -> Path:
    """Resolve the stable per-user runtime home owned by the Codex adapter."""
    configured = os.environ.get(RUNTIME_HOME_ENV)
    root = Path(configured).expanduser() if configured else Path.home() / ".aar" / "codex"
    return root.resolve(strict=False)


def _default_programmable_backend() -> str:
    backend = os.environ.get(PROGRAMMABLE_BACKEND_ENV, "ipython")
    if backend not in {"ipython", "plain"}:
        raise CodexMcpLauncherError(
            f"{PROGRAMMABLE_BACKEND_ENV} must be 'ipython' or 'plain'"
        )
    return backend


def _private_dir(runtime_home: Path) -> Path:
    private = runtime_home / PRIVATE_DIR_NAME
    runtime_home.mkdir(parents=True, exist_ok=True)
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    if os.name != "nt" and stat.S_IMODE(private.stat().st_mode) & 0o077:
        raise CodexMcpLauncherError("Codex supervisor private directory is not owner-only")
    return private


def _read_discovery(
    runtime_home: Path, *, tolerate_transient_unreadable: bool = False
) -> SupervisorDiscoveryRecord | None:
    path = runtime_home / PRIVATE_DIR_NAME / DISCOVERY_FILE_NAME
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        if tolerate_transient_unreadable:
            return None
        raise CodexMcpLauncherError("Codex supervisor discovery is unreadable") from error
    try:
        discovery = SupervisorDiscoveryRecord.model_validate_json(content, strict=True)
    except ValueError as error:
        raise CodexMcpLauncherError("Codex supervisor discovery is invalid") from error
    return discovery


def _load_discovery(
    runtime_home: Path, *, tolerate_transient_unreadable: bool = False
) -> SupervisorDiscoveryRecord | None:
    discovery = _read_discovery(
        runtime_home,
        tolerate_transient_unreadable=tolerate_transient_unreadable,
    )
    if discovery is None:
        return None
    observation = observe_process_identity(discovery.process_identity)
    if observation.state is ProcessIdentityState.UNAVAILABLE:
        raise CodexMcpLauncherError(
            "Codex supervisor process identity unavailable; refusing cleanup or replacement"
        ) from observation.error
    if observation.state is ProcessIdentityState.MISMATCH:
        return None
    return discovery


def _open_lock(path: Path) -> BinaryIO:
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    if os.fstat(descriptor).st_size == 0:
        os.write(descriptor, b"\0")
    handle = os.fdopen(descriptor, "r+b", buffering=0)
    if os.name != "nt" and stat.S_IMODE(path.stat().st_mode) & 0o077:
        handle.close()
        raise CodexMcpLauncherError("Codex supervisor startup lock is not owner-only")
    return handle


@contextmanager
def _startup_lock(path: Path, timeout_sec: float) -> Iterator[None]:
    handle = _open_lock(path)
    deadline = time.monotonic() + timeout_sec
    locked = False
    try:
        while not locked:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise CodexMcpLauncherError(
                        "timed out waiting for Codex supervisor startup ownership"
                    ) from error
                time.sleep(0.05)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _launch_log_tail(path: Path, *, max_bytes: int = 4_096) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            stream.seek(max(0, stream.tell() - max_bytes))
            return stream.read(max_bytes).decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def _spawn_supervisor(
    runtime_home: Path,
    *,
    programmable_backend: str,
    log_path: Path,
) -> subprocess.Popen[bytes]:
    command = exact_module_command(
        "aar.runtime.supervisor",
        [
            "--runtime-home",
            os.fspath(runtime_home),
            "--programmable-backend",
            programmable_backend,
        ],
        isolated=False,
    )
    if log_path.exists() and log_path.stat().st_size > MAX_LAUNCH_LOG_BYTES:
        descriptor = os.open(log_path, os.O_TRUNC | os.O_WRONLY, 0o600)
        os.close(descriptor)
    descriptor = os.open(log_path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    if os.name != "nt" and stat.S_IMODE(log_path.stat().st_mode) & 0o077:
        os.close(descriptor)
        raise CodexMcpLauncherError("Codex supervisor launch log is not owner-only")
    log = os.fdopen(descriptor, "ab", buffering=0)
    options: dict[str, object] = {
        "close_fds": True,
        "cwd": os.fspath(runtime_home),
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": log,
    }
    if os.name == "nt":
        options["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        )
    else:
        options["start_new_session"] = True
    try:
        return subprocess.Popen(command, **options)  # type: ignore[arg-type]
    finally:
        log.close()


def _reap_spawned_process(process: subprocess.Popen[bytes], timeout_sec: float) -> None:
    """Make the exact Popen child terminal and reaped before startup ownership is released."""

    if process.poll() is not None:
        process.wait()
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        process.wait(timeout=timeout_sec)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    process.wait(timeout=timeout_sec)


def _accept_discovery_after_spawn(
    discovery: SupervisorDiscoveryRecord,
    process: subprocess.Popen[bytes],
    *,
    cleanup_timeout_sec: float,
) -> SupervisorDiscoveryRecord:
    if discovery.pid == process.pid:
        return discovery
    _reap_spawned_process(process, cleanup_timeout_sec)
    return discovery


def ensure_codex_supervisor(
    runtime_home: Path,
    *,
    programmable_backend: str = "ipython",
    startup_timeout_sec: float = DEFAULT_STARTUP_TIMEOUT_SEC,
) -> SupervisorDiscoveryRecord:
    """Reuse one exact live owner or start it under a bounded cross-process lock."""
    if not 0 < startup_timeout_sec <= MAX_STARTUP_TIMEOUT_SEC:
        raise CodexMcpLauncherError(
            f"startup timeout must be within (0, {MAX_STARTUP_TIMEOUT_SEC:g}] seconds"
        )
    if programmable_backend not in {"ipython", "plain"}:
        raise CodexMcpLauncherError("programmable backend must be 'ipython' or 'plain'")
    runtime_home = runtime_home.expanduser().resolve(strict=False)
    private = _private_dir(runtime_home)
    existing = _load_discovery(runtime_home)
    if existing is not None:
        if existing.supervisor_version != EXPECTED_SUPERVISOR_VERSION:
            raise CodexMcpLauncherError(
                "the live Codex supervisor package version does not match this launcher; "
                "stop the exact runtime before upgrading"
            )
        return existing
    lock_path = private / STARTUP_LOCK_FILE_NAME
    log_path = private / LAUNCH_LOG_FILE_NAME
    with _startup_lock(lock_path, startup_timeout_sec):
        existing = _load_discovery(runtime_home)
        if existing is not None:
            if existing.supervisor_version != EXPECTED_SUPERVISOR_VERSION:
                raise CodexMcpLauncherError(
                    "the live Codex supervisor package version does not match this launcher; "
                    "stop the exact runtime before upgrading"
                )
            return existing
        process = _spawn_supervisor(
            runtime_home,
            programmable_backend=programmable_backend,
            log_path=log_path,
        )
        try:
            deadline = time.monotonic() + startup_timeout_sec
            while time.monotonic() < deadline:
                discovery = _load_discovery(
                    runtime_home,
                    tolerate_transient_unreadable=True,
                )
                if discovery is not None:
                    return _accept_discovery_after_spawn(
                        discovery,
                        process,
                        cleanup_timeout_sec=startup_timeout_sec,
                    )
                returncode = process.poll()
                if returncode is not None:
                    detail = _launch_log_tail(log_path)
                    suffix = f": {detail}" if detail else ""
                    raise CodexMcpLauncherError(
                        f"Codex supervisor exited before Ready ({returncode}){suffix}"
                    )
                time.sleep(0.05)
            discovery = _load_discovery(
                runtime_home,
                tolerate_transient_unreadable=True,
            )
            if discovery is not None:
                return _accept_discovery_after_spawn(
                    discovery,
                    process,
                    cleanup_timeout_sec=startup_timeout_sec,
                )
            raise CodexMcpLauncherError(
                "Codex supervisor did not become Ready before timeout"
            )
        except BaseException as startup_error:
            try:
                _reap_spawned_process(process, startup_timeout_sec)
            except BaseException as cleanup_error:
                raise CodexMcpLauncherError(
                    "Codex supervisor startup failed and its exact child could not be reaped"
                ) from cleanup_error
            raise startup_error


def _shutdown_request_bytes(discovery: SupervisorDiscoveryRecord) -> bytes:
    return canonical_json_bytes(
        {
            "discovery_digest": discovery.discovery_digest,
            "process_identity": discovery.process_identity.model_dump(mode="json"),
            "schema_version": SHUTDOWN_REQUEST_SCHEMA_VERSION,
        }
    )


def _discovery_bytes_match(path: Path, discovery: SupervisorDiscoveryRecord) -> bool:
    try:
        observed = SupervisorDiscoveryRecord.model_validate_json(path.read_bytes(), strict=True)
    except (OSError, ValueError):
        return False
    return observed.discovery_digest == discovery.discovery_digest


def _unlink_if_bytes_match(path: Path, expected: bytes) -> bool:
    try:
        first = path.read_bytes()
        if not hmac.compare_digest(first, expected):
            return False
        second = path.read_bytes()
        if not hmac.compare_digest(second, expected):
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _remove_stale_private_files(
    runtime_home: Path, discovery: SupervisorDiscoveryRecord
) -> None:
    private = runtime_home / PRIVATE_DIR_NAME
    discovery_path = private / DISCOVERY_FILE_NAME
    credential_path = private / CREDENTIAL_FILE_NAME
    request_path = private / SHUTDOWN_REQUEST_FILE_NAME
    if not _discovery_bytes_match(discovery_path, discovery):
        return
    if discovery.endpoint_kind == "unix" and _discovery_bytes_match(
        discovery_path, discovery
    ):
        with contextlib.suppress(FileNotFoundError):
            Path(discovery.endpoint_ref).unlink()
    if _discovery_bytes_match(discovery_path, discovery):
        _unlink_if_bytes_match(request_path, _shutdown_request_bytes(discovery))
    if _discovery_bytes_match(discovery_path, discovery):
        try:
            credential = credential_path.read_bytes()
        except OSError:
            credential = None
        if (
            credential is not None
            and f"sha256:{hashlib.sha256(credential).hexdigest()}"
            == discovery.attachment_credential_digest
        ):
            _unlink_if_bytes_match(credential_path, credential)
    if _discovery_bytes_match(discovery_path, discovery):
        _unlink_if_bytes_match(discovery_path, canonical_json_bytes(discovery))


def _write_shutdown_request(
    runtime_home: Path, discovery: SupervisorDiscoveryRecord
) -> None:
    private = _private_dir(runtime_home)
    target = private / SHUTDOWN_REQUEST_FILE_NAME
    temporary = private / f".{SHUTDOWN_REQUEST_FILE_NAME}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    payload = _shutdown_request_bytes(discovery)
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.replace(temporary, target)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temporary.unlink()


def _terminate_windows_process(discovery: SupervisorDiscoveryRecord, timeout_sec: float) -> None:
    try:
        target = open_exact_process(discovery.process_identity, terminate=True)
    except ProcessIdentityMismatch:
        return
    except ProcessIdentityUnavailable as error:
        raise CodexMcpLauncherError(
            "could not open the exact Codex supervisor process for termination"
        ) from error
    with target:
        sent = target.send_signal(signal.SIGTERM)
        if not sent:
            try:
                terminal = target.wait(timeout_sec)
            except ProcessIdentityUnavailable as error:
                raise CodexMcpLauncherError(
                    f"could not terminate Codex supervisor (winerror={target.last_error})"
                ) from error
            if terminal:
                return
            raise CodexMcpLauncherError(
                "Codex supervisor termination was denied and it remained live"
            )
        try:
            terminal = target.wait(timeout_sec)
        except ProcessIdentityUnavailable as error:
            raise CodexMcpLauncherError(
                "could not wait for Codex supervisor termination"
            ) from error
        if not terminal:
            raise CodexMcpLauncherError("Codex supervisor did not stop before timeout")


def stop_codex_supervisor(runtime_home: Path, *, timeout_sec: float = 20.0) -> bool:
    """Stop one exact discovered supervisor for preflight cleanup or an upgrade."""
    runtime_home = runtime_home.expanduser().resolve(strict=False)
    discovery = _read_discovery(runtime_home)
    if discovery is None:
        return False
    observation = observe_process_identity(discovery.process_identity)
    if observation.state is ProcessIdentityState.UNAVAILABLE:
        raise CodexMcpLauncherError(
            "Codex supervisor process identity unavailable; stop was not attempted"
        ) from observation.error
    if observation.state is ProcessIdentityState.MISMATCH:
        _remove_stale_private_files(runtime_home, discovery)
        return True
    _write_shutdown_request(runtime_home, discovery)
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        observation = observe_process_identity(discovery.process_identity)
        if observation.state is ProcessIdentityState.UNAVAILABLE:
            raise CodexMcpLauncherError(
                "Codex supervisor process identity unavailable while stopping"
            ) from observation.error
        if observation.state is ProcessIdentityState.MISMATCH:
            _remove_stale_private_files(runtime_home, discovery)
            return True
        time.sleep(0.05)
    if os.name == "nt":
        _terminate_windows_process(discovery, timeout_sec)
        # WaitForSingleObject on the exact opened process handle is the
        # authoritative Windows terminal readback. Process-table identity can
        # remain observable briefly afterward and must not turn a completed
        # fallback termination into a false setup failure.
        _remove_stale_private_files(runtime_home, discovery)
        return True
    try:
        target = open_exact_process(discovery.process_identity, terminate=True)
    except ProcessIdentityMismatch:
        _remove_stale_private_files(runtime_home, discovery)
        return True
    except ProcessIdentityUnavailable as error:
        raise CodexMcpLauncherError(
            "exact Codex supervisor fallback termination is unavailable"
        ) from error
    with target:
        target.send_signal(signal.SIGTERM)
        if not target.wait(timeout_sec):
            raise CodexMcpLauncherError(
                "Codex supervisor did not stop after fallback termination"
            )
    _remove_stale_private_files(runtime_home, discovery)
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-home", type=Path, default=default_runtime_home())
    parser.add_argument(
        "--programmable-backend",
        choices=("plain", "ipython"),
        default=None,
    )
    parser.add_argument(
        "--startup-timeout-sec",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT_SEC,
    )
    args = parser.parse_args(argv)
    try:
        backend = args.programmable_backend or _default_programmable_backend()
        runtime_home = args.runtime_home.expanduser().resolve(strict=False)
        ensure_codex_supervisor(
            runtime_home,
            programmable_backend=backend,
            startup_timeout_sec=args.startup_timeout_sec,
        )
        import anyio

        anyio.run(SupervisorClient(runtime_home).bridge_stdio)
    except Exception as error:
        print(
            f"aar-codex-mcp failed: {type(error).__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
