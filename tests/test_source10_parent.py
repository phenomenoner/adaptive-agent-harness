"""Black-box completion integration checks with scripted model tickets.

Exercise public helper behavior with controlled fixtures and real local workers;
no provider or contender-model calls are made.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    prepare_v6_registry,
    workbench_envelope,
    workbench_request,
)
from test_workbench_cell_feedback import CallerCase

import aar.runtime.ipython_backend as backend
from aar.canonical import canonical_sha256
from aar.rlm_workbench_models import RlmWorkbenchExecuteInput, RlmWorkbenchResult
from aar.runtime.artifact_publication import (
    ArtifactPublicationConflict,
    ArtifactPublicationRepository,
)
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import RegistryError
from aar.runtime.rlm_workbench import RlmWorkbenchError

OUTPUT = {"answer": "controlled-parent-result", "artifacts": []}


@pytest.fixture(autouse=True)
def candidate_worker_bootstrap(monkeypatch):
    """Test-only exact source projection; -I intentionally ignores PYTHONPATH."""
    monkeypatch.setattr(backend, "exact_module_command", candidate_worker_command)


def candidate_worker_command(module, arguments=(), *, isolated):
    source = str(Path(backend.__file__).resolve().parents[2])
    assert module == "aar.runtime.ipython_worker" and isolated
    bootstrap = (
        f"import sys,runpy,importlib.util;sys.path.insert(0,{source!r});"
        f"assert importlib.util.find_spec({module!r}).origin.startswith({source!r});"
        f"runpy.run_module({module!r},run_name='__main__')"
    )
    return [sys.executable, "-I", "-B", "-c", bootstrap, *arguments]


def make_case(
    path: Path,
    *,
    max_calls: int = 4,
    max_cells: int = 4,
    require_named: bool = False,
    cell_wall_time_ms: int | None = None,
    artifact_bytes: int | None = None,
) -> CallerCase:
    path.mkdir(parents=True, exist_ok=True)
    case = CallerCase.__new__(CallerCase)  # reuse fixture operations, not product authority
    case.database = path / "registry.sqlite"
    prepare_v6_registry(case.database, path / "snapshot.sqlite")
    case.host = ReferenceHost(
        case.database,
        now_ms=lambda: FIXED_NOW_MS,
        enable_durable_dispatch=False,
        dispatcher_concurrency=1,
    )
    req = workbench_request(
        case.host,
        require_named_artifacts=require_named,
        execution_mode="caller_delegated",
        max_model_calls=max_calls,
    )
    raw = req.model_dump(mode="json")
    raw["spec"]["budgets"]["max_cells"] = max_cells
    raw["spec"]["budgets"]["max_artifact_count"] = 5
    if cell_wall_time_ms is not None:
        raw["spec"]["budgets"]["cell_wall_time_ms"] = cell_wall_time_ms
    if artifact_bytes is not None:
        raw["spec"]["budgets"]["max_artifact_bytes"] = artifact_bytes
    case.request = RlmWorkbenchExecuteInput.model_validate(raw, strict=True)
    case.record = case.host.submit_rlm_workbench(
        workbench_envelope(case.host, case.request), case.request
    )
    case.turn = 0
    return case


def execute(code: str) -> dict:
    return {"kind": "execute_cell", "code": code, "expected_result_hint": None}


def counts(case: CallerCase) -> dict[str, int]:
    with sqlite3.connect(case.database) as db:
        return {
            table: db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE operation_id=?", (case.record.operation.value,)
            ).fetchone()[0]
            for table in (
                "rlm_workbench_finalization_manifests",
                "rlm_workbench_cells",
                "rlm_workbench_artifact_stages",
                "caller_work_tickets",
            )
        }


def legacy_complete(case: CallerCase) -> RlmWorkbenchResult:
    assert case.run().phase == "waiting_external"
    assert (
        case.send(execute("aar_artifact('reports/legacy.txt', b'legacy', 'text/plain')")).phase
        == "waiting_external"
    )
    prompt = json.loads(case.pending().root["request"]["prompt"])
    result = case.send(
        {
            "kind": "finalize",
            "output": OUTPUT,
            "artifact_stage_ids": prompt["last_committed_cell"]["artifact_stage_ids"],
        }
    )
    assert isinstance(result, RlmWorkbenchResult)
    return result


def admit_second_same_session(first: CallerCase) -> CallerCase:
    second = CallerCase.__new__(CallerCase)
    second.database, second.host = first.database, first.host
    raw = first.request.model_dump(mode="json")
    raw["context"]["request_id"] += "-second"
    raw["context"]["idempotency_key"] += "-second"
    second.request = RlmWorkbenchExecuteInput.model_validate(raw, strict=True)
    second.record = second.host.submit_rlm_workbench(
        workbench_envelope(second.host, second.request), second.request
    )
    second.turn = 0

    def pending_for(case):
        with sqlite3.connect(case.database) as db:
            ticket = db.execute(
                "SELECT ticket_id FROM caller_work_tickets WHERE operation_id=? "
                "AND state='pending'",
                (case.record.operation.value,),
            ).fetchone()
        assert ticket is not None
        return case.host.caller_work.get(ticket[0])

    first.pending = lambda: pending_for(first)
    second.pending = lambda: pending_for(second)
    return second


def test_source10_parent_one_cell_five_artifacts_no_extra_model_call(tmp_path: Path):
    case = make_case(tmp_path, max_calls=1, max_cells=1, require_named=True)
    try:
        assert case.run().phase == "waiting_external"
        artifacts = {f"reports/part-{i}.txt": (f"controlled-{i}", "text/plain") for i in range(5)}
        result = case.send(execute(f"aar_complete({OUTPUT!r}, {artifacts!r})\nx = 42"))
        assert isinstance(result, RlmWorkbenchResult), (
            "successful helper must not ask another planner"
        )
        assert result.root["output"] == OUTPUT
        assert result.model_calls == 1 and result.python_cells == 1
        assert len(result.artifact_bindings) == 5
        assert counts(case) == {
            "rlm_workbench_finalization_manifests": 1,
            "rlm_workbench_cells": 1,
            "rlm_workbench_artifact_stages": 5,
            "caller_work_tickets": 1,
        }
    finally:
        case.host.close()


def test_source10_parent_intent_then_error_never_publishes_or_leaks_next_cell(tmp_path: Path):
    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        result = case.send(
            execute(f"aar_complete({OUTPUT!r})\nraise RuntimeError('controlled after-intent')")
        )
        assert result.phase == "waiting_external"
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
        with sqlite3.connect(case.database) as db:
            raw = db.execute(
                "SELECT result_json FROM rlm_workbench_cells WHERE operation_id=?",
                (case.record.operation.value,),
            ).fetchone()[0]
            assert json.loads(raw)["status"] == "failed"
        after = case.send(execute("ordinary_value = 42\nordinary_value"))
        assert after.phase == "waiting_external"
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
    finally:
        case.host.close()


def test_source10_parent_ordinary_intent_shaped_data_is_not_helper_request(tmp_path: Path):
    case = make_case(tmp_path)
    marker = {
        "schema_version": "aar.workbench-completion-intent.v1",
        "output": OUTPUT,
        "artifact_logical_names": [],
    }
    try:
        assert case.run().phase == "waiting_external"
        assert case.send(execute(repr(marker))).phase == "waiting_external"
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
    finally:
        case.host.close()


def test_source10_parent_old_cell_helper_cannot_complete_later_cell(tmp_path: Path):
    case = make_case(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        assert case.send(execute("saved_complete = aar_complete\n42")).phase == "waiting_external"
        with pytest.raises(RlmWorkbenchError, match="earlier cell"):
            case.send(execute(f"saved_complete({OUTPUT!r})"))
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
    finally:
        case.host.close()


def test_source10_parent_final_cell32_completes_without_cell33(tmp_path: Path):
    case = make_case(tmp_path, max_calls=48, max_cells=32)
    try:
        assert case.run().phase == "waiting_external"
        for i in range(31):
            assert case.send(execute(f"last_controlled_index = {i}")).phase == "waiting_external"
        result = case.send(execute(f"aar_complete({OUTPUT!r})"))
        assert isinstance(result, RlmWorkbenchResult)
        assert result.model_calls == 32 and result.python_cells == 32
        assert counts(case)["rlm_workbench_cells"] == 32
        assert counts(case)["caller_work_tickets"] == 32
        assert counts(case)["rlm_workbench_finalization_manifests"] == 1
    finally:
        case.host.close()


@pytest.mark.parametrize("axis", ["cancel", "deadline", "lease_owner"])
def test_source10_parent_current_authority_before_completion_commit(
    tmp_path: Path, monkeypatch, axis: str
):
    case = make_case(tmp_path)
    entered = []
    injected = []
    coordinator = case.host.rlm_workbench
    assert coordinator is not None
    original = coordinator._finalize

    def fenced(*args, **kwargs):
        entered.append(axis)
        operation = case.record.operation.value
        if axis == "deadline":
            deadline = case.request.root["context"]["deadline_unix_ms"]
            monkeypatch.setattr(coordinator, "_now_ms", lambda: deadline + 1)
        elif axis == "cancel":
            case.host.registry.request_cancel(
                case.record.operation,
                requested_by_digest=canonical_sha256("controlled-canceller"),
                reason_code="controlled_completion_cancel",
            )
        else:
            with sqlite3.connect(case.database) as db:
                cur = db.execute(
                    "UPDATE operation_leases SET lease_epoch=lease_epoch+1, "
                    "owner_digest=? WHERE operation_id=? AND released_at_unix_ms IS NULL",
                    (canonical_sha256("replacement-controlled-owner"), operation),
                )
                assert cur.rowcount == 1
        injected.append(axis)
        return original(*args, **kwargs)

    monkeypatch.setattr(coordinator, "_finalize", fenced)
    try:
        assert case.run().phase == "waiting_external"
        try:
            outcome = case.send(execute(f"aar_complete({OUTPUT!r})"))
        except (RlmWorkbenchError, RegistryError, ArtifactPublicationConflict):
            pass
        else:
            assert outcome.root.get("phase", outcome.root.get("terminal_status")) != "succeeded"
        assert entered == [axis] and injected == [axis], "must reach and mutate intended seam"
        assert counts(case)["rlm_workbench_finalization_manifests"] == 0
    finally:
        case.host.close()


def test_source10_parent_native_repository_exact_replay_and_tampered_result(tmp_path: Path):
    """Pre-change discriminator: exact manifest replay must not accept changed result bytes."""
    case = make_case(tmp_path)
    repository = ArtifactPublicationRepository(case.database, now_ms=lambda: FIXED_NOW_MS)
    try:
        result = legacy_complete(case)
        with sqlite3.connect(case.database) as db:
            manifest = json.loads(
                db.execute(
                    "SELECT manifest_json FROM rlm_workbench_finalization_manifests "
                    "WHERE operation_id=?",
                    (case.record.operation.value,),
                ).fetchone()[0]
            )
        assert repository.finalize(manifest, result=result.root).replayed is True
        changed = json.loads(json.dumps(result.root))
        changed["output"]["answer"] = "different controlled result bytes"
        # Keep stale digest intentionally: this negative targets result digest/content binding,
        # not output-shape, manifest schema, or current owner admission.
        with pytest.raises(
            (ArtifactPublicationConflict, ValueError), match=r"result|digest|content"
        ):
            repository.finalize(manifest, result=changed)
        assert counts(case)["rlm_workbench_finalization_manifests"] == 1
    finally:
        repository.close()
        case.host.close()


def test_source10_parent_same_session_jobs_keep_result_and_artifacts_private(tmp_path: Path):
    first = make_case(tmp_path / "first", require_named=True)
    assert first.run().phase == "waiting_external"
    second = admit_second_same_session(first)
    try:
        assert (
            first.request.root["context"]["session_id"]
            == second.request.root["context"]["session_id"]
        )
        assert second.run().phase == "waiting_external"
        for case, label in [(first, "first"), (second, "second")]:
            output = {"answer": label, "artifacts": []}
            artifacts = {"reports/shared-name.txt": (label, "text/plain")}
            result = case.send(execute(f"aar_complete({output!r}, {artifacts!r})"))
            assert isinstance(result, RlmWorkbenchResult)
            assert result.root["output"] == output
            assert result.root["operation"] == case.record.operation.model_dump(mode="json")
            assert len(result.artifact_bindings) == 1
            with sqlite3.connect(case.database) as db:
                assert (
                    db.execute(
                        "SELECT content FROM rlm_workbench_artifact_stages WHERE operation_id=?",
                        (case.record.operation.value,),
                    ).fetchone()[0]
                    == label.encode()
                )
    finally:
        first.host.close()
