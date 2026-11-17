"""Offline completion projection through a real local worker and SQLite."""

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from test_workbench_cell_feedback import CallerCase

import aar
import aar.runtime.ipython_backend as backend


@pytest.fixture(autouse=True)
def candidate_worker(monkeypatch):
    if os.environ.get("AAR_LIFECYCLE_SOURCE_PROJECTION") == "0":
        # Installed proof must exercise the real launcher, never this bootstrap.
        return
    # -I ignores PYTHONPATH; explicitly bootstrap the task-owned candidate.
    source = str(Path(aar.__file__).resolve().parent.parent)

    def command(module, arguments=(), *, isolated):
        assert module == "aar.runtime.ipython_worker" and isolated
        bootstrap = (
            f"import sys,runpy;sys.path.insert(0,{source!r});"
            f"runpy.run_module({module!r},run_name='__main__')"
        )
        return [sys.executable, "-I", "-B", "-c", bootstrap, *arguments]

    monkeypatch.setattr(backend, "exact_module_command", command)


def cell(code):
    return {"kind": "execute_cell", "code": code, "expected_result_hint": None}


def test_caller_completion_uses_one_ticket_and_committed_stages(tmp_path):
    expected = Path(
        os.environ.get(
            "AAR_LIFECYCLE_EXPECTED_PACKAGE",
            str(Path(__file__).resolve().parents[1] / "src/aar"),
        )
    ).resolve()
    assert Path(aar.__file__).resolve().parent == expected
    case = CallerCase(tmp_path, max_calls=1)
    try:
        assert case.run().phase == "waiting_external"
        result = case.send(
            cell(
                "import aar.runtime.ipython_worker as w\nfrom pathlib import Path\n"
                f"assert Path(w.__file__).resolve() == "
                f"Path({str(expected / 'runtime/ipython_worker.py')!r})\n"
                "aar_complete({'answer':'42','artifacts':[]}, {f'{n}.txt': "
                "(b'hello','text/plain') for n in range(5)})"
            )
        )
        assert result.root["output"] == {"answer": "42", "artifacts": []}
        assert len(result.artifact_bindings) == 5
        with sqlite3.connect(case.database) as db:
            assert db.execute("select count(*) from caller_work_tickets").fetchone()[0] == 1
            assert (
                db.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 1
            )
    finally:
        case.host.close()


def test_lookalike_and_failed_helper_do_not_finalize(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        result = case.send(
            cell(
                "{'__aar_completion_v1__': {'output': "
                "{'answer':'fake','artifacts':[]}, 'names': []}}"
            )
        )
        assert result.phase == "waiting_external"
        result = case.send(
            cell("aar_complete({'answer':'bad','artifacts':[]})\nraise ValueError('after')")
        )
        assert result.phase == "waiting_external"
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
            values = [
                json.loads(x[0])
                for x in db.execute(
                    "select result_json from rlm_workbench_cells order by cell_index"
                )
            ]
            assert "__aar_ordinary_v1__" in values[0]["result"]
            assert values[1]["status"] == "failed"
    finally:
        case.host.close()


def test_retained_helper_cannot_complete_later_cell(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        assert case.send(cell("old_complete = aar_complete")).phase == "waiting_external"
        with pytest.raises(Exception, match="earlier cell"):
            case.send(cell("old_complete({'answer':'old','artifacts':[]})"))
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        case.host.close()


def test_duplicate_caught_completion_is_rejected(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        with pytest.raises(Exception, match="ambiguous completion"):
            case.send(
                cell(
                    "aar_complete({'answer':'one','artifacts':[]})\ntry:\n    "
                    "aar_complete({'answer':'two','artifacts':[]})\nexcept Exception:\n    pass"
                )
            )
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        case.host.close()


def test_invalid_output_is_not_published(tmp_path):
    case = CallerCase(tmp_path)
    try:
        case.run()
        with pytest.raises(Exception, match="output contract"):
            case.send(cell("aar_complete({'unexpected':'bad'})"))
        with sqlite3.connect(case.database) as db:
            assert (
                db.execute("select count(*) from rlm_workbench_finalization_manifests").fetchone()[
                    0
                ]
                == 0
            )
    finally:
        case.host.close()


def test_service_planner_completes_without_second_call(tmp_path):
    from test_rlm_workbench import (
        FIXED_NOW_MS,
        prepare_v6_registry,
        workbench_envelope,
        workbench_request,
    )

    from aar.runtime.reference_host import ReferenceHost

    class Planner:
        calls = 0

        def plan(self, operation, spec, snapshot):
            self.calls += 1
            assert self.calls == 1
            return cell("aar_complete({'answer':'ok','artifacts':[]})")

    db = tmp_path / "registry.sqlite"
    prepare_v6_registry(db, tmp_path / "snapshot.sqlite")
    planner = Planner()
    host = ReferenceHost(
        db,
        now_ms=lambda: FIXED_NOW_MS,
        workbench_planner=planner,
        enable_durable_dispatch=True,
        dispatcher_concurrency=1,
    )
    try:
        request = workbench_request(host, require_named_artifacts=False)
        record = host.submit_rlm_workbench(workbench_envelope(host, request), request)
        terminal = host.dispatcher.wait(record.operation, timeout_s=10)
        assert terminal.state.value == "succeeded"
        assert planner.calls == 1
        assert host.rlm_workbench.snapshot(record.operation).phase == "succeeded"
    finally:
        host.close()
