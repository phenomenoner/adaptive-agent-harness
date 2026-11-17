"""Atomic staged-artifact publication for AR-RW cell and finalization manifests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from aar.broker_models import ModelResponse, ModelUsageRecord
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_workbench_models import (
    ArtifactBinding,
    ArtifactStage,
    CandidateReceipt,
    CellCommitManifest,
    FinalizationManifest,
    RlmWorkbenchJobSpec,
    RlmWorkbenchResult,
)
from aar.runtime.sqlite_repository import SQLiteConnectionFactory
from aar.schemas import ArtifactIdRef, ArtifactReference


class ArtifactPublicationError(RuntimeError):
    """Base class for an artifact publication refusal."""


class ArtifactNotPublished(ArtifactPublicationError):
    """Artifact bytes do not have the required committed manifest visibility."""


class ArtifactPublicationConflict(ArtifactPublicationError):
    """A durable artifact identity was reused with different canonical bytes."""


def read_caller_planner_trace(
    connection: Any,
    operation_id: str,
) -> tuple[int, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...], int, int]:
    """Existing exact planner accounting projection, under the caller's transaction."""
    rows = connection.execute(
        """
        SELECT suspension.suspension_revision, suspension.ticket_id,
               ticket.state, ticket.settled_receipt_digest,
               model.state AS model_state, model.usage_json,
               model.response_json, model.response_digest
        FROM rlm_workbench_suspensions AS suspension
        JOIN caller_work_tickets AS ticket ON ticket.ticket_id = suspension.ticket_id
        LEFT JOIN model_executions AS model
          ON model.operation_id = suspension.operation_id
         AND model.idempotency_key = ticket.external_idempotency_key
         AND model.request_digest = ticket.request_digest
        WHERE suspension.operation_id = ? AND suspension.cell_execution_id IS NULL
        ORDER BY suspension.suspension_revision
        """,
        (operation_id,),
    ).fetchall()
    directive_rows = connection.execute(
        "SELECT payload_json FROM operation_events "
        "WHERE operation_id=? AND event_kind='planner_successor_consumed' ORDER BY sequence",
        (operation_id,),
    ).fetchall()
    if not rows:
        raise ArtifactPublicationConflict("planner trace is incomplete")
    directive_digests = []
    for row in directive_rows:
        digest = json.loads(str(row["payload_json"])).get("directive_digest")
        if not isinstance(digest, str):
            raise ArtifactPublicationConflict("planner directive digest is unavailable")
        directive_digests.append(digest)
    usage_receipt_digests = []
    broker_trace = []
    input_tokens = output_tokens = 0
    for row in rows:
        if str(row["state"]) != "settled_success":
            raise ArtifactPublicationConflict("planner trace includes a non-success settlement")
        settlement_digest = str(row["settled_receipt_digest"])
        receipt_rows = connection.execute(
            "SELECT receipt_json FROM caller_work_candidate_receipts "
            "WHERE ticket_id=? ORDER BY observed_at_unix_ms, receipt_digest",
            (str(row["ticket_id"]),),
        ).fetchall()
        try:
            candidates = tuple(
                CandidateReceipt.model_validate_json(str(item[0]), strict=True)
                for item in receipt_rows
            )
        except (ValueError, TypeError) as exc:
            raise ArtifactPublicationConflict(
                "planner trace settlement receipt is malformed"
            ) from exc
        receipts = tuple(
            receipt
            for receipt in candidates
            if receipt.receipt_digest == settlement_digest
            and receipt.root["ticket_id"] == str(row["ticket_id"])
        )
        if len(receipts) != 1:
            raise ArtifactPublicationConflict("planner trace settlement receipt is not unique")
        observation = dict(receipts[0].root["observation"])
        required = {
            name: observation.get(name)
            for name in (
                "route_receipt_digest",
                "usage_receipt_digest",
                "host_receipt_digest",
                "output_digest",
            )
        }
        if observation.get("kind") != "model" or not all(
            isinstance(v, str) for v in required.values()
        ):
            raise ArtifactPublicationConflict("planner trace model receipt is incomplete")
        try:
            usage = ModelUsageRecord.model_validate_json(str(row["usage_json"]), strict=True)
            response = ModelResponse.model_validate_json(str(row["response_json"]), strict=True)
            if (
                row["model_state"] != "result_committed"
                or response.usage != usage
                or canonical_sha256(usage) != required["usage_receipt_digest"]
                or canonical_sha256(response) != row["response_digest"]
                or row["response_digest"] != required["host_receipt_digest"]
                or response.route_receipt.receipt_digest != required["route_receipt_digest"]
                or "sha256:" + hashlib.sha256(response.output_text.encode()).hexdigest()
                != required["output_digest"]
            ):
                raise ValueError("planner usage is not bound to its settled response")
        except (ValueError, TypeError) as exc:
            raise ArtifactPublicationConflict(
                "planner usage receipt does not match its model journal"
            ) from exc
        # Equal usage digests on distinct tickets remain distinct committed executions.
        input_tokens += usage.input_tokens
        output_tokens += usage.output_tokens
        usage_receipt_digests.append(str(required["usage_receipt_digest"]))
        broker_trace.append(
            {
                "suspension_revision": int(row["suspension_revision"]),
                "ticket_id": str(row["ticket_id"]),
                "settlement_digest": settlement_digest,
                **required,
            }
        )
    return (
        len(rows),
        tuple(directive_digests),
        tuple(usage_receipt_digests),
        tuple(broker_trace),
        input_tokens,
        output_tokens,
    )


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
                or identity["workspace_generation"] != binding_document["workspace_generation"]
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
            job = connection.execute(
                "SELECT * FROM rlm_workbench_jobs WHERE operation_id=?", (operation_id,)
            ).fetchone()
            lease = None
            if (
                connection.execute(
                    "SELECT 1 FROM operation_dispatch WHERE operation_id=?", (operation_id,)
                ).fetchone()
                is not None
                or json.loads(str(job["spec_json"])).get("schema_version")
                == "aar.rlm-workbench-job.v1"
            ):
                job, lease = self.current_native_cell_authority(
                    connection,
                    operation_id,
                    document["attempt_id"],
                    document["attempt_fence"],
                    now_unix_ms=self._now_ms(),
                )

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

            if lease is not None:
                self._barrier("cell_commit_validated")
            now = self._now_ms()
            if lease is not None:
                self.current_native_cell_authority(
                    connection,
                    operation_id,
                    document["attempt_id"],
                    document["attempt_fence"],
                    now_unix_ms=now,
                )
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
                    canonical_json_bytes({"result_digest": document["result_digest"]}).decode(
                        "utf-8"
                    ),
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
        with self._factory.transaction(write=True) as connection:
            return self.finalize_in_transaction(connection, manifest, result=result)

    def finalize_in_transaction(
        self,
        connection: Any,
        manifest: dict[str, Any],
        *,
        result: dict[str, Any],
        proposal_digest: str | None = None,
    ) -> PublicationReceipt:
        """One publication writer; an existing write transaction owns check and effect.

        Native jobs are identified from their persisted job contract/dispatch, never
        from a caller-selected mode. Their selected bindings remain distinct from
        the standalone repository API's historical all-committed binding policy.
        """
        if not connection.in_transaction:
            raise ArtifactPublicationConflict("finalization requires an owning transaction")
        value = FinalizationManifest.model_validate(manifest, strict=True)
        document = value.model_dump(mode="json")
        operation_id = document["operation"]["value"]
        manifest_json = canonical_json_bytes(document).decode("utf-8")
        manifest_digest = document["manifest_digest"]
        result_json = canonical_json_bytes(result).decode("utf-8")
        if result.get("result_digest") != document["result_digest"]:
            raise ArtifactPublicationConflict("result digest does not match finalization")
        job = connection.execute(
            "SELECT * FROM rlm_workbench_jobs WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if job is None:
            raise ArtifactPublicationConflict("finalization job is unknown")
        spec = json.loads(str(job["spec_json"]))
        dispatch = connection.execute(
            "SELECT state, current_attempt_no FROM operation_dispatch WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        native = dispatch is not None or spec.get("schema_version") == "aar.rlm-workbench-job.v1"
        if native:
            result = RlmWorkbenchResult.model_validate(result, strict=True).root
            spec = RlmWorkbenchJobSpec.model_validate(spec, strict=True).root
            if (
                result["operation"] != document["operation"]
                or canonical_sha256({k: v for k, v in result.items() if k != "result_digest"})
                != document["result_digest"]
                or canonical_sha256(result["output"]) != result["output_digest"]
                or canonical_sha256(spec) != job["spec_digest"]
                or document["usage_digest"] != canonical_sha256(result["usage_receipt_digests"])
                or document["child_policy_digest"] != canonical_sha256(result["child_lineage"])
                or document["workspace_disposition_digest"]
                != canonical_sha256(
                    {
                        "workspace_disposition": result["workspace_disposition"],
                        "checkpoint": result["final_checkpoint_digest"],
                    }
                )
                or result["terminal_status"] != "succeeded"
                or result["workspace_disposition"] != "closed"
                or result["unresolved_required_children"] != 0
            ):
                raise ArtifactPublicationConflict("finalization result binding mismatch")
            contract = spec["completion"]["output_contract"]
            if len(canonical_json_bytes(result["output"])) > contract[
                "max_instance_bytes"
            ] or not Draft202012Validator(contract["schema"]).is_valid(result["output"]):
                raise ArtifactPublicationConflict("finalization result output contract mismatch")
            if spec["completion"]["require_named_artifacts"] and not result["artifact_bindings"]:
                raise ArtifactPublicationConflict("finalization result requires named artifacts")

        existing = connection.execute(
            "SELECT manifest_json, manifest_digest FROM rlm_workbench_finalization_manifests "
            "WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        if existing is not None:
            if (
                str(existing["manifest_json"]) != manifest_json
                or str(existing["manifest_digest"]) != manifest_digest
                or job["phase"] != "succeeded"
                or job["result_json"] != result_json
                or job["result_digest"] != document["result_digest"]
            ):
                raise ArtifactPublicationConflict(
                    "finalization replay result or manifest bytes changed"
                )
            return PublicationReceipt(replayed=True, manifest_digest=manifest_digest)

        if (
            job["phase"] != document["expected_prior_phase"]
            or job["control_revision"] != document["control_revision"]
            or job["cancellation_requested"]
            or job["cancellation_revision"] != document["expected_cancellation_revision"]
            or job["cumulative_deadline_unix_ms"] != document["expected_deadline_unix_ms"]
        ):
            raise ArtifactPublicationConflict("finalization job authority mismatch")
        attempt = connection.execute(
            "SELECT * FROM operation_attempts WHERE operation_id=? AND attempt_id=?",
            (operation_id, document["finalizer_attempt_id"]),
        ).fetchone()
        if attempt is None:
            raise ArtifactPublicationConflict("finalizer attempt is not authoritative")

        unresolved = connection.execute(
            "SELECT COUNT(*) FROM caller_work_tickets WHERE operation_id=? AND state NOT IN ("
            "'settled_success','settled_failure','cancelled_before_send','cancelled_certain','quarantined')",
            (operation_id,),
        ).fetchone()[0]
        pending = connection.execute(
            "SELECT COUNT(*) FROM rlm_workbench_suspensions WHERE "
            "operation_id=? AND state='pending'",
            (operation_id,),
        ).fetchone()[0]
        if unresolved or pending or document["unresolved_required_ticket_count"]:
            raise ArtifactPublicationConflict("finalization has unresolved required tickets")

        bindings = self._committed_bindings(connection, operation_id)
        all_digests = tuple(canonical_sha256(b.model_dump(mode="json")) for b in bindings)
        listed = tuple(document["artifact_binding_digests"])
        if native:
            selected = tuple(canonical_sha256(b) for b in result["artifact_bindings"])
            names = tuple(b["logical_name"] for b in result["artifact_bindings"])
            valid_bindings = (
                selected == listed
                and len(selected) == len(set(selected))
                and len(names) == len(set(names))
                and all(d in all_digests for d in selected)
            )
        else:
            valid_bindings = all_digests == listed
        if not valid_bindings:
            raise ArtifactPublicationConflict("finalization artifact binding set mismatch")

        if native and spec["model"]["execution_mode"] == "caller_delegated":
            calls, directives, usage, trace, inputs, outputs = read_caller_planner_trace(
                connection, operation_id
            )
            cells = connection.execute(
                "SELECT manifest_digest FROM rlm_workbench_cell_manifests "
                "WHERE operation_id=? ORDER BY cell_execution_id",
                (operation_id,),
            ).fetchall()
            expected_accounting = {
                "model_calls": calls,
                "input_tokens": inputs,
                "output_tokens": outputs,
                "usage_receipt_digests": list(usage),
                "broker_trace_digest": canonical_sha256(trace),
                "step_trace_digest": canonical_sha256(
                    {
                        "directives": directives,
                        "cell_manifests": tuple(str(row[0]) for row in cells),
                    }
                ),
            }
            if any(result[key] != expected for key, expected in expected_accounting.items()):
                raise ArtifactPublicationConflict(
                    "native caller accounting differs from settled journal"
                )

        lease_expiry = None
        if native:
            lease = self._current_native_lease(
                connection,
                operation_id,
                document["finalizer_attempt_id"],
                document["finalizer_attempt_fence"],
            )
            if (
                lease["cancellation_requested"]
                or lease["control_revision"] != document["control_revision"]
            ):
                raise ArtifactPublicationConflict("native finalizer control changed")
            lease_expiry = int(lease["expires_at_unix_ms"])

        self._barrier("finalization_validated")
        now = self._now_ms()
        if now >= document["expected_deadline_unix_ms"] or (
            lease_expiry is not None and now >= lease_expiry
        ):
            raise ArtifactPublicationConflict(
                "finalization deadline or lease expired before commit"
            )
        revision = int(job["control_revision"]) + 1
        connection.execute(
            "INSERT INTO rlm_workbench_finalization_manifests("
            "operation_id, finalizer_attempt_id, finalizer_attempt_fence, expected_prior_phase, "
            "expected_cancellation_revision, "
            "expected_cumulative_deadline_unix_ms, control_revision, "
            "unresolved_required_ticket_count, proposal_digest, result_digest, manifest_json, "
            "manifest_digest, committed_at_unix_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                operation_id,
                document["finalizer_attempt_id"],
                document["finalizer_attempt_fence"],
                document["expected_prior_phase"],
                document["expected_cancellation_revision"],
                document["expected_deadline_unix_ms"],
                revision,
                0,
                proposal_digest
                or canonical_sha256({"manifest_digest": manifest_digest, "result": result}),
                document["result_digest"],
                manifest_json,
                manifest_digest,
                now,
            ),
        )
        self._barrier("finalization_manifest_inserted")
        updated = connection.execute(
            "UPDATE rlm_workbench_jobs SET phase='succeeded', control_revision=?, result_json=?, "
            "result_digest=?, checkpoint_digest=COALESCE("
            "?,checkpoint_digest), updated_at_unix_ms=? "
            "WHERE operation_id=? AND phase='finalizing' AND control_revision=? "
            "AND cancellation_requested=0 AND cancellation_revision=?",
            (
                revision,
                result_json,
                document["result_digest"],
                result.get("final_checkpoint_digest"),
                now,
                operation_id,
                document["control_revision"],
                document["expected_cancellation_revision"],
            ),
        )
        if updated.rowcount != 1:
            raise ArtifactPublicationConflict("finalization job compare-and-set failed")
        if native:
            updated = connection.execute(
                "UPDATE operation_controls SET control_revision=? WHERE operation_id=? "
                "AND control_revision=? AND cancellation_requested=0",
                (revision, operation_id, document["control_revision"]),
            )
            if updated.rowcount != 1:
                raise ArtifactPublicationConflict("finalization control compare-and-set failed")
        return PublicationReceipt(replayed=False, manifest_digest=manifest_digest)

    @staticmethod
    def current_native_cell_authority(
        connection: Any,
        operation_id: str,
        attempt_id: str,
        fence_digest: str,
        *,
        now_unix_ms: int,
    ) -> tuple[Any, Any]:
        """Read existing native cell authority in the caller's write transaction.

        No grant or write is produced here. The cell owner holds this same
        transaction through promotion; resumed and ordinary native cells share
        the publication repository's existing fence/control/deadline policy.
        """
        job = connection.execute(
            "SELECT * FROM rlm_workbench_jobs WHERE operation_id=? AND phase='running'",
            (operation_id,),
        ).fetchone()
        if job is None:
            raise ArtifactPublicationConflict("native cell job is not running")
        lease = ArtifactPublicationRepository._current_native_lease(
            connection, operation_id, attempt_id, fence_digest
        )
        if (
            job["cancellation_requested"]
            or lease["cancellation_requested"]
            or job["control_revision"] != lease["control_revision"]
        ):
            raise ArtifactPublicationConflict("native cell control changed or cancelled")
        if (
            now_unix_ms >= lease["expires_at_unix_ms"]
            or now_unix_ms >= job["cumulative_deadline_unix_ms"]
        ):
            raise ArtifactPublicationConflict("native cell deadline or lease expired before commit")
        return job, lease

    @staticmethod
    def _current_native_lease(
        connection: Any,
        operation_id: str,
        attempt_id: str,
        fence_digest: str,
    ) -> Any:
        row = connection.execute(
            "SELECT lease.expires_at_unix_ms, lease.lease_epoch, lease.owner_digest, "
            "attempt.dispatcher_generation, control.control_revision, "
            "control.cancellation_requested "
            "FROM operation_attempts AS attempt "
            "JOIN operation_dispatch AS dispatch USING(operation_id) "
            "JOIN operation_leases AS lease ON lease.operation_id=attempt.operation_id "
            "AND lease.attempt_no=attempt.attempt_no "
            "JOIN operation_controls AS control ON control.operation_id=attempt.operation_id "
            "JOIN operations AS op ON op.operation_id=attempt.operation_id "
            "WHERE attempt.operation_id=? AND attempt.attempt_id=? AND attempt.state='running' "
            "AND dispatch.state='running' AND dispatch.current_attempt_no=attempt.attempt_no "
            "AND op.state='running' AND op.runtime_generation=attempt.runtime_generation "
            "AND lease.released_at_unix_ms IS NULL",
            (operation_id, attempt_id),
        ).fetchone()
        if (
            row is None
            or canonical_sha256(
                {
                    "dispatcher_generation": row["dispatcher_generation"],
                    "lease_epoch": row["lease_epoch"],
                    "owner_digest": row["owner_digest"],
                }
            )
            != fence_digest
        ):
            raise ArtifactPublicationConflict("native attempt fence or ownership is stale")
        return row

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
            manifest = FinalizationManifest.model_validate_json(str(finalized[0]), strict=True)
            expected = set(manifest.artifact_binding_digests)
            bindings = self._committed_bindings(connection, operation_id)
            return tuple(
                binding
                for binding in bindings
                if canonical_sha256(binding.model_dump(mode="json")) in expected
            )

    def resolve_public_reference(
        self,
        reference: ArtifactReference,
    ) -> bytes | None:
        """Resolve native bytes by operation; None means a non-native operation.

        A missing native publication is a refusal, never a legacy fallback.
        Operation, finalization, binding and bytes are read in one snapshot.
        """
        with self._factory.transaction(write=False) as connection:
            native = connection.execute(
                "SELECT 1 FROM rlm_workbench_jobs WHERE operation_id = ?",
                (reference.created_by.value,),
            ).fetchone()
            if native is None:
                return None
            rows = connection.execute(
                """
                SELECT s.*, j.workspace_id, j.workspace_generation, f.manifest_json
                FROM rlm_workbench_artifact_stages AS s
                JOIN rlm_workbench_jobs AS j ON j.operation_id = s.operation_id
                JOIN rlm_workbench_finalization_manifests AS f
                  ON f.operation_id = s.operation_id
                WHERE s.operation_id = ? AND s.content_digest = ?
                  AND s.state = 'committed' AND j.phase = 'succeeded'
                """,
                (reference.created_by.value, reference.digest),
            ).fetchall()
            for row in rows:
                binding = self._binding_from_row(row)
                manifest = FinalizationManifest.model_validate_json(
                    str(row["manifest_json"]), strict=True
                )
                if (
                    canonical_sha256(binding.model_dump(mode="json"))
                    not in manifest.artifact_binding_digests
                ):
                    continue
                document = binding.model_dump(mode="json")
                expected_artifact = ArtifactIdRef(
                    value="artifact-" + str(document["digest"]).removeprefix("sha256:")[:32]
                )
                if (
                    reference.artifact == expected_artifact
                    and reference.media_type == document["media_type"]
                    and reference.size_bytes == document["size_bytes"]
                    and not reference.redacted
                ):
                    content = bytes(row["content"])
                    if (
                        len(content) != reference.size_bytes
                        or "sha256:" + hashlib.sha256(content).hexdigest() != reference.digest
                    ):
                        raise ArtifactPublicationConflict(
                            "native artifact content integrity mismatch"
                        )
                    return content
        raise KeyError(reference.digest)

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
