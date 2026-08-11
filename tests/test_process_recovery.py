from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from aar.continuity_models import OperationAttemptRefV1
from aar.runtime.registry import OperationRegistry, StaleAttemptFence
from aar.schemas import OperationRef

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "tests" / "process_probe.py"


def run_probe(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(PROBE), *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )


def test_fresh_process_reconciles_committed_workspace_receipt(tmp_path: Path) -> None:
    database = tmp_path / "committed.sqlite3"
    first = run_probe("commit-uncertain", str(database))
    assert first.returncode == 0, first.stderr
    accepted = json.loads(first.stdout)

    second = run_probe("reconcile", str(database), accepted["operation"])
    assert second.returncode == 0, second.stderr
    report = json.loads(second.stdout)
    assert report["state"] == "succeeded"
    assert report["certainty"] == "certain"
    assert report["observed_revision"] == 1
    assert report["runtime_generation"] == accepted["runtime_generation"] + 1


def test_fresh_process_classifies_abrupt_running_crash_without_receipt(
    tmp_path: Path,
) -> None:
    database = tmp_path / "crashed.sqlite3"
    first = run_probe("leave-running", str(database))
    assert first.returncode == 23
    operation = first.stdout.strip()
    assert operation.startswith("op-")

    second = run_probe("reconcile", str(database), operation)
    assert second.returncode == 0, second.stderr
    report = json.loads(second.stdout)
    assert report["state"] == "failed"
    assert report["certainty"] == "certain"
    assert report["reconciliation_required"] is False


@pytest.mark.parametrize(
    ("action", "exit_code", "expected_state", "expected_steps"),
    (
        ("rlm-loss-before-broker", 24, "indeterminate", 0),
        ("rlm-loss-after-terminal", 25, "succeeded", 1),
    ),
)
def test_fresh_process_reconciles_rlm_durable_boundaries(
    tmp_path: Path,
    action: str,
    exit_code: int,
    expected_state: str,
    expected_steps: int,
) -> None:
    database = tmp_path / f"{action}.sqlite3"
    first = run_probe(action, str(database))
    assert first.returncode == exit_code, first.stderr
    operation = first.stdout.strip()
    assert operation.startswith("op-")

    second = run_probe("rlm-reconcile", str(database), operation)
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)
    assert recovered["report"]["state"] == expected_state
    assert recovered["snapshot"]["state"] == expected_state
    assert len(recovered["snapshot"]["steps"]) == expected_steps
    if expected_state == "indeterminate":
        assert recovered["report"]["reconciliation_required"] is True
        assert recovered["snapshot"]["result"] is None
    else:
        assert recovered["report"]["reconciliation_required"] is False
        assert recovered["snapshot"]["result"] is not None


def test_fresh_process_finishes_outer_authoritative_rlm_cancellation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "rlm-cancel.sqlite3"
    first = run_probe("rlm-cancel-loss-after-outer", str(database))
    assert first.returncode == 26, first.stderr
    operation = first.stdout.strip()

    second = run_probe("rlm-reconcile", str(database), operation)
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)
    assert recovered["report"]["state"] == "cancelled"
    assert recovered["snapshot"]["state"] == "cancelled"
    assert recovered["snapshot"]["result"] is None
    assert recovered["snapshot"]["steps"] == []


def test_fresh_process_reconciles_atomic_asset_import_without_duplicate_or_serving_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "asset-import.sqlite3"
    first = run_probe("asset-loss-after-import", str(database))
    assert first.returncode == 27, first.stderr
    operation = first.stdout.strip()

    second = run_probe("asset-reconcile", str(database), operation)
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)
    assert recovered["report"]["state"] == "succeeded"
    assert recovered["counts"] == [1, 0]
    assert recovered["result"]["asset_count"] == 1
    assert recovered["result"]["active_serving_mutated"] is False


@pytest.mark.parametrize(
    ("action", "exit_code", "expected_state", "expected_attempt", "expected_decisions"),
    (
        ("durable-claim-crash", 28, "succeeded", 2, ["start_successor"]),
        ("durable-receipt-crash", 29, "succeeded", 2, ["start_successor"]),
        ("durable-unresolved-crash", 30, "indeterminate", 1, ["reconcile_effect"]),
    ),
)
def test_fresh_process_recovers_durable_dispatch_attempts_without_changing_logical_id(
    tmp_path: Path,
    action: str,
    exit_code: int,
    expected_state: str,
    expected_attempt: int,
    expected_decisions: list[str],
) -> None:
    database = tmp_path / f"{action}.sqlite3"
    first = run_probe(action, str(database))
    assert first.returncode == exit_code, first.stderr
    crashed = json.loads(first.stdout)

    second = run_probe("durable-recover", str(database), crashed["operation"])
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)

    assert recovered["operation"] == crashed["operation"]
    assert recovered["runtime_generation"] == crashed["runtime_generation"] + 1
    assert recovered["record"]["state"] == expected_state
    assert recovered["continuity"]["last_attempt"]["attempt_no"] == expected_attempt
    assert [item["decision"] for item in recovered["decisions"]] == expected_decisions
    assert recovered["event_sequences"] == sorted(set(recovered["event_sequences"]))

    if action == "durable-unresolved-crash":
        assert recovered["record"]["certainty"] == "indeterminate"
        assert recovered["record"]["reconciliation_required"] is True
        assert recovered["broker_trace_count"] == 1
        assert recovered["terminal_snapshot"] is None
    else:
        assert recovered["record"]["certainty"] == "certain"
        assert recovered["record"]["reconciliation_required"] is False
        assert recovered["broker_trace_count"] == 1
        assert recovered["terminal_snapshot"]["operation_state"] == "succeeded"

    registry = OperationRegistry(database, lambda: 1_700_000_000_000)
    try:
        old_attempt = OperationAttemptRefV1(
            operation=OperationRef(value=crashed["operation"]),
            attempt_no=crashed["attempt_no"],
            attempt_id=crashed["attempt_id"],
        )
        with pytest.raises(StaleAttemptFence):
            registry.heartbeat(
                old_attempt,
                crashed["runtime_generation"],
                crashed["dispatcher_generation"],
                crashed["lease_epoch"],
                crashed["owner_digest"],
                30_000,
            )
    finally:
        registry.close()


def test_fresh_process_preserves_durable_queued_cancellation_and_events(tmp_path: Path) -> None:
    database = tmp_path / "durable-cancel-crash.sqlite3"
    first = run_probe("durable-cancel-crash", str(database))
    assert first.returncode == 31, first.stderr
    operation = first.stdout.strip()

    second = run_probe("durable-recover", str(database), operation)
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)

    assert recovered["operation"] == operation
    assert recovered["record"] == {
        "certainty": "certain",
        "reconciliation_required": False,
        "state": "cancelled",
    }
    assert recovered["continuity"]["control"]["cancellation_requested"] is True
    assert recovered["continuity"]["last_attempt"] is None
    assert "cancel_requested" in recovered["event_kinds"]
    assert "cancelled_before_claim" in recovered["event_kinds"]
    assert recovered["terminal_snapshot"]["operation_state"] == "cancelled"
