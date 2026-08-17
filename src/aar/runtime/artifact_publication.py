"""Atomic staged-artifact publication for AR-RW cell and finalization manifests."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import (
    ArtifactBinding,
    ArtifactStage,
    CellCommitManifest,
    FinalizationManifest,
)
from aar.runtime.sqlite_repository import SQLiteConnectionFactory


class ArtifactPublicationError(RuntimeError):
    """Base class for an artifact publication refusal."""


class ArtifactNotPublished(ArtifactPublicationError):
    """Artifact bytes do not have the required committed manifest visibility."""


class ArtifactPublicationConflict(ArtifactPublicationError):
    """A durable artifact identity was reused with different canonical bytes."""


@dataclass(frozen=True, slots=True)
class PublicationReceipt:
    replayed: bool
    manifest_digest: str


class ArtifactPublicationRepository:
    def __init__(
        self,
        database_path: Path,
        *,
        now_ms: Callable[[], int],
        barrier: Callable[[str], None] | None = None,
    ) -> None:
        self._factory = SQLiteConnectionFactory(database_path)
        self._now_ms = now_ms
        self._barrier = barrier or (lambda _name: None)

    def stage_content(
        self,
        *,
        stage_id: str,
        binding: ArtifactBinding,
        content: bytes,
    ) -> ArtifactStage:
        binding_document = binding.model_dump(mode="json")
        content_bytes = bytes(content)
        content_digest = f"sha256:{hashlib.sha256(content_bytes).hexdigest()}"
        if binding_document["digest"] != content_digest:
            raise ArtifactPublicationConflict("binding digest does not match staged bytes")
        if binding_document["size_bytes"] != len(content_bytes):
            raise ArtifactPublicationConflict("binding size does not match staged bytes")

        with self._factory.transaction(write=True) as connection:
            existing = self._stage_row(connection, stage_id)
            if existing is not None:
                existing_binding = self._binding_from_row(existing)
                if (
                    existing_binding.model_dump(mode="json") != binding_document
                    or bytes(existing["content"]) != content_bytes
                ):
                    raise ArtifactPublicationConflict(
                        "stage id already binds different artifact bytes"
                    )
                return self._stage_model(existing, existing_binding)

            operation_id = binding_document["operation"]["value"]
            cell_execution_id = binding_document["cell_execution_id"]
            identity = connection.execute(
                """
                SELECT j.workspace_id, j.workspace_generation
                FROM rlm_workbench_cells AS c
                JOIN rlm_workbench_jobs AS j ON j.operation_id = c.operation_id
                WHERE c.operation_id=? AND c.cell_execution_id=?
                """,
                (operation_id, cell_execution_id),
            ).fetchone()
            if identity is None:
                raise ArtifactPublicationConflict("artifact cell identity is unknown")
            if (
                identity["workspace_id"] != binding_document["workspace"]["value"]
                or identity["workspace_generation"]
                != binding_document["workspace_generation"]
            ):
                raise ArtifactPublicationConflict("artifact workspace identity mismatch")

            now = self._now_ms()
            connection.execute(
                """
                INSERT INTO rlm_workbench_artifact_stages(
                    stage_id, operation_id, cell_execution_id, logical_name, role,
                    media_type, content_digest, size_bytes, content, state,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'staged', ?, ?)
                """,
                (
                    stage_id,
                    operation_id,
                    cell_execution_id,
                    binding_document["logical_name"],
                    binding_document["role"],
                    binding_document["media_type"],
                    content_digest,
                    len(content_bytes),
                    content_bytes,
                    now,
                    now,
                ),
            )
            row = self._stage_row(connection, stage_id)
            if row is None:
                raise ArtifactPublicationConflict("staged artifact insert was not visible")
            return self._stage_model(row, binding)

    def read_cell_visible(self, stage_id: str) -> bytes:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT content FROM rlm_workbench_artifact_stages "
                "WHERE stage_id=? AND state='committed'",
                (stage_id,),
            ).fetchone()
        if row is None:
            raise ArtifactNotPublished(stage_id)
        return bytes(row[0])

    def commit_cell(self, manifest: dict[str, Any]) -> PublicationReceipt:
        value = CellCommitManifest.model_validate(manifest, strict=True)
        document = value.model_dump(mode="json")
        operation_id = document["operation"]["value"]
        cell_execution_id = document["cell_execution_id"]
        manifest_json = canonical_json_bytes(document).decode("utf-8")
        manifest_digest = document["manifest_digest"]

        with self._factory.transaction(write=True) as connection:
            existing = connection.execute(
                """
                SELECT manifest_json, manifest_digest
                FROM rlm_workbench_cell_manifests
                WHERE operation_id=? AND cell_execution_id=?
                """,
                (operation_id, cell_execution_id),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["manifest_json"]) != manifest_json
                    or str(existing["manifest_digest"]) != manifest_digest
                ):
                    raise ArtifactPublicationConflict(
                        "cell manifest identity already binds different bytes"
                    )
                return PublicationReceipt(replayed=True, manifest_digest=manifest_digest)

            cell = connection.execute(
                """
                SELECT c.state, c.attempt_id, c.attempt_fence, c.workspace_id,
                       c.workspace_generation, c.pre_workspace_revision,
                       c.source_digest, j.phase
                FROM rlm_workbench_cells AS c
                JOIN rlm_workbench_jobs AS j ON j.operation_id = c.operation_id
                WHERE c.operation_id=? AND c.cell_execution_id=?
                """,
                (operation_id, cell_execution_id),
            ).fetchone()
            if cell is None:
                raise ArtifactPublicationConflict("cell manifest references unknown cell")
            expected = (
                (cell["state"], "running"),
                (cell["attempt_id"], document["attempt_id"]),
                (cell["attempt_fence"], document["attempt_fence"]),
                (cell["workspace_id"], document["workspace"]["value"]),
                (cell["workspace_generation"], document["workspace_generation"]),
                (cell["pre_workspace_revision"], document["pre_revision"]),
                (cell["source_digest"], document["source_digest"]),
                (cell["phase"], "running"),
            )
            if any(actual != wanted for actual, wanted in expected):
                raise ArtifactPublicationConflict("cell manifest authority mismatch")

            listed_stage_ids = tuple(document["artifact_stage_ids"])
            stage_rows = connection.execute(
                """
                SELECT stage_id, state FROM rlm_workbench_artifact_stages
                WHERE operation_id=? AND cell_execution_id=?
                ORDER BY stage_id
                """,
                (operation_id, cell_execution_id),
            ).fetchall()
            if tuple(row["stage_id"] for row in stage_rows) != tuple(
                sorted(listed_stage_ids)
            ) or any(row["state"] != "staged" for row in stage_rows):
                raise ArtifactPublicationConflict(
                    "cell manifest must bind the exact staged artifact set"
                )

            now = self._now_ms()
            connection.execute(
                """
                INSERT INTO rlm_workbench_cell_manifests(
                    operation_id, cell_execution_id, manifest_json,
                    manifest_digest, created_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (operation_id, cell_execution_id, manifest_json, manifest_digest, now),
            )
            self._barrier("cell_manifest_inserted")
            connection.executemany(
                """
                UPDATE rlm_workbench_artifact_stages
                SET state='committed', updated_at_unix_ms=?
                WHERE stage_id=? AND state='staged'
                """,
                ((now, stage_id) for stage_id in listed_stage_ids),
            )
            connection.execute(
                """
                UPDATE rlm_workbench_cells
                SET state='committed', post_workspace_revision=?,
                    post_checkpoint_digest=?, result_json=?, result_digest=?,
                    updated_at_unix_ms=?
                WHERE operation_id=? AND cell_execution_id=? AND state='running'
                """,
                (
                    document["post_revision"],
                    document["checkpoint_digest"],
                    canonical_json_bytes(
                        {"result_digest": document["result_digest"]}
                    ).decode("utf-8"),
                    document["result_digest"],
                    now,
                    operation_id,
                    cell_execution_id,
                ),
            )
        return PublicationReceipt(replayed=False, manifest_digest=manifest_digest)

    def cell_manifest(
        self,
        operation_id: str,
        cell_execution_id: str,
    ) -> CellCommitManifest | None:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT manifest_json FROM rlm_workbench_cell_manifests "
                "WHERE operation_id=? AND cell_execution_id=?",
                (operation_id, cell_execution_id),
            ).fetchone()
        if row is None:
            return None
        return CellCommitManifest.model_validate_json(str(row[0]), strict=True)

    def finalize(
        self,
        manifest: dict[str, Any],
        *,
        result: dict[str, Any],
    ) -> PublicationReceipt:
        value = FinalizationManifest.model_validate(manifest, strict=True)
        document = value.model_dump(mode="json")
        operation_id = document["operation"]["value"]
        manifest_json = canonical_json_bytes(document).decode("utf-8")
        manifest_digest = document["manifest_digest"]
        result_json = canonical_json_bytes(result).decode("utf-8")
        if result.get("result_digest") != document["result_digest"]:
            raise ArtifactPublicationConflict("result digest does not match finalization")

        with self._factory.transaction(write=True) as connection:
            existing = connection.execute(
                """
                SELECT manifest_json, manifest_digest
                FROM rlm_workbench_finalization_manifests
                WHERE operation_id=?
                """,
                (operation_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["manifest_json"]) != manifest_json
                    or str(existing["manifest_digest"]) != manifest_digest
                ):
                    raise ArtifactPublicationConflict(
                        "finalization identity already binds different bytes"
                    )
                return PublicationReceipt(replayed=True, manifest_digest=manifest_digest)

            job = connection.execute(
                """
                SELECT phase, control_revision, cancellation_revision,
                       cumulative_deadline_unix_ms
                FROM rlm_workbench_jobs WHERE operation_id=?
                """,
                (operation_id,),
            ).fetchone()
            if job is None or (
                job["phase"] != document["expected_prior_phase"]
                or job["control_revision"] != document["control_revision"]
                or job["cancellation_revision"]
                != document["expected_cancellation_revision"]
                or job["cumulative_deadline_unix_ms"]
                != document["expected_deadline_unix_ms"]
            ):
                raise ArtifactPublicationConflict("finalization job authority mismatch")

            attempt = connection.execute(
                "SELECT 1 FROM operation_attempts WHERE operation_id=? AND attempt_id=?",
                (operation_id, document["finalizer_attempt_id"]),
            ).fetchone()
            if attempt is None:
                raise ArtifactPublicationConflict("finalizer attempt is not authoritative")

            unresolved = connection.execute(
                """
                SELECT COUNT(*) FROM caller_work_tickets
                WHERE operation_id=? AND state NOT IN (
                    'settled_success', 'settled_failure', 'cancelled_before_send',
                    'cancelled_certain', 'quarantined'
                )
                """,
                (operation_id,),
            ).fetchone()[0]
            if unresolved != document["unresolved_required_ticket_count"]:
                raise ArtifactPublicationConflict(
                    "finalization unresolved caller-work count mismatch"
                )

            bindings = self._committed_bindings(connection, operation_id)
            binding_digests = tuple(
                canonical_sha256(binding.model_dump(mode="json")) for binding in bindings
            )
            if binding_digests != tuple(document["artifact_binding_digests"]):
                raise ArtifactPublicationConflict(
                    "finalization artifact binding set mismatch"
                )

            now = self._now_ms()
            proposal_digest = canonical_sha256(
                {"manifest_digest": manifest_digest, "result": result}
            )
            connection.execute(
                """
                INSERT INTO rlm_workbench_finalization_manifests(
                    operation_id, finalizer_attempt_id, finalizer_attempt_fence,
                    expected_prior_phase, expected_cancellation_revision,
                    expected_cumulative_deadline_unix_ms, control_revision,
                    unresolved_required_ticket_count, proposal_digest,
                    result_digest, manifest_json, manifest_digest,
                    committed_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    document["finalizer_attempt_id"],
                    document["finalizer_attempt_fence"],
                    document["expected_prior_phase"],
                    document["expected_cancellation_revision"],
                    document["expected_deadline_unix_ms"],
                    document["control_revision"],
                    document["unresolved_required_ticket_count"],
                    proposal_digest,
                    document["result_digest"],
                    manifest_json,
                    manifest_digest,
                    now,
                ),
            )
            self._barrier("finalization_manifest_inserted")
            connection.execute(
                """
                UPDATE rlm_workbench_jobs
                SET phase='succeeded', result_json=?, result_digest=?,
                    updated_at_unix_ms=?
                WHERE operation_id=? AND phase='finalizing'
                """,
                (result_json, document["result_digest"], now, operation_id),
            )
        return PublicationReceipt(replayed=False, manifest_digest=manifest_digest)

    def finalization_manifest(self, operation_id: str) -> FinalizationManifest | None:
        with self._factory.transaction(write=False) as connection:
            row = connection.execute(
                "SELECT manifest_json FROM rlm_workbench_finalization_manifests "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
        if row is None:
            return None
        return FinalizationManifest.model_validate_json(str(row[0]), strict=True)

    def list_final_bindings(self, operation_id: str) -> tuple[ArtifactBinding, ...]:
        with self._factory.transaction(write=False) as connection:
            finalized = connection.execute(
                "SELECT manifest_json FROM rlm_workbench_finalization_manifests "
                "WHERE operation_id=?",
                (operation_id,),
            ).fetchone()
            if finalized is None:
                return ()
            manifest = FinalizationManifest.model_validate_json(
                str(finalized[0]), strict=True
            )
            expected = set(manifest.artifact_binding_digests)
            bindings = self._committed_bindings(connection, operation_id)
            return tuple(
                binding
                for binding in bindings
                if canonical_sha256(binding.model_dump(mode="json")) in expected
            )

    def close(self) -> None:
        self._factory.close()

    @staticmethod
    def _stage_row(connection: Any, stage_id: str) -> Any:
        return connection.execute(
            """
            SELECT s.*, j.workspace_id, j.workspace_generation
            FROM rlm_workbench_artifact_stages AS s
            JOIN rlm_workbench_jobs AS j ON j.operation_id = s.operation_id
            WHERE s.stage_id=?
            """,
            (stage_id,),
        ).fetchone()

    @staticmethod
    def _binding_from_row(row: Any) -> ArtifactBinding:
        return ArtifactBinding.model_validate(
            {
                "schema_version": "aar.artifact-binding.v1",
                "logical_name": row["logical_name"],
                "media_type": row["media_type"],
                "digest": row["content_digest"],
                "size_bytes": row["size_bytes"],
                "operation": {"type": "operation", "value": row["operation_id"]},
                "workspace": {"type": "workspace", "value": row["workspace_id"]},
                "workspace_generation": row["workspace_generation"],
                "cell_execution_id": row["cell_execution_id"],
                "role": row["role"],
            },
            strict=True,
        )

    @staticmethod
    def _stage_model(row: Any, binding: ArtifactBinding) -> ArtifactStage:
        return ArtifactStage.model_validate(
            {
                "schema_version": "aar.artifact-stage.v1",
                "stage_id": row["stage_id"],
                "binding": binding.model_dump(mode="json"),
                "content_digest": row["content_digest"],
                "state": row["state"],
            },
            strict=True,
        )

    @classmethod
    def _committed_bindings(
        cls,
        connection: Any,
        operation_id: str,
    ) -> tuple[ArtifactBinding, ...]:
        rows = connection.execute(
            """
            SELECT s.*, j.workspace_id, j.workspace_generation
            FROM rlm_workbench_artifact_stages AS s
            JOIN rlm_workbench_jobs AS j ON j.operation_id = s.operation_id
            WHERE s.operation_id=? AND s.state='committed'
            ORDER BY s.logical_name
            """,
            (operation_id,),
        ).fetchall()
        return tuple(cls._binding_from_row(row) for row in rows)
