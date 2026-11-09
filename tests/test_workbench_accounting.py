"""Receipt-scoped native accounting; real workbench/IPython, synthetic model sends."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from test_workbench_cell_feedback import CallerCase

from aar.canonical import canonical_sha256
from aar.runtime.rlm_workbench import RlmWorkbenchConflict


def begin_two_turn_case(tmp_path: Path) -> CallerCase:
    case = CallerCase(tmp_path)
    assert case.run().phase == "waiting_external"
    assert case.send({
        "kind": "execute_cell", "code": "answer = 42", "expected_result_hint": None
    }).phase == "waiting_external"
    return case


def finish(case: CallerCase):
    return case.send({
        "kind": "finalize", "output": {"answer": "42", "artifacts": []},
        "artifact_stage_ids": [],
    })


def test_native_totals_count_two_distinct_equal_usage_receipts(tmp_path: Path):
    case = begin_two_turn_case(tmp_path)
    try:
        result = finish(case)
        with sqlite3.connect(case.database) as db:
            usages = [json.loads(r[0]) for r in db.execute(
                "SELECT usage_json FROM model_executions "
                "WHERE operation_id=? AND state='result_committed' ORDER BY rowid",
                (case.record.operation.value,),
            )]
        assert len(usages) == 2 and usages[0] == usages[1]
        assert result.model_calls == 2
        assert result.root["usage_receipt_digests"] == [canonical_sha256(u) for u in usages]
        assert result.root["input_tokens"] == sum(u["input_tokens"] for u in usages) == 14
        assert result.root["output_tokens"] == sum(u["output_tokens"] for u in usages) == 6
    finally:
        case.host.close()


@pytest.mark.parametrize("mutation", ["changed_usage", "missing_usage", "request", "missing_model"])
def test_mismatched_committed_usage_never_publishes_a_terminal_aggregate(tmp_path: Path, mutation):
    case = begin_two_turn_case(tmp_path)
    try:
        with sqlite3.connect(case.database) as db:
            key, body = db.execute(
                "SELECT idempotency_key,usage_json FROM model_executions WHERE operation_id=?",
                (case.record.operation.value,),
            ).fetchone()
            if mutation in {"changed_usage", "missing_usage"}:
                usage = json.loads(body)
                usage["input_tokens"] += 1
                usage["total_tokens"] += 1
                value = json.dumps(usage) if mutation == "changed_usage" else None
                db.execute(
                    "UPDATE model_executions SET usage_json=? "
                    "WHERE operation_id=? AND idempotency_key=?",
                    (value, case.record.operation.value, key),
                )
            elif mutation == "request":
                db.execute(
                    "UPDATE model_executions SET request_digest=? "
                    "WHERE operation_id=? AND idempotency_key=?",
                    ("sha256:" + "f" * 64, case.record.operation.value, key),
                )
            else:
                db.execute(
                    "DELETE FROM model_executions WHERE operation_id=? AND idempotency_key=?",
                    (case.record.operation.value, key),
                )
        with pytest.raises(RlmWorkbenchConflict, match="usage"):
            finish(case)
        with sqlite3.connect(case.database) as db:
            row = db.execute("SELECT count(*) FROM rlm_workbench_finalization_manifests").fetchone()
            assert row[0] == 0
    finally:
        case.host.close()
