"""Discriminating regressions for the independently reviewed F1/F2 batch."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_rlm_workbench import (
    FIXED_NOW_MS,
    BrokerRoundTripPlanner,
)
from test_rlm_workbench import (
    test_rebind_replays_after_coordinator_crash as exercise_rebind_replay,
)

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.artifact_publication import ArtifactPublicationRepository
from aar.runtime.brokers import FakeArtifactBroker
from aar.runtime.ipython_backend import SupervisedIPythonWorkspaceBackend
from aar.runtime.programming import PlainPythonWorkspaceBackend
from aar.runtime.rlm_workbench import RlmWorkbenchConflict, RlmWorkbenchCoordinator
from aar.runtime.workspace_models import ProgrammableWorkspaceHandle, WorkspaceProgramSpec
from aar.schemas import ArtifactIdRef, ArtifactReference, OperationRef, SessionRef, WorkspaceRef


def handle_for(backend) -> ProgrammableWorkspaceHandle:
    return ProgrammableWorkspaceHandle(
        workspace=WorkspaceRef(value="workspace-review-events"),
        backend=backend.descriptor,
        generation=1,
        revision=0,
    )


def artifact_response() -> dict:
    return {
        "status": "succeeded",
        "result": None,
        "result_excluded_reason": None,
        "events": [{"kind": "artifact", "artifact_index": 0}],
        "artifacts": [
            {
                "name": "answer.txt",
                "media_type": "text/plain",
                "content_base64": base64.b64encode(b"42").decode(),
            }
        ],
    }


@pytest.mark.parametrize(
    "backend_type", [PlainPythonWorkspaceBackend, SupervisedIPythonWorkspaceBackend]
)
@pytest.mark.parametrize("max_events", [1, 2, 3])
def test_ordinary_cell_error_preserves_primary_identity_at_minimum_event_capacity(
    backend_type, max_events: int
) -> None:
    backend = backend_type()
    handle = backend.create(
        WorkspaceRef(value="workspace-review-cell-error"),
        SessionRef(value="session-review-cell-error"),
    )
    try:
        result = backend.execute(
            OperationRef(value="operation-review-cell-error"),
            handle,
            WorkspaceProgramSpec(
                code="raise ValueError('original failure')", max_events=max_events
            ),
        )
        assert result.status == "failed"
        assert result.workspace_lost is False
        assert 1 <= len(result.events) <= max_events
        assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
        error = next(event for event in result.events if event.kind == "exception")
        assert error.data["type"] == "ValueError"
        assert error.data["message"] == "original failure"
    finally:
        backend.shutdown()


@pytest.mark.parametrize("failure_site", ["sink", "stage"])
@pytest.mark.parametrize("max_events", [1, 2, 3])
def test_artifact_error_preserves_primary_identity_within_event_capacity(
    failure_site: str, max_events: int
) -> None:
    def fail(*_args):
        raise OSError("original artifact failure")

    backend = SupervisedIPythonWorkspaceBackend(
        artifact_sink=fail if failure_site == "sink" else FakeArtifactBroker().put,
        artifact_stage_handler=fail if failure_site == "stage" else None,
    )
    try:
        result = backend._result_from_response(
            OperationRef(value="operation-review-artifact-error"),
            handle_for(backend),
            WorkspaceProgramSpec(code="1", max_events=max_events),
            artifact_response(),
        )
        assert result.status == "failed"
        assert 1 <= len(result.events) <= max_events
        error = next(event for event in result.events if event.kind == "exception")
        assert error.data["type"] == "OSError"
        assert error.data["message"] == "original artifact failure"
    finally:
        backend.shutdown()


@pytest.mark.parametrize("max_events", [1, 2, 3])
def test_lost_result_keeps_failure_within_declared_event_capacity(max_events: int) -> None:
    backend = SupervisedIPythonWorkspaceBackend()
    try:
        result = backend._lost_result(
            OperationRef(value="operation-review-lost"),
            handle_for(backend),
            "timed_out",
            "original worker loss",
            max_events,
        )
        assert result.status == "timed_out" and result.workspace_lost
        assert 1 <= len(result.events) <= max_events
        assert [event.sequence for event in result.events] == list(range(1, len(result.events) + 1))
        assert any(event.kind == "exception" for event in result.events)
    finally:
        backend.shutdown()


@pytest.mark.parametrize("callback_present", [False, True])
def test_native_execution_without_active_binding_cannot_use_legacy_sink(callback_present) -> None:
    coordinator = object.__new__(RlmWorkbenchCoordinator)
    coordinator._active_lock = threading.RLock()
    coordinator._active_cells = {}
    legacy_calls = []
    legacy = FakeArtifactBroker()

    def legacy_sink(*args):
        legacy_calls.append(args)
        return legacy.put(*args)

    backend = SupervisedIPythonWorkspaceBackend(
        artifact_sink=legacy_sink,
        artifact_stage_handler=coordinator._stage_worker_artifact if callback_present else None,
    )
    try:
        result = backend._result_from_response(
            OperationRef(value="exec-review-native"),
            handle_for(backend),
            WorkspaceProgramSpec(code="1", max_events=1),
            artifact_response(),
        )
        assert legacy_calls == []
        assert result.status == "failed"
        assert len(result.events) == 1
        assert result.events[0].data["type"] == "WorkspaceOperationConflict"
    finally:
        backend.shutdown()


BARRIERS = (
    "rebind_prepared_before_commit",
    "rebind_committed_before_delivery",
    "rebind_delivered_before_ack",
    "rebind_acked_before_cell_commit",
)


def legacy_rows(connection):
    return connection.execute(
        "SELECT digest, content FROM broker_artifacts ORDER BY digest"
    ).fetchall()


class ArtifactRoundTripPlanner(BrokerRoundTripPlanner):
    database: Path

    def plan(self, operation, spec, snapshot):
        directive = super().plan(operation, spec, snapshot)
        if directive["kind"] == "execute_cell":
            with sqlite3.connect(self.database) as connection:
                self.legacy_before = legacy_rows(connection)
            directive["code"] = (
                "aar_artifact('before.txt', b'42', 'text/plain')\n"
                + directive["code"]
                + "\naar_artifact('after.txt', b'not-selected', 'text/plain')\nanswer"
            )
            self.directive = directive
        else:
            with sqlite3.connect(self.database) as connection:
                assert connection.execute(
                    "SELECT COUNT(*) FROM rlm_workbench_artifact_stages WHERE state='committed'"
                ).fetchone() == (2,)
                assert legacy_rows(connection) == self.legacy_before
            publications = ArtifactPublicationRepository(self.database, now_ms=lambda: FIXED_NOW_MS)
            try:
                with pytest.raises(KeyError):
                    publications.resolve_public_reference(reference(operation.value, b"42"))
            finally:
                publications.close()
            cell_id = (
                "cell-"
                + hashlib.sha256(
                    f"{operation.value}\0{0}\0{canonical_sha256(self.directive)}".encode()
                ).hexdigest()[:32]
            )
            directive["artifact_stage_ids"] = [
                "stage-"
                + hashlib.sha256(
                    canonical_json_bytes(
                        {
                            "operation": operation.model_dump(mode="json"),
                            "cell_execution_id": cell_id,
                            "ordinal": 1,
                            "logical_name": "before.txt",
                            "digest": "sha256:" + hashlib.sha256(b"42").hexdigest(),
                        }
                    )
                ).hexdigest()[:48]
            ]
        return directive


def reference(operation_id: str, content: bytes) -> ArtifactReference:
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    return ArtifactReference(
        artifact=ArtifactIdRef(value="artifact-" + digest.removeprefix("sha256:")[:32]),
        digest=digest,
        media_type="text/plain",
        size_bytes=len(content),
        created_by=OperationRef(value=operation_id),
        redacted=False,
    )


@pytest.mark.parametrize("barrier", BARRIERS)
def test_suspended_artifacts_commit_and_finalize_without_legacy_fallback(
    tmp_path: Path, barrier: str
) -> None:
    planner = ArtifactRoundTripPlanner()
    planner.database = tmp_path / "registry.sqlite"
    exercise_rebind_replay(
        tmp_path, barrier, BARRIERS[: BARRIERS.index(barrier) + 1], _planner=planner
    )
    database = tmp_path / "registry.sqlite"
    with sqlite3.connect(database) as connection:
        operation_id, manifest_json = connection.execute(
            "SELECT operation_id, manifest_json FROM rlm_workbench_cell_manifests"
        ).fetchone()
        stages = connection.execute(
            "SELECT stage_id, state, operation_id FROM rlm_workbench_artifact_stages "
            "ORDER BY stage_id"
        ).fetchall()
        assert len(stages) == 2
        assert all(row[1:] == ("committed", operation_id) for row in stages)
        assert json.loads(manifest_json)["artifact_stage_ids"] == [row[0] for row in stages]
        assert legacy_rows(connection) == planner.legacy_before
    publications = ArtifactPublicationRepository(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        assert publications.resolve_public_reference(reference(operation_id, b"42")) == b"42"
        with pytest.raises(KeyError):
            publications.resolve_public_reference(reference(operation_id, b"not-selected"))
    finally:
        publications.close()


def test_resumed_artifact_still_obeys_its_native_media_policy(tmp_path: Path) -> None:
    class InvalidMediaPlanner(ArtifactRoundTripPlanner):
        def plan(self, operation, spec, snapshot):
            directive = super().plan(operation, spec, snapshot)
            if directive["kind"] == "execute_cell":
                directive["code"] = directive["code"].replace("'text/plain'", "'image/png'")
                self.directive = directive
            return directive

    planner = InvalidMediaPlanner()
    planner.database = tmp_path / "registry.sqlite"
    with pytest.raises(RlmWorkbenchConflict, match="media type is not allowed"):
        exercise_rebind_replay(tmp_path, BARRIERS[0], BARRIERS[:1], _planner=planner)
    with sqlite3.connect(planner.database) as connection:
        assert legacy_rows(connection) == planner.legacy_before
        assert connection.execute(
            "SELECT COUNT(*) FROM rlm_workbench_artifact_stages"
        ).fetchone() == (0,)


def test_late_binding_release_cannot_remove_an_equal_valued_successor() -> None:
    coordinator = object.__new__(RlmWorkbenchCoordinator)
    coordinator._active_lock = threading.RLock()
    coordinator._active_cells = {}
    backend = SupervisedIPythonWorkspaceBackend()
    execution = OperationRef(value="exec-review-replacement")
    spec = SimpleNamespace(
        root={
            "budgets": {"max_artifact_count": 2, "max_artifact_bytes": 1024},
            "artifacts": {"allowed_media_types": ["text/plain"]},
        }
    )
    try:
        with coordinator._active_cell_binding(
            execution,
            OperationRef(value="operation-review-binding"),
            object(),
            object(),
            handle_for(backend),
            spec,
            object(),
            cell_execution_id="cell-review-binding",
        ):
            predecessor = coordinator._active_cells[execution.value]
            with (
                pytest.raises(RlmWorkbenchConflict, match="already has an active"),
                coordinator._active_cell_binding(
                    execution,
                    predecessor.operation,
                    predecessor.attempt,
                    predecessor.fence,
                    predecessor.handle,
                    spec,
                    predecessor.session,
                    cell_execution_id=predecessor.cell_execution_id,
                ),
            ):
                raise AssertionError("competing binding entered")
            successor = replace(predecessor)
            assert successor == predecessor and successor is not predecessor
            with coordinator._active_lock:
                coordinator._active_cells[execution.value] = successor
        assert coordinator._active_cells[execution.value] is successor
    finally:
        backend.shutdown()
