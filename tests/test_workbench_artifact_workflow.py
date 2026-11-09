"""Artifact workflow controls: real local IPython, scripted model tickets, no provider."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_workbench_cell_feedback import CallerCase

from aar.runtime.rlm_workbench import RlmWorkbenchConflict


def execute(code: str) -> dict:
    return {"kind": "execute_cell", "code": code, "expected_result_hint": None}


def test_draft_validate_then_stage_once_and_finalize(tmp_path: Path):
    case = CallerCase(tmp_path)
    try:
        assert case.run().phase == "waiting_external"
        assert (
            case.send(execute("draft = '4'\ndraft += '2'\nassert draft == '42'")).phase
            == "waiting_external"
        )
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("SELECT count(*) FROM rlm_workbench_artifact_stages").fetchone()[0] == 0
            )
        assert (
            case.send(execute("aar_artifact('reports/result.txt', draft, 'text/plain')")).phase
            == "waiting_external"
        )
        prompt = json.loads(case.pending().root["request"]["prompt"])
        stages = prompt["last_committed_cell"]["artifact_stage_ids"]
        assert len(stages) == 1
        result = case.send(
            {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": stages,
            }
        )
        assert result.model_calls == 3 and result.python_cells == 2
        assert len(result.artifact_bindings) == 1
        with sqlite3.connect(case.database) as db:
            assert db.execute("SELECT state FROM rlm_workbench_artifact_stages").fetchall() == [
                ("committed",)
            ]
            assert (
                db.execute("SELECT count(*) FROM rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 1
            )
    finally:
        case.host.close()


def test_artifact_count_is_still_cumulative_across_cells(tmp_path: Path):
    case = CallerCase(tmp_path)
    try:
        cap = case.request.root["spec"]["budgets"]["max_artifact_count"]
        assert case.run().phase == "waiting_external"
        assert (
            case.send(
                execute(
                    f"for i in range({cap}):\n"
                    "    aar_artifact(f'reports/item-{i}.txt', b'x', 'text/plain')"
                )
            ).phase
            == "waiting_external"
        )
        with pytest.raises(RlmWorkbenchConflict, match="artifact count budget exhausted"):
            case.send(execute("aar_artifact('reports/extra.txt', b'x', 'text/plain')"))
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("SELECT count(*) FROM rlm_workbench_artifact_stages").fetchone()[0]
                == cap
            )
            assert (
                db.execute("SELECT count(*) FROM rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        case.host.close()


def test_duplicate_name_is_still_rejected_across_cells(tmp_path: Path):
    case = CallerCase(tmp_path)
    try:
        assert case.request.root["spec"]["budgets"]["max_artifact_count"] > 1
        assert case.run().phase == "waiting_external"
        assert (
            case.send(execute("aar_artifact('reports/once.txt', b'old', 'text/plain')")).phase
            == "waiting_external"
        )
        with pytest.raises(RlmWorkbenchConflict, match="logical name is already staged"):
            case.send(execute("aar_artifact('reports/once.txt', b'new', 'text/plain')"))
        with sqlite3.connect(case.database) as db:
            assert db.execute("SELECT content FROM rlm_workbench_artifact_stages").fetchall() == [
                (b"old",)
            ]
            assert (
                db.execute("SELECT count(*) FROM rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        case.host.close()
