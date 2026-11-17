"""Known pre-ticket capacity failure and uncertain authority are different states."""

from __future__ import annotations

import json
import sqlite3
from copy import deepcopy

from pydantic import ValidationError
from test_lifecycle_consumer import exact_executed_package  # noqa: F401
from test_rlm_workbench import (
    FIXED_NOW_MS,
    prepare_v6_registry,
    workbench_envelope,
    workbench_request,
)

import aar.runtime.rlm_workbench as workbench
from aar.canonical import canonical_json_bytes
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import OperationState, OutcomeCertainty


def _admitted_case(tmp_path, *, large=True):
    database = tmp_path / "registry.sqlite"
    prepare_v6_registry(database, tmp_path / "snapshot.sqlite")
    host = ReferenceHost(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        enable_durable_dispatch=False,
        dispatcher_concurrency=1,
    )
    try:
        original = workbench_request(
            host,
            require_named_artifacts=False,
            execution_mode="caller_delegated",
            max_model_calls=4,
        )
        document = deepcopy(original.root)
        if large:
            # Exactly the admitted maximum, NOT an invalid overlong job spec.
            document["spec"]["objective"] = "x" * 65_536
        request = type(original).model_validate(document, strict=True)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        return database, host, record
    except BaseException:
        host.close()
        raise


def test_admitted_prompt_capacity_failure_is_certain_not_unknown(tmp_path):
    database, host, record = _admitted_case(tmp_path)
    try:
        terminal = host.start_durable_dispatch().wait(record.operation, timeout_s=10)
        assert host.rlm_workbench is not None
        snapshot = host.rlm_workbench.snapshot(record.operation).root
        with sqlite3.connect(database) as connection:
            counts = {
                name: connection.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                for name in (
                    "caller_work_tickets",
                    "rlm_workbench_cells",
                    "rlm_workbench_finalization_manifests",
                )
            }
        observed = {
            "operation_state": terminal.state.value,
            "certainty": terminal.certainty.value,
            "native_phase": snapshot["phase"],
            "table_counts": counts,
        }
        assert terminal.state is OperationState.FAILED, json.dumps(observed)
        assert terminal.certainty is OutcomeCertainty.CERTAIN, json.dumps(observed)
        assert snapshot["phase"] == "failed", json.dumps(observed)
        assert all(count == 0 for count in counts.values())
        assert terminal.failure is not None
        assert terminal.failure.code == "BUDGET_EXCEEDED"
        assert any(
            item.name == "budget_kind" and item.value == "planner_prompt_characters"
            for item in terminal.failure.details
        )
        assert "x" * 100 not in terminal.failure.model_dump_json()
    finally:
        host.close()


def test_unexpected_validation_failure_still_parks_instead_of_fabricating_certainty(
    tmp_path, monkeypatch
):
    _, host, record = _admitted_case(tmp_path, large=False)
    unexpected = ValidationError.from_exception_data(
        "controlled-unexpected",
        [
            {
                "type": "value_error",
                "loc": ("controlled",),
                "input": None,
                "ctx": {"error": ValueError("controlled unexpected validation failure")},
            }
        ],
    )

    def reject_unexpected(*_args, **_kwargs):
        raise unexpected

    monkeypatch.setattr(workbench, "_planner_request", reject_unexpected)
    try:
        terminal = host.start_durable_dispatch().wait(record.operation, timeout_s=10)
        assert terminal.state is OperationState.INDETERMINATE
        assert terminal.certainty is OutcomeCertainty.INDETERMINATE
        assert terminal.failure is None
        assert host.rlm_workbench is not None
        assert host.rlm_workbench.snapshot(record.operation).root["phase"] == "parked"
    finally:
        host.close()


def test_capacity_inner_failure_rolls_back_before_uncertain_outer_projection(tmp_path, monkeypatch):
    database, host, record = _admitted_case(tmp_path)
    original_transition = host.registry.transition_claimed
    reached = []

    def fail_after_inner(*args, **kwargs):
        if kwargs.get("note") == "workbench_planner_context_too_large":
            original_inner = kwargs["inner_transition"]

            def interrupted(connection):
                original_inner(connection)
                reached.append("inner_completed_before_controlled_fault")
                raise RuntimeError("controlled failure after inner write before outer commit")

            kwargs["inner_transition"] = interrupted
        return original_transition(*args, **kwargs)

    monkeypatch.setattr(host.registry, "transition_claimed", fail_after_inner)
    try:
        terminal = host.start_durable_dispatch().wait(record.operation, timeout_s=10)
        assert reached == ["inner_completed_before_controlled_fault"]
        assert terminal.state is OperationState.INDETERMINATE
        assert terminal.failure is None
        assert host.rlm_workbench is not None
        assert host.rlm_workbench.snapshot(record.operation).root["phase"] == "parked"
        with sqlite3.connect(database) as connection:
            persisted = json.loads(
                connection.execute("SELECT failure_json FROM rlm_workbench_jobs").fetchone()[0]
            )
            assert persisted["code"] == "RECONCILIATION_REQUIRED"
            assert persisted["certainty"] == "indeterminate"
            assert not any(
                item["name"] == "budget_kind" and item["value"] == "planner_prompt_characters"
                for item in persisted["details"]
            )
    finally:
        host.close()


def test_prompt_character_boundary_accepts_exact_capacity_and_rejects_one_more():
    empty = {"bounded": ""}
    overhead = len(canonical_json_bytes(empty).decode())
    at_limit = {"bounded": "x" * (65_536 - overhead)}
    assert len(workbench._planner_prompt(at_limit)) == 65_536
    beyond = {"bounded": at_limit["bounded"] + "x"}
    try:
        workbench._planner_prompt(beyond)
    except workbench.RlmWorkbenchPromptCapacityExceeded as error:
        assert error.observed_characters == 65_537
        assert error.maximum_characters == 65_536
    else:
        raise AssertionError("one extra character must fail before creating any ticket")
