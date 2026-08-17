from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"
BASELINE_SQL = SDD / "fixtures" / "registry-v5.sql"
MIGRATION_SQL = SDD / "migration-v6.sql"
FIXED_NOW_MS = 2_000_000_000_000
OPERATION_ID = "op-artifact-test"
ATTEMPT_ID = "attempt-artifact-test"
ATTEMPT_FENCE = "sha256:" + "a" * 64
CELL_EXECUTION_ID = "cell-artifact-test"
WORKSPACE_ID = "workspace-artifact-test"


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def canonical_digest(value: object) -> str:
    return digest_bytes(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    )


def create_artifact_registry(path: Path, *, phase: str = "running") -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(BASELINE_SQL.read_text(encoding="utf-8"))
        connection.executescript(MIGRATION_SQL.read_text(encoding="utf-8"))
        connection.execute(
            """
            INSERT INTO operations(
                operation_id, host_value, principal_value, idempotency_key,
                input_digest, state, certainty, runtime_generation,
                record_revision, reconciliation_required, request_json,
                payload_json, created_at_unix_ms, updated_at_unix_ms
            ) VALUES(?, 'host', 'principal', 'idem-artifact', ?, 'accepted',
                     'certain', 1, 0, 0, '{}', '{}', ?, ?)
            """,
            (OPERATION_ID, "sha256:" + "1" * 64, FIXED_NOW_MS, FIXED_NOW_MS),
        )
        connection.execute(
            """
            INSERT INTO operation_attempts(
                operation_id, attempt_no, attempt_id, runtime_generation,
                dispatcher_generation, state, certainty, created_at_unix_ms
            ) VALUES(?, 1, ?, 1, 1, 'running', 'certain', ?)
            """,
            (OPERATION_ID, ATTEMPT_ID, FIXED_NOW_MS),
        )
        connection.execute(
            """
            INSERT INTO rlm_workbench_jobs(
                operation_id, phase, control_revision, cancellation_revision,
                cancellation_requested, spec_json, spec_digest, context_digest,
                route_binding_digest, cumulative_deadline_unix_ms,
                workspace_id, workspace_generation, workspace_revision,
                certainty, created_at_unix_ms, updated_at_unix_ms
            ) VALUES(?, ?, 1, 0, 0, '{}', ?, ?, ?, ?, ?, 1, 0,
                     'certain', ?, ?)
            """,
            (
                OPERATION_ID,
                phase,
                "sha256:" + "2" * 64,
                "sha256:" + "3" * 64,
                "sha256:" + "4" * 64,
                FIXED_NOW_MS + 60_000,
                WORKSPACE_ID,
                FIXED_NOW_MS,
                FIXED_NOW_MS,
            ),
        )
        connection.execute(
            """
            INSERT INTO rlm_workbench_cells(
                operation_id, cell_execution_id, cell_index, source_json,
                source_digest, state, attempt_id, attempt_fence, workspace_id,
                workspace_generation, pre_workspace_revision,
                created_at_unix_ms, updated_at_unix_ms
            ) VALUES(?, ?, 0, '{}', ?, 'running', ?, ?, ?, 1, 0, ?, ?)
            """,
            (
                OPERATION_ID,
                CELL_EXECUTION_ID,
                "sha256:" + "5" * 64,
                ATTEMPT_ID,
                ATTEMPT_FENCE,
                WORKSPACE_ID,
                FIXED_NOW_MS,
                FIXED_NOW_MS,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def artifact_binding(content: bytes) -> dict[str, object]:
    return {
        "schema_version": "aar.artifact-binding.v1",
        "logical_name": "result/output.txt",
        "media_type": "text/plain",
        "digest": digest_bytes(content),
        "size_bytes": len(content),
        "operation": {"type": "operation", "value": OPERATION_ID},
        "workspace": {"type": "workspace", "value": WORKSPACE_ID},
        "workspace_generation": 1,
        "cell_execution_id": CELL_EXECUTION_ID,
        "role": "result",
    }


def cell_manifest(stage_id: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "aar.cell-commit-manifest.v1",
        "operation": {"type": "operation", "value": OPERATION_ID},
        "attempt_id": ATTEMPT_ID,
        "attempt_fence": ATTEMPT_FENCE,
        "workspace": {"type": "workspace", "value": WORKSPACE_ID},
        "workspace_generation": 1,
        "pre_revision": 0,
        "post_revision": 1,
        "cell_execution_id": CELL_EXECUTION_ID,
        "source_digest": "sha256:" + "5" * 64,
        "result_digest": "sha256:" + "6" * 64,
        "checkpoint_digest": "sha256:" + "7" * 64,
        "artifact_stage_ids": [stage_id],
    }
    return {**payload, "manifest_digest": canonical_digest(payload)}


def finalization_manifest(binding: dict[str, object]) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "aar.finalization-manifest.v1",
        "operation": {"type": "operation", "value": OPERATION_ID},
        "finalizer_attempt_id": ATTEMPT_ID,
        "finalizer_attempt_fence": ATTEMPT_FENCE,
        "expected_prior_phase": "finalizing",
        "control_revision": 2,
        "expected_cancellation_revision": 0,
        "expected_deadline_unix_ms": FIXED_NOW_MS + 60_000,
        "worker_owner_generation": 1,
        "unresolved_required_ticket_count": 0,
        "result_digest": "sha256:" + "8" * 64,
        "artifact_binding_digests": [canonical_digest(binding)],
        "usage_digest": "sha256:" + "9" * 64,
        "child_policy_digest": "sha256:" + "b" * 64,
        "workspace_disposition_digest": "sha256:" + "c" * 64,
    }
    return {**payload, "manifest_digest": canonical_digest(payload)}


class InjectedCrash(RuntimeError):
    pass


class CrashOnce:
    def __init__(self, barrier: str) -> None:
        self.barrier = barrier
        self.triggered = False

    def __call__(self, barrier: str) -> None:
        if barrier == self.barrier and not self.triggered:
            self.triggered = True
            raise InjectedCrash(barrier)


def stage_one(repo: Any, content: bytes = b"artifact-body") -> tuple[Any, dict[str, object]]:
    from aar.rlm_workbench_models import ArtifactBinding

    binding_document = artifact_binding(content)
    binding = ArtifactBinding.model_validate(binding_document, strict=True)
    stage = repo.stage_content(
        stage_id="stage-artifact-test",
        binding=binding,
        content=content,
    )
    return stage, binding_document


def test_staged_artifact_bytes_are_invisible_until_certain_cell_manifest(
    tmp_path: Path,
) -> None:
    from aar.runtime.artifact_publication import (
        ArtifactNotPublished,
        ArtifactPublicationRepository,
    )

    database = tmp_path / "registry.sqlite"
    create_artifact_registry(database)
    repo = ArtifactPublicationRepository(database, now_ms=lambda: FIXED_NOW_MS)
    try:
        stage, _ = stage_one(repo)
        assert stage.state == "staged"
        with pytest.raises(ArtifactNotPublished):
            repo.read_cell_visible(stage.stage_id)
        assert repo.list_final_bindings(OPERATION_ID) == ()

        receipt = repo.commit_cell(cell_manifest(stage.stage_id))
        assert receipt.replayed is False
        assert repo.read_cell_visible(stage.stage_id) == b"artifact-body"
        assert repo.list_final_bindings(OPERATION_ID) == ()
    finally:
        repo.close()


def test_cell_commit_crash_is_atomic_and_exact_replay_is_idempotent(
    tmp_path: Path,
) -> None:
    from aar.runtime.artifact_publication import (
        ArtifactNotPublished,
        ArtifactPublicationRepository,
    )

    database = tmp_path / "registry.sqlite"
    create_artifact_registry(database)
    crash = CrashOnce("cell_manifest_inserted")
    repo = ArtifactPublicationRepository(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        barrier=crash,
    )
    try:
        stage, _ = stage_one(repo)
        manifest = cell_manifest(stage.stage_id)
        with pytest.raises(InjectedCrash, match="cell_manifest_inserted"):
            repo.commit_cell(manifest)

        with pytest.raises(ArtifactNotPublished):
            repo.read_cell_visible(stage.stage_id)
        assert repo.cell_manifest(OPERATION_ID, CELL_EXECUTION_ID) is None

        first = repo.commit_cell(manifest)
        replay = repo.commit_cell(manifest)
        assert first.replayed is False
        assert replay.replayed is True
        assert replay.manifest_digest == first.manifest_digest
        assert repo.read_cell_visible(stage.stage_id) == b"artifact-body"
    finally:
        repo.close()


def test_finalization_crash_replay_commits_result_and_named_artifacts_once(
    tmp_path: Path,
) -> None:
    from aar.runtime.artifact_publication import ArtifactPublicationRepository

    database = tmp_path / "registry.sqlite"
    create_artifact_registry(database)
    crash = CrashOnce("finalization_manifest_inserted")
    repo = ArtifactPublicationRepository(
        database,
        now_ms=lambda: FIXED_NOW_MS,
        barrier=crash,
    )
    try:
        stage, binding = stage_one(repo)
        repo.commit_cell(cell_manifest(stage.stage_id))
        connection = sqlite3.connect(database)
        try:
            connection.execute(
                "UPDATE rlm_workbench_jobs SET phase='finalizing', "
                "control_revision=2 WHERE operation_id=?",
                (OPERATION_ID,),
            )
            connection.commit()
        finally:
            connection.close()

        manifest = finalization_manifest(binding)
        result: dict[str, object] = {
            "schema_version": "aar.rlm-workbench-result.v1",
            "operation": {"type": "operation", "value": OPERATION_ID},
            "result_digest": manifest["result_digest"],
        }
        with pytest.raises(InjectedCrash, match="finalization_manifest_inserted"):
            repo.finalize(manifest, result=result)

        assert repo.finalization_manifest(OPERATION_ID) is None
        assert repo.list_final_bindings(OPERATION_ID) == ()

        first = repo.finalize(manifest, result=result)
        replay = repo.finalize(manifest, result=result)
        assert first.replayed is False
        assert replay.replayed is True
        assert replay.manifest_digest == first.manifest_digest
        final_bindings = repo.list_final_bindings(OPERATION_ID)
        assert len(final_bindings) == 1
        assert final_bindings[0].logical_name == "result/output.txt"

        connection = sqlite3.connect(database)
        try:
            row = connection.execute(
                "SELECT phase, result_digest FROM rlm_workbench_jobs "
                "WHERE operation_id=?",
                (OPERATION_ID,),
            ).fetchone()
            assert row == ("succeeded", manifest["result_digest"])
            assert connection.execute(
                "SELECT COUNT(*) FROM rlm_workbench_finalization_manifests "
                "WHERE operation_id=?",
                (OPERATION_ID,),
            ).fetchone() == (1,)
        finally:
            connection.close()
    finally:
        repo.close()
