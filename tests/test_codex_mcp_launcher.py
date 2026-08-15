from __future__ import annotations

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

from aar.compat import codex_mcp
from aar.compat.codex_mcp import (
    SHUTDOWN_REQUEST_FILE_NAME,
    SHUTDOWN_REQUEST_SCHEMA_VERSION,
    stop_codex_supervisor,
)
from aar.runtime.process_identity import (
    SupervisorDiscoveryRecord,
    process_identity_matches,
)

ROOT = Path(__file__).resolve().parents[1]


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
    assert not path.exists()


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
    monkeypatch.setattr(codex_mcp, "process_identity_matches", lambda _identity: True)
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
        request_path = runtime_home / "supervisor" / SHUTDOWN_REQUEST_FILE_NAME
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
        deadline = time.monotonic() + 5
        while request_path.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert not request_path.exists()
        assert process_identity_matches(discovery.process_identity)
    finally:
        _stop_test_supervisor(runtime_home)
