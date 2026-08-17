from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from aar.canonical import canonical_json_bytes, canonical_sha256

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = (
    ROOT
    / "docs"
    / "sdd"
    / "aar-rlm-native-workbench-v2"
    / "fixtures"
)


def load_fixture(name: str) -> dict[str, Any]:
    value = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class CountingDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        receipt_frame = load_fixture("valid-worker-broker-receipt.json")
        payload = receipt_frame["payload"]
        assert isinstance(payload, dict)
        self.receipt_payload = payload

    def __call__(self, intent: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(copy.deepcopy(intent))
        return copy.deepcopy(self.receipt_payload)


def test_worker_frame_retry_reuses_sequence_and_broker_identity_exactly_once(
    tmp_path: Path,
) -> None:
    from aar.rlm_workbench_models import WorkspaceBrokerFrame

    from aar.runtime.ipython_backend import WorkspaceBrokerSession

    dispatcher = CountingDispatcher()
    database = tmp_path / "broker-frame-journal.sqlite"
    frame = WorkspaceBrokerFrame.model_validate(
        load_fixture("valid-worker-broker-intent-typed.json"),
        strict=True,
    )
    session = WorkspaceBrokerSession(
        database,
        operation_id="fixture-operation",
        attempt_id="fixture-attempt",
        dispatch=dispatcher,
    )
    try:
        first = session.handle_frame(frame)
        retry = session.handle_frame(frame)

        assert retry.model_dump(mode="json") == first.model_dump(mode="json")
        assert len(dispatcher.calls) == 1
        assert session.broker_call_count == 1
        assert session.event_count == 1
    finally:
        session.close()

    reopened_dispatcher = CountingDispatcher()
    reopened = WorkspaceBrokerSession(
        database,
        operation_id="fixture-operation",
        attempt_id="fixture-attempt",
        dispatch=reopened_dispatcher,
    )
    try:
        replay_after_restart = reopened.handle_frame(frame)
        assert replay_after_restart.model_dump(mode="json") == first.model_dump(
            mode="json"
        )
        assert reopened_dispatcher.calls == []
        assert reopened.broker_call_count == 1
        assert reopened.event_count == 1
    finally:
        reopened.close()


def test_same_frame_sequence_with_divergent_intent_is_protocol_conflict(
    tmp_path: Path,
) -> None:
    from aar.rlm_workbench_models import WorkspaceBrokerFrame

    from aar.runtime.ipython_backend import WorkerFrameConflict, WorkspaceBrokerSession

    dispatcher = CountingDispatcher()
    database = tmp_path / "broker-frame-journal.sqlite"
    original_document = load_fixture("valid-worker-broker-intent-typed.json")
    original = WorkspaceBrokerFrame.model_validate(original_document, strict=True)
    session = WorkspaceBrokerSession(
        database,
        operation_id="fixture-operation",
        attempt_id="fixture-attempt",
        dispatch=dispatcher,
    )
    try:
        session.handle_frame(original)
        divergent_document = copy.deepcopy(original_document)
        request = divergent_document["payload"]["request"]
        assert isinstance(request, dict)
        request["prompt"] = "different prompt under same frame sequence"
        payload_bytes = canonical_json_bytes(divergent_document["payload"])
        divergent_document["payload_bytes"] = len(payload_bytes)
        divergent_document["payload_digest"] = canonical_sha256(
            divergent_document["payload"]
        )
        divergent = WorkspaceBrokerFrame.model_validate(
            divergent_document,
            strict=True,
        )

        with pytest.raises(WorkerFrameConflict, match=r"sequence|identity|digest"):
            session.handle_frame(divergent)
        assert len(dispatcher.calls) == 1
        assert session.broker_call_count == 1
        assert session.event_count == 1
    finally:
        session.close()
