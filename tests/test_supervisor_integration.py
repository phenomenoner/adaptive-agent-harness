from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from aar.compat.codex_mcp import stop_codex_supervisor
from aar.runtime.process_identity import (
    ProcessStartIdentity,
    SupervisorDiscoveryRecord,
    open_exact_process,
    process_identity_matches,
)
from aar.runtime.python_child import exact_module_command
from aar.runtime.registry import OperationRegistry
from aar.runtime.supervisor_protocol import PrivateFrame, SupervisorLifecycleReceipt

ROOT = Path(__file__).resolve().parents[1]


def _wait_discovery(
    runtime_home: Path, process: subprocess.Popen[str]
) -> SupervisorDiscoveryRecord:
    path = runtime_home / "supervisor" / "discovery.json"
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            _stdout, stderr = process.communicate(timeout=1)
            raise AssertionError(f"supervisor exited before Ready: {stderr}")
        if path.exists():
            try:
                record = SupervisorDiscoveryRecord.model_validate_json(
                    path.read_bytes(), strict=True
                )
                if process_identity_matches(record.process_identity):
                    return record
            except ValueError:
                pass
        time.sleep(0.025)
    raise AssertionError("supervisor discovery timeout")


def _start_supervisor(
    runtime_home: Path, *, backend: str = "plain"
) -> tuple[subprocess.Popen[str], SupervisorDiscoveryRecord]:
    process = subprocess.Popen(
        exact_module_command(
            "aar.runtime.supervisor",
            [
            "--runtime-home",
            str(runtime_home),
            "--programmable-backend",
            backend,
            ],
            isolated=False,
        ),
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return process, _wait_discovery(runtime_home, process)


def _stop_supervisor(process: subprocess.Popen[str], runtime_home: Path) -> None:
    if process.poll() is None:
        assert stop_codex_supervisor(runtime_home)
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def _frontend_exchange(runtime_home: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    process = subprocess.run(
        [
            sys.executable,
            "-m",
            "aar.mcp.server",
            "--runtime-home",
            str(runtime_home),
        ],
        cwd=ROOT,
        input="".join(json.dumps(item, separators=(",", ":")) + "\n" for item in messages),
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert process.returncode == 0, process.stderr
    return [json.loads(line) for line in process.stdout.splitlines()]


def _tool_call(
    runtime_home: Path,
    *,
    name: str,
    arguments: dict[str, Any],
    request_id: int = 2,
) -> dict[str, Any]:
    rows = _frontend_exchange(
        runtime_home,
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "lt2-test", "version": "1"},
                },
            },
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        ],
    )
    response = next(item for item in rows if item.get("id") == request_id)
    assert response["result"]["isError"] is False
    return response["result"]["structuredContent"]


def _lifecycle_receipts(runtime_home: Path) -> list[SupervisorLifecycleReceipt]:
    path = runtime_home / "supervisor" / "lifecycle.jsonl"
    return [
        SupervisorLifecycleReceipt.model_validate_json(line, strict=True)
        for line in path.read_bytes().splitlines()
    ]


def test_frontends_reconnect_without_owning_runtime_and_cleanup_is_exact(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    process, discovery = _start_supervisor(runtime_home)
    private = runtime_home / "supervisor"
    try:
        assert process_identity_matches(discovery.process_identity)
        if os.name != "nt":
            assert stat.S_IMODE(private.stat().st_mode) == 0o700
            assert stat.S_IMODE((private / "attachment.key").stat().st_mode) == 0o600
            assert stat.S_IMODE(Path(discovery.endpoint_ref).stat().st_mode) == 0o600

        rows = _frontend_exchange(
            runtime_home,
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "first", "version": "1"},
                    },
                },
                {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            ],
        )
        tools = next(item for item in rows if item.get("id") == 2)["result"]["tools"]
        assert len(tools) == 30
        assert process.poll() is None

        capabilities = _tool_call(runtime_home, name="aar_capabilities", arguments={})
        projection = capabilities["supervisor"]
        assert projection["mode"] == "attached-supervisor"
        assert projection["frontend_ephemeral"] is True
        assert projection["dispatcher_generation"] == discovery.dispatcher_generation
        assert projection["protocol_digest"].startswith("sha256:")
        assert projection["process_identity_digest"].startswith("sha256:")
        assert process.poll() is None

        if discovery.endpoint_kind == "unix":
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as bad_client:
                bad_client.settimeout(5)
                bad_client.connect(discovery.endpoint_ref)
                bad_client.sendall(b"AUTH invalid-credential\n")
                error = PrivateFrame.model_validate_json(
                    bad_client.makefile("rb").readline().rstrip(b"\n"), strict=True
                )
                assert error.kind == "error"
        assert _tool_call(runtime_home, name="aar_capabilities", arguments={})[
            "ready"
        ]["runtime_generation"] == discovery.runtime_generation

        with ThreadPoolExecutor(max_workers=6) as executor:
            concurrent_results = tuple(
                executor.map(
                    lambda _index: _tool_call(
                        runtime_home, name="aar_capabilities", arguments={}
                    ),
                    range(6),
                )
            )
        assert {
            item["ready"]["runtime_generation"] for item in concurrent_results
        } == {discovery.runtime_generation}

        contender = subprocess.run(
            exact_module_command(
                "aar.runtime.supervisor",
                [
                "--runtime-home",
                str(runtime_home),
                "--programmable-backend",
                "plain",
                ],
                isolated=False,
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert contender.returncode == 1
        assert "live exact process" in contender.stderr
        observed = SupervisorDiscoveryRecord.model_validate_json(
            (private / "discovery.json").read_bytes(), strict=True
        )
        assert observed.discovery_digest == discovery.discovery_digest
    finally:
        _stop_supervisor(process, runtime_home)

    assert not (private / "discovery.json").exists()
    assert not (private / "attachment.key").exists()
    if discovery.endpoint_kind == "unix":
        assert not Path(discovery.endpoint_ref).exists()
    receipts = [
        item
        for item in _lifecycle_receipts(runtime_home)
        if item.runtime_generation == discovery.runtime_generation
    ]
    assert [item.state for item in receipts] == [
        "starting",
        "migrating",
        "recovering",
        "ready",
        "draining",
        "stopped",
    ]
    registry = OperationRegistry(
        runtime_home / "reference.sqlite3", lambda: int(time.time() * 1000)
    )
    try:
        assert registry.supervisor_runs()[-1].state == "stopped"
    finally:
        registry.close()


def test_hard_owner_loss_reconciles_predecessor_and_worker_binding(tmp_path: Path) -> None:
    runtime_home = tmp_path / "runtime"
    first, first_discovery = _start_supervisor(runtime_home, backend="ipython")
    successor: subprocess.Popen[str] | None = None
    worker_identity: ProcessStartIdentity | None = None
    try:
        reference = _tool_call(
            runtime_home,
            name="aar_reference_context",
            arguments={
                "capability": "workspace.program.create",
                "context_key": "lt2-worker-create",
                "budget_wall_time_ms": 60_000,
            },
        )
        created = _tool_call(
            runtime_home,
            name="aar_program_workspace_create",
            arguments={
                "context": reference["context"],
                "workspace_id": "lt2-worker",
            },
        )
        assert created["handle"]["workspace"]["value"] == "lt2-worker"

        with sqlite3.connect(runtime_home / "reference.sqlite3") as connection:
            row = connection.execute(
                """
                SELECT process_start_identity, state
                FROM worker_bindings
                WHERE runtime_generation = ?
                """,
                (first_discovery.runtime_generation,),
            ).fetchone()
        assert row is not None
        worker_identity = ProcessStartIdentity.model_validate_json(str(row[0]), strict=True)
        assert row[1] == "ready"
        assert process_identity_matches(worker_identity)

        first.kill()
        first.wait(timeout=15)
        assert (runtime_home / "supervisor" / "discovery.json").exists()

        successor, successor_discovery = _start_supervisor(runtime_home, backend="ipython")
        assert successor_discovery.runtime_generation == first_discovery.runtime_generation + 1
        assert successor_discovery.process_identity != first_discovery.process_identity
        with sqlite3.connect(runtime_home / "reference.sqlite3") as connection:
            prior_run = connection.execute(
                "SELECT state, terminal_reason FROM supervisor_runs WHERE runtime_generation = ?",
                (first_discovery.runtime_generation,),
            ).fetchone()
            worker = connection.execute(
                """
                SELECT state, termination_receipt_json
                FROM worker_bindings
                WHERE runtime_generation = ?
                """,
                (first_discovery.runtime_generation,),
            ).fetchone()
        assert prior_run == (
            "reconcile-required",
            "predecessor_process_absent_on_startup",
        )
        assert worker is not None
        assert worker[0] in {"lost", "quarantined", "terminated"}
        assert worker[1]
        assert not process_identity_matches(worker_identity)
        assert _tool_call(runtime_home, name="aar_capabilities", arguments={})[
            "ready"
        ]["runtime_generation"] == successor_discovery.runtime_generation
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)
        if successor is not None:
            _stop_supervisor(successor, runtime_home)
        if worker_identity is not None and process_identity_matches(worker_identity):
            with open_exact_process(worker_identity, terminate=True) as target:
                target.send_signal(signal.SIGTERM)
                target.wait(5)
            pytest.fail("test-owned predecessor worker survived successor recovery")


def test_durable_rlm_survives_frontend_exit_and_reconnects_by_operation_id(
    tmp_path: Path,
) -> None:
    runtime_home = tmp_path / "runtime"
    supervisor, _discovery = _start_supervisor(runtime_home, backend="plain")
    try:
        capabilities = _tool_call(runtime_home, name="aar_capabilities", arguments={})
        deadline = capabilities["server_now_unix_ms"] + 60_000
        grants = {
            item["capability"]: item["grant_id"]
            for item in capabilities["reference_grants"]
        }
        read_context = {
            "runtime_generation": capabilities["ready"]["runtime_generation"],
            "capability_digest": capabilities["ready"]["capabilities"]["digest"],
            "principal_id": "lt2-rlm-principal",
            "session_id": "lt2-rlm-session",
            "deadline_unix_ms": deadline,
        }
        mutation_context = {
            **read_context,
            "request_id": "request-lt2-frontend-exit",
            "idempotency_key": "idempotency-lt2-frontend-exit",
            "grant_ids": sorted((grants["model.request"], grants["rlm.execute"])),
            "budget_wall_time_ms": 60_000,
            "budget_model_requests": 2,
            "budget_input_tokens": 1_024,
            "budget_output_tokens": 128,
            "budget_child_operations": 0,
            "budget_artifact_bytes": 0,
        }
        accepted = _tool_call(
            runtime_home,
            name="aar_rlm_execute",
            arguments={
                "context": mutation_context,
                "query": "Return one concise durable supervisor result.",
                "strategy": "baseline",
                "max_steps": 2,
                "start_only": True,
            },
        )
        assert accepted["state"] == "accepted"
        operation_id = accepted["operation"]["value"]
        assert supervisor.poll() is None

        terminal: dict[str, Any] | None = None
        for _ in range(80):
            observed = _tool_call(
                runtime_home,
                name="aar_rlm_status",
                arguments={"context": read_context, "operation_id": operation_id},
            )
            if observed["operation"] is not None and observed["operation"]["state"] in {
                "succeeded",
                "failed",
                "cancelled",
            }:
                terminal = observed
                break
            time.sleep(0.025)
        assert terminal is not None
        assert terminal["operation"]["state"] == "succeeded"
        assert terminal["operation"]["operation"]["value"] == operation_id
        assert terminal["snapshot"]["operation"]["value"] == operation_id
        assert terminal["snapshot"]["state"] == "succeeded"
        assert terminal["snapshot"]["result"]
        assert supervisor.poll() is None
    finally:
        _stop_supervisor(supervisor, runtime_home)
