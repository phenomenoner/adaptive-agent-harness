"""Parent-owned local consumer composition; model tickets are scripted, no provider."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest
from test_review_regressions import reference
from test_workbench_cell_feedback import CallerCase

import aar
import aar.runtime.ipython_backend as backend


@pytest.fixture(autouse=True)
def exact_executed_package(monkeypatch):
    default_package = Path(__file__).resolve().parents[1] / "src" / "aar"
    expected = Path(
        os.environ.get("AAR_LIFECYCLE_EXPECTED_PACKAGE", str(default_package))
    ).resolve()
    assert Path(aar.__file__).resolve().parent == expected
    if os.environ.get("AAR_LIFECYCLE_SOURCE_PROJECTION") == "1":
        source = str(expected.parent)

        def command(module, arguments=(), *, isolated):
            assert module == "aar.runtime.ipython_worker" and isolated
            bootstrap = (
                f"import sys,runpy;sys.path.insert(0,{source!r});"
                f'runpy.run_module({module!r},run_name="__main__")'
            )
            return [sys.executable, "-I", "-B", "-c", bootstrap, *arguments]

        monkeypatch.setattr(backend, "exact_module_command", command)
    # In the installed lane the normal exact_module_command is NOT replaced.


def execute(code):
    return {"kind": "execute_cell", "code": code, "expected_result_hint": None}


def current_prompt(case):
    return json.loads(case.pending().root["request"]["prompt"])


class ConsumerCase(CallerCase):
    def __init__(self, *args, **kwargs):
        self.observed_worker_states = []
        super().__init__(*args, **kwargs)

    def run(self):
        result = super().run()
        workspace = self.host.program_workspace
        assert isinstance(workspace, backend.SupervisedIPythonWorkspaceBackend)
        for state in workspace._states.values():
            if not any(state is prior for prior in self.observed_worker_states):
                self.observed_worker_states.append(state)
        return result


def close_and_verify(case):
    primary = sys.exception()
    states = case.observed_worker_states
    try:
        case.host.close()
        if primary is None:
            assert states, "A real IPython worker must have existed"
        assert all(state.process.poll() is not None for state in states)
        pipe_states = []
        for state in states:
            for name in ("stdin", "stdout", "stderr"):
                pipe = getattr(state.process, name)
                if pipe is None:
                    continue
                native_open = False
                if not pipe.closed:
                    try:
                        os.fstat(pipe.fileno())
                        native_open = True
                    except (OSError, ValueError):
                        native_open = False
                pipe_states.append(
                    {"name": name, "wrapper_closed": pipe.closed, "native_fd_open": native_open}
                )
        assert all(item["wrapper_closed"] and not item["native_fd_open"] for item in pipe_states), (
            json.dumps(pipe_states)
        )
    except BaseException as cleanup_error:
        if primary is None:
            raise
        primary.add_note(f"Additional owned cleanup failure: {type(cleanup_error).__name__}")


def test_multiple_artifacts_then_verification_finalize_resolves_exact_bytes(tmp_path):
    case = ConsumerCase(tmp_path, max_calls=4)
    try:
        case.run()
        case.send(
            execute("answer = '42'\naar_artifact('reports/answer.txt', answer, 'text/plain')")
        )
        case.send(execute("aar_artifact('reports/check.txt', 'verified', 'text/plain')"))
        case.send(execute("assert answer == str(6 * 7)\nprint('checks passed')"))
        prompt = current_prompt(case)
        inventory = prompt["committed_artifacts"]
        assert {item["logical_name"] for item in inventory} == {
            "reports/answer.txt",
            "reports/check.txt",
        }
        result = case.send(
            {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": [item["stage_id"] for item in inventory],
            }
        )
        coordinator = case.host.rlm_workbench
        assert coordinator is not None
        assert coordinator.snapshot(case.record.operation).phase == "succeeded"
        assert len(result.artifact_bindings) == 2
        assert (
            coordinator.resolve_public_artifact(reference(case.record.operation.value, b"42"))
            == b"42"
        )
        assert (
            coordinator.resolve_public_artifact(reference(case.record.operation.value, b"verified"))
            == b"verified"
        )
        with sqlite3.connect(case.database) as database:
            assert (
                database.execute(
                    "select count(*) from rlm_workbench_finalization_manifests"
                ).fetchone()[0]
                == 1
            )
            assert database.execute("select count(*) from caller_work_tickets").fetchone()[0] == 4
    finally:
        close_and_verify(case)


def test_failed_cell_stage_excluded_but_later_success_can_finalize(tmp_path):
    case = ConsumerCase(tmp_path, max_calls=4)
    try:
        case.run()
        case.send(
            execute(
                "aar_artifact('reports/failed.txt', 'unverified', "
                "'text/plain')\nraise RuntimeError('controlled failure')"
            )
        )
        failed = current_prompt(case)
        assert failed["last_committed_cell"]["result"]["status"] == "failed"
        assert failed["committed_artifacts"] == []
        case.send(execute("aar_artifact('reports/accepted.txt', '42', 'text/plain')"))
        case.send(execute("assert 6 * 7 == 42"))
        inventory = current_prompt(case)["committed_artifacts"]
        assert [item["logical_name"] for item in inventory] == ["reports/accepted.txt"]
        case.send(
            {
                "kind": "finalize",
                "output": {"answer": "42", "artifacts": []},
                "artifact_stage_ids": [item["stage_id"] for item in inventory],
            }
        )
        coordinator = case.host.rlm_workbench
        assert coordinator is not None
        assert (
            coordinator.resolve_public_artifact(reference(case.record.operation.value, b"42"))
            == b"42"
        )
        with pytest.raises(KeyError):
            coordinator.resolve_public_artifact(
                reference(case.record.operation.value, b"unverified")
            )
        with sqlite3.connect(case.database) as database:
            states = dict(
                database.execute("select logical_name,state from rlm_workbench_artifact_stages")
            )
            assert states == {"reports/failed.txt": "staged", "reports/accepted.txt": "committed"}
    finally:
        close_and_verify(case)


def test_completion_helper_one_cell_publishes_and_closes_owned_worker(tmp_path):
    case = ConsumerCase(tmp_path, max_calls=1)
    try:
        case.run()
        result = case.send(
            execute(
                "aar_complete({'answer':'42','artifacts':[]}, "
                "{'reports/answer.txt': ('42','text/plain')})"
            )
        )
        coordinator = case.host.rlm_workbench
        assert coordinator is not None
        assert result.python_cells == 1 and result.model_calls == 1
        assert coordinator.snapshot(case.record.operation).phase == "succeeded"
        assert (
            coordinator.resolve_public_artifact(reference(case.record.operation.value, b"42"))
            == b"42"
        )
    finally:
        close_and_verify(case)
