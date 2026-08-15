from __future__ import annotations

import contextlib
import ctypes
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.compat import codex_mcp
from aar.compat.codex_mcp import (
    SHUTDOWN_REQUEST_SCHEMA_VERSION,
    stop_codex_supervisor,
)
from aar.runtime import process_identity as process_identity_module
from aar.runtime.process_identity import (
    ProcessIdentityObservation,
    ProcessIdentityState,
    ProcessIdentityUnavailable,
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    process_identity_matches,
)

ROOT = Path(__file__).resolve().parents[1]
DIGEST = canonical_sha256({"fixture": "codex-mcp-launcher"})


def _identity(pid: int, *, start_time: int = 11, boot_id: str = "boot-a") -> ProcessStartIdentity:
    return ProcessStartIdentity(
        pid=pid,
        platform="linux",
        start_time=start_time,
        boot_id=boot_id,
    )


def _windows_identity(pid: int, *, start_time: int = 11) -> ProcessStartIdentity:
    return ProcessStartIdentity(
        pid=pid,
        platform="windows",
        start_time=start_time,
    )


def _discovery(
    runtime_home: Path,
    identity: ProcessStartIdentity,
    *,
    endpoint_ref: Path | None = None,
    credential_digest: str = DIGEST,
) -> SupervisorDiscoveryRecord:
    publication_id = f"{identity.pid:032x}"
    return SupervisorDiscoveryRecord.issue(
        publication_id=publication_id,
        endpoint_kind="unix",
        endpoint_ref=os.fspath(endpoint_ref or (runtime_home / "supervisor.sock")),
        credential_file=f"attachment-{publication_id}.key",
        shutdown_request_file=f"shutdown-{publication_id}.request",
        process_identity=identity,
        runtime_generation=1,
        dispatcher_generation=1,
        capability_digest=DIGEST,
        runtime_home_digest=DIGEST,
        ready_at_unix_ms=1,
        attachment_credential_digest=credential_digest,
        supervisor_version=codex_mcp.EXPECTED_SUPERVISOR_VERSION,
    )


def _write_discovery(runtime_home: Path, discovery: SupervisorDiscoveryRecord) -> Path:
    private = runtime_home / "supervisor"
    private.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        private.chmod(0o700)
    path = private / "discovery.json"
    path.write_bytes(canonical_json_bytes(discovery))
    return path


class _FakeChild:
    def __init__(
        self,
        events: list[str],
        *,
        pid: int = 901,
        identity: ProcessStartIdentity | None = None,
    ) -> None:
        self.events = events
        self.pid = pid
        self.identity = identity or _identity(pid)
        self.returncode: int | None = None
        self.exact_signal_count = 0
        self.exact_wait_count = 0
        self.terminal_receipt_count = 0
        self._terminal_receipt: object | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.events.append("terminate")
        self.returncode = -15

    def kill(self) -> None:
        self.events.append("kill")
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        assert self.events and self.events[-1] in {"terminate", "kill"}
        self.events.append("wait")
        if self.returncode is None:
            self.returncode = -15
        return self.returncode

    def terminalize(self, timeout_sec: float) -> object:
        assert timeout_sec > 0
        if self._terminal_receipt is not None:
            return self._terminal_receipt
        if self.returncode is None:
            self.events.append("exact-signal")
            self.exact_signal_count += 1
            self.returncode = -15
        self.events.append("exact-wait")
        self.exact_wait_count += 1
        self.terminal_receipt_count += 1
        self._terminal_receipt = object()
        return self._terminal_receipt


def test_reap_spawned_process_joins_one_exact_terminal_result() -> None:
    events: list[str] = []
    child = _FakeChild(events)

    codex_mcp._reap_spawned_process(child, 1)

    assert events == ["exact-signal", "exact-wait"]
    assert child.returncode == -15
    assert child.exact_signal_count == 1
    assert child.exact_wait_count == 1
    assert child.terminal_receipt_count == 1


def _messages(client_name: str) -> str:
    requests = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": client_name, "version": "1"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "aar_capabilities", "arguments": {}},
        },
    ]
    return "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in requests)


def _run_frontend(runtime_home: Path, client_name: str) -> tuple[list[dict[str, Any]], str]:
    environment = dict(os.environ)
    environment["AAR_CODEX_RUNTIME_HOME"] = os.fspath(runtime_home)
    completed = subprocess.run(
        [sys.executable, "-m", "aar.compat.codex_mcp"],
        cwd=ROOT,
        env=environment,
        input=_messages(client_name),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return [json.loads(line) for line in completed.stdout.splitlines()], completed.stderr


def _read_discovery(runtime_home: Path) -> SupervisorDiscoveryRecord:
    path = runtime_home / "supervisor" / "discovery.json"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if path.is_file():
            try:
                discovery = SupervisorDiscoveryRecord.model_validate_json(
                    path.read_bytes(), strict=True
                )
            except ValueError:
                time.sleep(0.025)
                continue
            if process_identity_matches(discovery.process_identity):
                return discovery
        time.sleep(0.025)
    raise AssertionError("Codex launcher did not publish a live supervisor discovery")


def _stop_test_supervisor(runtime_home: Path) -> None:
    path = runtime_home / "supervisor" / "discovery.json"
    if not path.is_file():
        return
    discovery = SupervisorDiscoveryRecord.model_validate_json(path.read_bytes(), strict=True)
    assert stop_codex_supervisor(runtime_home)
    assert not process_identity_matches(discovery.process_identity)
    retained = SupervisorDiscoveryRecord.model_validate_json(path.read_bytes(), strict=True)
    assert retained == discovery


def _assert_capabilities(rows: list[dict[str, Any]], discovery: SupervisorDiscoveryRecord) -> None:
    tools = next(item for item in rows if item.get("id") == 2)["result"]["tools"]
    assert len(tools) == 30
    response = next(item for item in rows if item.get("id") == 3)["result"]
    assert response["isError"] is False
    capabilities = response["structuredContent"]
    assert capabilities["supervisor"]["mode"] == "attached-supervisor"
    assert capabilities["supervisor"]["frontend_ephemeral"] is True
    assert capabilities["ready"]["runtime_generation"] == discovery.runtime_generation


def test_launcher_rejects_live_supervisor_from_another_package_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed = SimpleNamespace(supervisor_version="aar-supervisor/0.0.0")
    monkeypatch.setattr(codex_mcp, "_load_discovery", lambda _runtime_home: observed)

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="package version"):
        codex_mcp.ensure_codex_supervisor(tmp_path / "runtime")


def test_windows_stop_accepts_signalled_exact_process_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    discovery = SimpleNamespace(process_identity=SimpleNamespace())
    calls: list[str] = []
    monkeypatch.setattr(codex_mcp, "_read_discovery", lambda _runtime_home: discovery)
    monkeypatch.setattr(
        codex_mcp,
        "observe_process_identity",
        lambda _identity: ProcessIdentityObservation(state=ProcessIdentityState.MATCH),
    )
    monkeypatch.setattr(codex_mcp, "_write_shutdown_request", lambda *_args: None)
    monkeypatch.setattr(
        codex_mcp,
        "_terminate_windows_process",
        lambda _discovery, _timeout_sec: calls.append("terminated"),
    )
    monkeypatch.setattr(
        codex_mcp,
        "_remove_stale_private_files",
        lambda _runtime_home, _discovery: calls.append("cleaned"),
    )
    monkeypatch.setattr(codex_mcp.os, "name", "nt")

    assert stop_codex_supervisor(tmp_path / "runtime", timeout_sec=0)
    assert calls == ["terminated", "cleaned"]


def test_spawn_readiness_tolerates_transient_discovery_share_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def deny_read(_path: Path) -> bytes:
        raise PermissionError(13, "sharing violation")

    monkeypatch.setattr(Path, "read_bytes", deny_read)

    assert (
        codex_mcp._read_discovery(tmp_path, tolerate_transient_unreadable=True)
        is None
    )
    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="unreadable"):
        codex_mcp._read_discovery(tmp_path)


def test_codex_launcher_starts_then_reuses_one_supervisor(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    try:
        first_rows, first_stderr = _run_frontend(runtime_home, "codex-first")
        first = _read_discovery(runtime_home)
        _assert_capabilities(first_rows, first)
        assert first_stderr == ""

        second_rows, second_stderr = _run_frontend(runtime_home, "codex-second")
        second = _read_discovery(runtime_home)
        _assert_capabilities(second_rows, second)
        assert second_stderr == ""
        assert second.discovery_digest == first.discovery_digest
        assert second.process_identity == first.process_identity
    finally:
        _stop_test_supervisor(runtime_home)


def test_concurrent_codex_launchers_converge_on_one_supervisor(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(_run_frontend, runtime_home, f"codex-race-{index}")
                for index in range(2)
            ]
            results = [future.result() for future in futures]
        discovery = _read_discovery(runtime_home)
        for rows, stderr in results:
            _assert_capabilities(rows, discovery)
            assert stderr == ""
        lifecycle = (runtime_home / "supervisor" / "lifecycle.jsonl").read_text(
            encoding="utf-8"
        )
        assert lifecycle.count('"state":"ready"') == 1
    finally:
        _stop_test_supervisor(runtime_home)


def test_supervisor_ignores_shutdown_request_for_another_identity(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    try:
        _run_frontend(runtime_home, "codex-shutdown-fence")
        discovery = _read_discovery(runtime_home)
        request_path = runtime_home / "supervisor" / discovery.shutdown_request_file
        request_path.write_text(
            json.dumps(
                {
                    "discovery_digest": "sha256:" + "0" * 64,
                    "process_identity": discovery.process_identity.model_dump(mode="json"),
                    "schema_version": SHUTDOWN_REQUEST_SCHEMA_VERSION,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        time.sleep(0.15)
        assert request_path.exists()
        assert process_identity_matches(discovery.process_identity)
    finally:
        _stop_test_supervisor(runtime_home)


def test_launcher_identity_unavailable_before_spawn_does_not_spawn_or_remove_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    discovery = _discovery(runtime_home, _identity(901))
    discovery_path = _write_discovery(runtime_home, discovery)
    private = runtime_home / "supervisor"
    credential_path = private / discovery.credential_file
    endpoint_path = Path(discovery.endpoint_ref)
    request_path = private / discovery.shutdown_request_file
    credential_path.write_bytes(b"credential")
    endpoint_path.write_bytes(b"endpoint")
    request_path.write_bytes(b"request")
    spawned: list[str] = []

    def unavailable(_pid: int) -> ProcessStartIdentity:
        raise ProcessIdentityUnavailable("identity query temporarily unavailable")

    monkeypatch.setattr(process_identity_module, "process_identity", unavailable)

    def unexpected_spawn(*_args: object, **_kwargs: object) -> _FakeChild:
        spawned.append("spawn")
        raise AssertionError("identity UNAVAILABLE must not start a replacement")

    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", unexpected_spawn)

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="identity unavailable"):
        codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=0.1)

    assert spawned == []
    assert discovery_path.exists()
    assert credential_path.read_bytes() == b"credential"
    assert endpoint_path.read_bytes() == b"endpoint"
    assert request_path.read_bytes() == b"request"


def test_stop_identity_unavailable_does_not_request_signal_cleanup_or_succeed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    discovery = _discovery(runtime_home, _identity(902))
    _write_discovery(runtime_home, discovery)
    calls: list[str] = []

    def unavailable(_pid: int) -> ProcessStartIdentity:
        raise ProcessIdentityUnavailable("identity query temporarily unavailable")

    monkeypatch.setattr(process_identity_module, "process_identity", unavailable)
    monkeypatch.setattr(
        codex_mcp,
        "_write_shutdown_request",
        lambda *_args: calls.append("request"),
    )
    monkeypatch.setattr(
        codex_mcp,
        "_terminate_windows_process",
        lambda *_args: calls.append("signal"),
    )
    monkeypatch.setattr(
        codex_mcp,
        "_remove_stale_private_files",
        lambda *_args: calls.append("cleanup"),
    )

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="identity unavailable"):
        stop_codex_supervisor(runtime_home, timeout_sec=0.01)

    assert calls == []


def test_launcher_timeout_reaps_exact_spawned_child_before_startup_lock_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    events: list[str] = []
    child = _FakeChild(events)

    @contextlib.contextmanager
    def lock(_path: Path, _timeout_sec: float):
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(codex_mcp, "_load_discovery", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        codex_mcp,
        "_spawn_supervisor",
        lambda *_args, **_kwargs: child,
    )
    monkeypatch.setattr(codex_mcp.time, "sleep", lambda _seconds: None)

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="before timeout"):
        codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=0.001)

    assert "exact-signal" in events
    assert "exact-wait" in events
    assert events.index("exact-wait") < events.index("lock-exit")


def test_launcher_post_spawn_exception_reaps_exact_child_before_startup_lock_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    events: list[str] = []
    child = _FakeChild(events)
    load_count = 0

    @contextlib.contextmanager
    def lock(_path: Path, _timeout_sec: float):
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    def injected_failure(*_args: object, **_kwargs: object) -> None:
        nonlocal load_count
        load_count += 1
        if load_count < 3:
            return None
        raise RuntimeError("injected post-spawn discovery failure")

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(codex_mcp, "_load_discovery", injected_failure)
    monkeypatch.setattr(
        codex_mcp,
        "_spawn_supervisor",
        lambda *_args, **_kwargs: child,
    )

    with pytest.raises(Exception, match="injected post-spawn discovery failure"):
        codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=1)

    assert load_count >= 3
    assert "exact-signal" in events
    assert "exact-wait" in events
    assert events.index("exact-wait") < events.index("lock-exit")


def test_launcher_accepts_late_ready_record_for_exact_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    child = _FakeChild([], pid=903)
    discovery = _discovery(runtime_home, _identity(child.pid))
    loads: list[SupervisorDiscoveryRecord | None] = [None, None, discovery]
    events: list[str] = []

    @contextlib.contextmanager
    def lock(_path: Path, _timeout_sec: float):
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    clock = iter((0.0, 1.0))

    def load(*_args: object, **_kwargs: object) -> SupervisorDiscoveryRecord | None:
        return loads.pop(0)

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(codex_mcp, "_load_discovery", load)
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(codex_mcp.time, "monotonic", lambda: next(clock, 1.0))

    assert codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=0.5) == discovery
    assert events == ["lock-enter", "lock-exit"]
    assert child.returncode is None


def test_launcher_returns_foreign_ready_only_after_exact_child_is_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    events: list[str] = []
    child = _FakeChild(events, pid=904)
    foreign = _discovery(runtime_home, _identity(905, start_time=12, boot_id="boot-b"))
    loads: list[SupervisorDiscoveryRecord | None] = [None, None, foreign]

    @contextlib.contextmanager
    def lock(_path: Path, _timeout_sec: float):
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(
        codex_mcp,
        "_load_discovery",
        lambda *_args, **_kwargs: loads.pop(0),
    )
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_args, **_kwargs: child)

    result = codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=1)

    assert result == foreign
    assert "exact-signal" in events
    assert "exact-wait" in events
    assert events.index("exact-wait") < events.index("lock-exit")


def test_launcher_reaps_same_pid_foreign_identity_before_accepting_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    events: list[str] = []
    child = _FakeChild(events, pid=906, identity=_identity(906, start_time=11))
    replacement = _discovery(runtime_home, _identity(906, start_time=12))
    loads: list[SupervisorDiscoveryRecord | None] = [None, None, replacement]

    @contextlib.contextmanager
    def lock(_path: Path, _timeout_sec: float):
        events.append("lock-enter")
        try:
            yield
        finally:
            events.append("lock-exit")

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(
        codex_mcp,
        "_load_discovery",
        lambda *_args, **_kwargs: loads.pop(0),
    )
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_args, **_kwargs: child)

    assert codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=1) == replacement
    assert "exact-signal" in events
    assert "exact-wait" in events
    assert events.index("exact-wait") < events.index("lock-exit")


class _FakeCtypesFunction:
    def __init__(self, function: object) -> None:
        self._function = function
        self.argtypes: object = None
        self.restype: object = None

    def __call__(self, *args: object) -> object:
        return self._function(*args)


@pytest.mark.skipif(os.name != "nt", reason="Windows exact-handle termination contract")
@pytest.mark.parametrize(
    ("wait_result", "message"),
    [
        (0, None),
        (0x00000102, "denied and it remained live"),
        (0xDEAD, "could not terminate Codex supervisor"),
    ],
)
def test_windows_terminate_false_wait_branches_use_exact_handle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wait_result: int,
    message: str | None,
) -> None:
    identity = _windows_identity(906)
    discovery = _discovery(tmp_path, identity)
    calls: list[tuple[str, object]] = []

    class FakeKernel32:
        def __init__(self) -> None:
            self.OpenProcess = _FakeCtypesFunction(self.open_process)
            self.GetProcessTimes = _FakeCtypesFunction(self.get_process_times)
            self.TerminateProcess = _FakeCtypesFunction(self.terminate_process)
            self.WaitForSingleObject = _FakeCtypesFunction(self.wait_for_single_object)
            self.CloseHandle = _FakeCtypesFunction(self.close_handle)

        def open_process(self, access: object, inherit: object, pid: object) -> int:
            calls.append(("open", (access, inherit, pid)))
            return 0xABC

        def terminate_process(self, handle: object, code: object) -> bool:
            calls.append(("terminate", (handle, code)))
            return False

        def get_process_times(
            self,
            handle: object,
            creation: object,
            exit_time: object,
            kernel_time: object,
            user_time: object,
        ) -> bool:
            calls.append(("get-times", handle))
            creation._obj.dwLowDateTime = identity.start_time & 0xFFFFFFFF
            creation._obj.dwHighDateTime = identity.start_time >> 32
            return True

        def wait_for_single_object(self, handle: object, timeout_ms: object) -> int:
            calls.append(("wait", (handle, timeout_ms)))
            return wait_result

        def close_handle(self, handle: object) -> bool:
            calls.append(("close", handle))
            return True

    kernel32 = FakeKernel32()
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)

    if message is None:
        codex_mcp._terminate_windows_process(discovery, 0.25)
    else:
        with pytest.raises(codex_mcp.CodexMcpLauncherError, match=message):
            codex_mcp._terminate_windows_process(discovery, 0.25)

    assert calls[0][0] == "open"
    assert calls[1][0] == "get-times"
    assert calls[2][0] == "terminate"
    assert calls[3][0] == "wait"
    assert calls[-1] == ("close", 0xABC)
    assert calls[3][1][0] == 0xABC


def test_stop_wait_match_to_unavailable_refuses_cleanup_and_fallback_signal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    discovery = _discovery(runtime_home, _identity(980))
    _write_discovery(runtime_home, discovery)
    observations = iter(
        (
            ProcessIdentityObservation(state=ProcessIdentityState.MATCH),
            ProcessIdentityObservation(
                state=ProcessIdentityState.UNAVAILABLE,
                error=ProcessIdentityUnavailable("injected wait observation gap"),
            ),
        )
    )
    effects: list[str] = []
    monkeypatch.setattr(
        codex_mcp,
        "observe_process_identity",
        lambda _identity: next(observations),
    )
    monkeypatch.setattr(
        codex_mcp,
        "_remove_stale_private_files",
        lambda *_args: effects.append("cleanup"),
    )
    monkeypatch.setattr(
        codex_mcp,
        "open_exact_process",
        lambda *_args, **_kwargs: effects.append("open") or None,
    )
    monkeypatch.setattr(
        codex_mcp,
        "_terminate_windows_process",
        lambda *_args, **_kwargs: effects.append("signal"),
    )

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="unavailable while stopping"):
        stop_codex_supervisor(runtime_home, timeout_sec=0.1)

    request_path = runtime_home / "supervisor" / discovery.shutdown_request_file
    assert request_path.read_bytes() == canonical_json_bytes(
        {
            "discovery_digest": discovery.discovery_digest,
            "process_identity": discovery.process_identity.model_dump(mode="json"),
            "schema_version": SHUTDOWN_REQUEST_SCHEMA_VERSION,
        }
    )
    assert effects == []


class _PidReuseSpawnedChild(_FakeChild):
    """A reaped child whose numeric PID now resolves to a foreign replacement."""

    def __init__(self, events: list[str], *, pid: int) -> None:
        super().__init__(events, pid=pid)
        self.replacement_signal_count = 0
        self.numeric_pid_reopen_count = 0

    def terminate(self) -> None:
        self.events.append("unsafe-popen-terminate")
        self.replacement_signal_count += 1
        self.returncode = -15

    def kill(self) -> None:
        self.events.append("unsafe-popen-kill")
        self.replacement_signal_count += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.events.append("popen-wait")
        if self.returncode is None:
            self.returncode = -15
        return self.returncode


def _assert_spawned_child_not_signalled_by_pid(
    child: _PidReuseSpawnedChild, seam: str
) -> None:
    assert child.replacement_signal_count == 0, (
        f"{seam} signalled the PID-reuse replacement through Popen instead of a retained "
        "exact child handle"
    )
    assert child.numeric_pid_reopen_count == 0
    assert child.exact_signal_count == 1, f"{seam} did not signal the retained handle"
    assert child.exact_wait_count == 1, f"{seam} did not wait through the retained handle"
    assert child.terminal_receipt_count == 1, f"{seam} did not publish one terminal result"


def test_startup_timeout_reap_uses_exact_child_handle_across_pid_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    child = _PidReuseSpawnedChild([], pid=1_020)

    @contextlib.contextmanager
    def lock(_path: Path, _timeout: float):
        yield

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(codex_mcp, "_load_discovery", lambda *_a, **_k: None)
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_a, **_k: child)
    monkeypatch.setattr(codex_mcp.time, "sleep", lambda _seconds: None)

    with pytest.raises(codex_mcp.CodexMcpLauncherError, match="before timeout"):
        codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=0.001)

    _assert_spawned_child_not_signalled_by_pid(child, "startup timeout")


def test_post_spawn_exception_reap_uses_exact_child_handle_across_pid_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    child = _PidReuseSpawnedChild([], pid=1_021)
    reads = 0

    @contextlib.contextmanager
    def lock(_path: Path, _timeout: float):
        yield

    def load(*_args, **_kwargs):
        nonlocal reads
        reads += 1
        if reads >= 3:
            raise RuntimeError("injected post-spawn observation failure")
        return None

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(codex_mcp, "_load_discovery", load)
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_a, **_k: child)

    with pytest.raises(RuntimeError, match="post-spawn observation failure"):
        codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=1)

    _assert_spawned_child_not_signalled_by_pid(child, "post-spawn exception")


def test_foreign_ready_reap_uses_exact_contender_handle_across_pid_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime_home = tmp_path / "runtime"
    child = _PidReuseSpawnedChild([], pid=1_022)
    foreign = _discovery(runtime_home, _identity(1_023, start_time=20, boot_id="boot-z"))
    observations: list[SupervisorDiscoveryRecord | None] = [None, None, foreign]

    @contextlib.contextmanager
    def lock(_path: Path, _timeout: float):
        yield

    monkeypatch.setattr(codex_mcp, "_startup_lock", lock)
    monkeypatch.setattr(
        codex_mcp,
        "_load_discovery",
        lambda *_a, **_k: observations.pop(0),
    )
    monkeypatch.setattr(codex_mcp, "_spawn_supervisor", lambda *_a, **_k: child)

    assert codex_mcp.ensure_codex_supervisor(runtime_home, startup_timeout_sec=1) == foreign
    _assert_spawned_child_not_signalled_by_pid(child, "foreign Ready contender reap")
