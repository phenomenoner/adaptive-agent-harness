"""Completion must not succeed with silently dropped artifact intents."""

from __future__ import annotations

import pytest

from aar.runtime.ipython_worker import _Worker


@pytest.mark.parametrize("kind", ["progress", "display"])
@pytest.mark.parametrize("count", [1, 2])
def test_saturated_optional_events_reject_completion_before_artifact_payloads(kind, count):
    worker = _Worker()
    prior = dict(worker.shell.user_ns)
    files = {f"report-{index}.txt": ("payload", "text/plain") for index in range(count)}
    filler = "aar_progress('step')" if kind == "progress" else "aar_display('step')"
    try:
        result = worker.execute(
            {
                "code": f"{filler}\n{filler}\naar_complete({{'answer': 'ok'}}, {files!r})",
                "max_events": 4,
                "max_output_chars": 8192,
                "artifact_enabled": True,
                "broker_enabled": False,
            }
        )
        assert result["status"] == "failed"
        assert result["artifacts"] == []
        assert not any(event["kind"] == "artifact" for event in result["events"])
    finally:
        worker.shell.user_ns.clear()
        worker.shell.user_ns.update(prior)


@pytest.mark.parametrize("count", [1, 2])
def test_completion_with_sufficient_reserved_event_capacity_preserves_every_intent(count):
    worker = _Worker()
    prior = dict(worker.shell.user_ns)
    files = {f"report-{index}.txt": ("payload", "text/plain") for index in range(count)}
    try:
        result = worker.execute(
            {
                "code": f"aar_complete({{'answer': 'ok'}}, {files!r})",
                "max_events": 4,
                "max_output_chars": 8192,
                "artifact_enabled": True,
                "broker_enabled": False,
            }
        )
        assert result["status"] == "succeeded"
        assert len(result["artifacts"]) == count
        assert [e["artifact_index"] for e in result["events"] if e["kind"] == "artifact"] == list(
            range(count)
        )
    finally:
        worker.shell.user_ns.clear()
        worker.shell.user_ns.update(prior)
