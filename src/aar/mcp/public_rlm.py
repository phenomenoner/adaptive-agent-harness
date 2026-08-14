"""Durable caller-delegated RLM coordination for the curated public MCP surface.

This module never invokes a model provider. It persists an immutable job-level model route and exact
call specification, lets the authenticated caller claim a pre-spend ticket before execution, and
atomically commits the caller's bounded observation before advancing the deterministic strategy.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp.public_rlm_models import (
    PublicRlmCallerReceipt,
    PublicRlmCallSpec,
    PublicRlmClaimRequest,
    PublicRlmCommitRequest,
    PublicRlmExecutionTicket,
    PublicRlmJobSpec,
    PublicRlmJobView,
    PublicRlmStepSummary,
    PublicRlmTerminalResult,
    PublicRlmToolResult,
)
from aar.schemas import OutcomeCertainty

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{0,127}$")


class PublicRlmError(RuntimeError):
    """Base class for public caller-delegated RLM contract failures."""


class PublicRlmNotFound(PublicRlmError):
    """The requested tenant-private RLM job does not exist."""


class PublicRlmConflict(PublicRlmError):
    """An identity, revision, ticket, or idempotency binding conflicts."""


class PublicRlmInvalidTransition(PublicRlmError):
    """The requested command is invalid from the durable job phase."""


class PublicRlmCapacityExceeded(PublicRlmError):
    """The tenant reached a configured retained RLM capacity limit."""


class PublicRlmCoordinator:
    """Persist and advance one tenant's caller-delegated public RLM jobs."""

    def __init__(
        self,
        database_path: Path,
        *,
        principal_id: str,
        now_ms: Callable[[], int],
        max_jobs: int = 256,
    ) -> None:
        if not principal_id:
            raise ValueError("public RLM principal identity is required")
        if max_jobs < 1 or max_jobs > 100_000:
            raise ValueError("public RLM max_jobs must be between 1 and 100000")
        self._principal_id = principal_id
        self._now_ms = now_ms
        self._max_jobs = max_jobs
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path.resolve(),
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA busy_timeout=5000")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS public_rlm_jobs (
                job_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                start_idempotency_key TEXT NOT NULL,
                start_request_digest TEXT NOT NULL,
                start_response_json TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                phase TEXT NOT NULL,
                certainty TEXT NOT NULL,
                revision INTEGER NOT NULL,
                terminal_result_json TEXT,
                failure_code TEXT,
                failure_message TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                UNIQUE(principal_id, start_idempotency_key)
            );

            CREATE TABLE IF NOT EXISTS public_rlm_calls (
                job_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                call_id TEXT NOT NULL UNIQUE,
                state TEXT NOT NULL,
                call_spec_json TEXT NOT NULL,
                ticket_json TEXT,
                caller_receipt_json TEXT,
                step_summary_json TEXT,
                created_at_unix_ms INTEGER NOT NULL,
                updated_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY(job_id, step_index),
                FOREIGN KEY(job_id) REFERENCES public_rlm_jobs(job_id)
            );

            CREATE TABLE IF NOT EXISTS public_rlm_commands (
                job_id TEXT NOT NULL,
                command_kind TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                response_json TEXT NOT NULL,
                created_at_unix_ms INTEGER NOT NULL,
                PRIMARY KEY(job_id, command_kind, idempotency_key),
                FOREIGN KEY(job_id) REFERENCES public_rlm_jobs(job_id)
            );
            """
        )

    @contextmanager
    def _write(self) -> Iterator[None]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            else:
                self._connection.execute("COMMIT")

    @staticmethod
    def _validate_identity(value: str, label: str) -> None:
        if not _IDENTITY.fullmatch(value):
            raise ValueError(f"{label} is not a bounded public identity")

    def _job_id(self, idempotency_key: str) -> str:
        material = f"{self._principal_id}\0{idempotency_key}".encode()
        return f"rlm-{hashlib.sha256(material).hexdigest()[:32]}"

    def _job_row(self, job_id: str) -> sqlite3.Row:
        row = self._connection.execute(
            """
            SELECT * FROM public_rlm_jobs
            WHERE job_id = ? AND principal_id = ?
            """,
            (job_id, self._principal_id),
        ).fetchone()
        if row is None:
            raise PublicRlmNotFound("public RLM job was not found for this tenant")
        return row

    @staticmethod
    def _assert_revision(row: sqlite3.Row, expected_revision: int) -> None:
        if int(row["revision"]) != expected_revision:
            raise PublicRlmConflict(
                "public RLM job revision changed; read status before retrying"
            )

    def _command_replay(
        self,
        *,
        job_id: str,
        command_kind: str,
        idempotency_key: str,
        request_digest: str,
    ) -> PublicRlmToolResult | None:
        row = self._connection.execute(
            """
            SELECT request_digest, response_json FROM public_rlm_commands
            WHERE job_id = ? AND command_kind = ? AND idempotency_key = ?
            """,
            (job_id, command_kind, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        if str(row["request_digest"]) != request_digest:
            raise PublicRlmConflict(
                "public RLM idempotency key is already bound to different request bytes"
            )
        return PublicRlmToolResult(job=self._view(self._job_row(job_id)))

    def _save_command(
        self,
        *,
        job_id: str,
        command_kind: str,
        idempotency_key: str,
        request_digest: str,
        response: PublicRlmToolResult,
    ) -> None:
        job = self._job_row(job_id)
        spec = PublicRlmJobSpec.model_validate_json(str(job["spec_json"]), strict=True)
        command_limit = 2 * spec.max_model_calls + 1
        command_count = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM public_rlm_commands WHERE job_id = ?",
                (job_id,),
            ).fetchone()[0]
        )
        if command_count >= command_limit:
            raise PublicRlmCapacityExceeded(
                "public RLM command history reached the job bound"
            )
        response_marker = {
            "schema_version": "aar.public-rlm-command-response.v1",
            "response_digest": canonical_sha256(response),
        }
        self._connection.execute(
            """
            INSERT INTO public_rlm_commands(
                job_id, command_kind, idempotency_key, request_digest,
                response_json, created_at_unix_ms
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                command_kind,
                idempotency_key,
                request_digest,
                canonical_json_bytes(response_marker).decode(),
                self._now_ms(),
            ),
        )

    def _call_rows(self, job_id: str) -> tuple[sqlite3.Row, ...]:
        return tuple(
            self._connection.execute(
                """
                SELECT * FROM public_rlm_calls
                WHERE job_id = ? ORDER BY step_index
                """,
                (job_id,),
            ).fetchall()
        )

    def _view(self, row: sqlite3.Row | None = None) -> PublicRlmJobView:
        job = self._job_row(str(row["job_id"])) if row is not None else None
        if job is None:  # pragma: no cover - callers always supply a row
            raise AssertionError("public RLM job row is required")
        job_id = str(job["job_id"])
        phase = str(job["phase"])
        calls = self._call_rows(job_id)
        pending_call = None
        active_ticket = None
        steps: list[PublicRlmStepSummary] = []
        for call in calls:
            if call["state"] == "pending":
                pending_call = PublicRlmCallSpec.model_validate_json(
                    str(call["call_spec_json"]), strict=True
                )
            if call["step_summary_json"] is not None:
                steps.append(
                    PublicRlmStepSummary.model_validate_json(
                        str(call["step_summary_json"]), strict=True
                    )
                )
            if call["ticket_json"] is not None and phase in {
                "awaiting_caller_result",
                "cancel_requested",
                "indeterminate",
            }:
                active_ticket = PublicRlmExecutionTicket.model_validate_json(
                    str(call["ticket_json"]), strict=True
                )
        result = (
            None
            if job["terminal_result_json"] is None
            else PublicRlmTerminalResult.model_validate_json(
                str(job["terminal_result_json"]), strict=True
            )
        )
        return PublicRlmJobView.issue(
            job_id=job_id,
            phase=phase,
            certainty=OutcomeCertainty(str(job["certainty"])),
            revision=int(job["revision"]),
            spec=PublicRlmJobSpec.model_validate_json(str(job["spec_json"]), strict=True),
            calls_committed=len(steps),
            pending_call=pending_call,
            active_ticket=active_ticket,
            steps=tuple(steps),
            result=result,
            failure_code=(
                None if job["failure_code"] is None else str(job["failure_code"])
            ),
            failure_message=(
                None if job["failure_message"] is None else str(job["failure_message"])
            ),
        )

    @staticmethod
    def _prompt(
        spec: PublicRlmJobSpec,
        *,
        step_index: int,
        previous_output: str | None = None,
    ) -> str:
        if spec.strategy == "single_call":
            if step_index != 0 or previous_output is not None:
                raise PublicRlmInvalidTransition("single-call strategy is already complete")
            return spec.query
        if step_index == 0:
            return (
                "Task:\n"
                f"{spec.query}\n\n"
                "Produce a complete draft answer. Return only the answer."
            )
        if previous_output is None:
            raise PublicRlmInvalidTransition(
                "iterative refinement requires the previous committed model output"
            )
        return (
            "Task:\n"
            f"{spec.query}\n\n"
            "Previous answer:\n"
            f"{previous_output}\n\n"
            "Improve correctness, completeness, and clarity. Return only the revised answer."
        )

    @classmethod
    def _call_spec(
        cls,
        job_id: str,
        spec: PublicRlmJobSpec,
        *,
        step_index: int,
        previous_output: str | None = None,
    ) -> PublicRlmCallSpec:
        return PublicRlmCallSpec.issue(
            job_id=job_id,
            step_index=step_index,
            strategy=spec.strategy,
            prompt=cls._prompt(
                spec,
                step_index=step_index,
                previous_output=previous_output,
            ),
            max_output_tokens=spec.max_output_tokens_per_call,
            max_result_bytes=spec.max_result_bytes_per_call,
        )

    def start(
        self,
        spec: PublicRlmJobSpec,
        *,
        idempotency_key: str,
    ) -> PublicRlmToolResult:
        self._validate_identity(idempotency_key, "idempotency_key")
        if len(spec.query.encode("utf-8")) > 16_384:
            raise ValueError("public RLM query exceeds the fixed UTF-8 byte bound")
        if (
            spec.strategy == "iterative_refinement"
            and spec.max_result_bytes_per_call > 32_768
        ):
            raise ValueError(
                "iterative refinement result bytes cannot exceed 32768"
            )
        job_id = self._job_id(idempotency_key)
        request_digest = canonical_sha256(
            {
                "command": "start",
                "idempotency_key": idempotency_key,
                "spec": spec,
            }
        )
        with self._write():
            existing = self._connection.execute(
                """
                SELECT job_id, start_request_digest FROM public_rlm_jobs
                WHERE principal_id = ? AND start_idempotency_key = ?
                """,
                (self._principal_id, idempotency_key),
            ).fetchone()
            if existing is not None:
                if str(existing["start_request_digest"]) != request_digest:
                    raise PublicRlmConflict(
                        "public RLM start idempotency key is bound to different job bytes"
                    )
                return PublicRlmToolResult(
                    job=self._view(self._job_row(str(existing["job_id"])))
                )
            count = int(
                self._connection.execute(
                    "SELECT COUNT(*) FROM public_rlm_jobs WHERE principal_id = ?",
                    (self._principal_id,),
                ).fetchone()[0]
            )
            if count >= self._max_jobs:
                raise PublicRlmCapacityExceeded(
                    "tenant retained RLM job count reached the configured limit"
                )
            call = self._call_spec(job_id, spec, step_index=0)
            view = PublicRlmJobView.issue(
                job_id=job_id,
                phase="pending_model_call",
                certainty=OutcomeCertainty.CERTAIN,
                revision=0,
                spec=spec,
                calls_committed=0,
                pending_call=call,
                active_ticket=None,
                steps=(),
                result=None,
                failure_code=None,
                failure_message=None,
            )
            response = PublicRlmToolResult(job=view)
            now_ms = self._now_ms()
            self._connection.execute(
                """
                INSERT INTO public_rlm_jobs(
                    job_id, principal_id, start_idempotency_key, start_request_digest,
                    start_response_json, spec_json, phase, certainty, revision,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    self._principal_id,
                    idempotency_key,
                    request_digest,
                    canonical_json_bytes(response).decode(),
                    canonical_json_bytes(spec).decode(),
                    "pending_model_call",
                    OutcomeCertainty.CERTAIN.value,
                    0,
                    now_ms,
                    now_ms,
                ),
            )
            self._connection.execute(
                """
                INSERT INTO public_rlm_calls(
                    job_id, step_index, call_id, state, call_spec_json,
                    created_at_unix_ms, updated_at_unix_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    call.step_index,
                    call.call_id,
                    "pending",
                    canonical_json_bytes(call).decode(),
                    now_ms,
                    now_ms,
                ),
            )
            return response

    def status(self, job_id: str) -> PublicRlmToolResult:
        self._validate_identity(job_id, "job_id")
        with self._lock:
            return PublicRlmToolResult(job=self._view(self._job_row(job_id)))

    def claim(self, request: PublicRlmClaimRequest) -> PublicRlmToolResult:
        request_digest = canonical_sha256(request)
        with self._write():
            job = self._job_row(request.job_id)
            replay = self._command_replay(
                job_id=request.job_id,
                command_kind="claim",
                idempotency_key=request.idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            call = self._connection.execute(
                """
                SELECT * FROM public_rlm_calls WHERE job_id = ? AND call_id = ?
                """,
                (request.job_id, request.call_id),
            ).fetchone()
            if call is None:
                raise PublicRlmConflict("model-call identity is not pending for this RLM job")
            if call["ticket_json"] is not None:
                raise PublicRlmConflict("model call is already claimed by an execution ticket")
            self._assert_revision(job, request.expected_revision)
            if str(job["phase"]) != "pending_model_call" or str(call["state"]) != "pending":
                raise PublicRlmInvalidTransition(
                    "public RLM job does not have an unclaimed pending model call"
                )
            call_spec = PublicRlmCallSpec.model_validate_json(
                str(call["call_spec_json"]), strict=True
            )
            job_spec = PublicRlmJobSpec.model_validate_json(
                str(job["spec_json"]), strict=True
            )
            try:
                ticket = PublicRlmExecutionTicket.issue(call_spec, job_spec, request)
            except ValueError as error:
                raise PublicRlmConflict(str(error)) from None
            now_ms = self._now_ms()
            self._connection.execute(
                """
                UPDATE public_rlm_calls
                SET state = 'claimed', ticket_json = ?, updated_at_unix_ms = ?
                WHERE job_id = ? AND call_id = ? AND state = 'pending'
                """,
                (
                    canonical_json_bytes(ticket).decode(),
                    now_ms,
                    request.job_id,
                    request.call_id,
                ),
            )
            changed = self._connection.execute(
                """
                UPDATE public_rlm_jobs
                SET phase = 'awaiting_caller_result', revision = revision + 1,
                    updated_at_unix_ms = ?
                WHERE job_id = ? AND principal_id = ? AND revision = ?
                  AND phase = 'pending_model_call'
                """,
                (
                    now_ms,
                    request.job_id,
                    self._principal_id,
                    request.expected_revision,
                ),
            ).rowcount
            if changed != 1:  # pragma: no cover - lock plus CAS closes this race
                raise PublicRlmConflict("public RLM claim lost its revision compare-and-set")
            response = PublicRlmToolResult(job=self._view(self._job_row(request.job_id)))
            self._save_command(
                job_id=request.job_id,
                command_kind="claim",
                idempotency_key=request.idempotency_key,
                request_digest=request_digest,
                response=response,
            )
            return response

    def commit(self, request: PublicRlmCommitRequest) -> PublicRlmToolResult:
        request_digest = canonical_sha256(request)
        with self._write():
            job = self._job_row(request.job_id)
            replay = self._command_replay(
                job_id=request.job_id,
                command_kind="commit",
                idempotency_key=request.idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            call = self._connection.execute(
                "SELECT * FROM public_rlm_calls WHERE job_id = ? AND call_id = ?",
                (request.job_id, request.call_id),
            ).fetchone()
            if call is None or call["ticket_json"] is None:
                raise PublicRlmConflict("model call has no claimed execution ticket")
            ticket = PublicRlmExecutionTicket.model_validate_json(
                str(call["ticket_json"]), strict=True
            )
            if ticket.ticket_digest != request.ticket_digest:
                raise PublicRlmConflict("execution ticket digest does not match the claimed call")
            spec = PublicRlmJobSpec.model_validate_json(str(job["spec_json"]), strict=True)
            try:
                receipt = PublicRlmCallerReceipt.issue(
                    ticket,
                    request,
                    max_result_bytes=spec.max_result_bytes_per_call,
                )
            except ValueError as error:
                raise PublicRlmConflict(str(error)) from None
            if call["caller_receipt_json"] is not None:
                existing_receipt = PublicRlmCallerReceipt.model_validate_json(
                    str(call["caller_receipt_json"]), strict=True
                )
                if existing_receipt != receipt:
                    raise PublicRlmConflict(
                        "model call already binds a different caller observation"
                    )
                raise PublicRlmConflict(
                    "model call is already committed; replay the original "
                    "idempotency key"
                )
            self._assert_revision(job, request.expected_revision)
            phase = str(job["phase"])
            if phase not in {"awaiting_caller_result", "cancel_requested"}:
                raise PublicRlmInvalidTransition(
                    "public RLM job is not awaiting this caller result"
                )

            now_ms = self._now_ms()
            call_state = "observed" if phase == "cancel_requested" else "committed"
            self._connection.execute(
                """
                UPDATE public_rlm_calls
                SET state = ?, caller_receipt_json = ?, updated_at_unix_ms = ?
                WHERE job_id = ? AND call_id = ? AND caller_receipt_json IS NULL
                """,
                (
                    call_state,
                    canonical_json_bytes(receipt).decode(),
                    now_ms,
                    request.job_id,
                    request.call_id,
                ),
            )

            next_phase = phase
            certainty = OutcomeCertainty.CERTAIN
            failure_code: str | None = None
            failure_message: str | None = None
            terminal: PublicRlmTerminalResult | None = None

            if phase == "cancel_requested":
                certainty = OutcomeCertainty.INDETERMINATE
                failure_code = "RLM_CANCEL_REQUESTED_AFTER_CLAIM"
                failure_message = (
                    "caller observation was retained after cancellation, but AAR cannot prove "
                    "that the host-side model execution stopped"
                )
            elif request.outcome == "outcome_unknown":
                next_phase = "indeterminate"
                certainty = OutcomeCertainty.INDETERMINATE
                failure_code = request.failure_code or "RLM_CALL_OUTCOME_UNKNOWN"
                failure_message = request.failure_message or (
                    "the caller could not prove a terminal host-side model outcome"
                )
            elif request.outcome == "failed_certain":
                next_phase = "failed"
                failure_code = request.failure_code
                failure_message = request.failure_message or (
                    "the caller reported a certain host-side model failure"
                )
            elif not receipt.within_output_bound:
                next_phase = "failed"
                failure_code = "RLM_RESULT_TOO_LARGE"
                failure_message = (
                    "the model result exceeded the bound; only its digest and byte count were "
                    "retained"
                )
            elif receipt.route_matched is False:
                next_phase = "failed"
                failure_code = "RLM_ROUTE_MISMATCH"
                failure_message = (
                    "the caller-reported effective model route differs from the bound ticket"
                )
            elif (
                receipt.output_tokens is not None
                and receipt.output_tokens > ticket.requested_max_output_tokens
            ):
                next_phase = "failed"
                failure_code = "RLM_REPORTED_USAGE_EXCEEDS_TICKET"
                failure_message = (
                    "caller-reported output usage exceeds the ticketed output-token bound"
                )
            else:
                call_spec = PublicRlmCallSpec.model_validate_json(
                    str(call["call_spec_json"]), strict=True
                )
                step = PublicRlmStepSummary.from_committed(
                    call_spec=call_spec,
                    ticket=ticket,
                    receipt=receipt,
                )
                self._connection.execute(
                    """
                    UPDATE public_rlm_calls SET step_summary_json = ?
                    WHERE job_id = ? AND call_id = ?
                    """,
                    (
                        canonical_json_bytes(step).decode(),
                        request.job_id,
                        request.call_id,
                    ),
                )
                steps = tuple(
                    PublicRlmStepSummary.model_validate_json(
                        str(item["step_summary_json"]), strict=True
                    )
                    for item in self._call_rows(request.job_id)
                    if item["step_summary_json"] is not None
                )
                if len(steps) >= spec.max_model_calls:
                    assert receipt.output_text is not None
                    terminal = PublicRlmTerminalResult.issue(
                        job_id=request.job_id,
                        strategy=spec.strategy,
                        answer=receipt.output_text,
                        steps=steps,
                    )
                    next_phase = "succeeded"
                else:
                    assert receipt.output_text is not None
                    next_call = self._call_spec(
                        request.job_id,
                        spec,
                        step_index=len(steps),
                        previous_output=receipt.output_text,
                    )
                    self._connection.execute(
                        """
                        INSERT INTO public_rlm_calls(
                            job_id, step_index, call_id, state, call_spec_json,
                            created_at_unix_ms, updated_at_unix_ms
                        ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                        """,
                        (
                            request.job_id,
                            next_call.step_index,
                            next_call.call_id,
                            canonical_json_bytes(next_call).decode(),
                            now_ms,
                            now_ms,
                        ),
                    )
                    next_phase = "pending_model_call"

            changed = self._connection.execute(
                """
                UPDATE public_rlm_jobs
                SET phase = ?, certainty = ?, revision = revision + 1,
                    terminal_result_json = ?, failure_code = ?, failure_message = ?,
                    updated_at_unix_ms = ?
                WHERE job_id = ? AND principal_id = ? AND revision = ? AND phase = ?
                """,
                (
                    next_phase,
                    certainty.value,
                    None if terminal is None else canonical_json_bytes(terminal).decode(),
                    failure_code,
                    failure_message,
                    now_ms,
                    request.job_id,
                    self._principal_id,
                    request.expected_revision,
                    phase,
                ),
            ).rowcount
            if changed != 1:  # pragma: no cover - lock plus CAS closes this race
                raise PublicRlmConflict("public RLM commit lost its revision compare-and-set")
            response = PublicRlmToolResult(job=self._view(self._job_row(request.job_id)))
            self._save_command(
                job_id=request.job_id,
                command_kind="commit",
                idempotency_key=request.idempotency_key,
                request_digest=request_digest,
                response=response,
            )
            return response

    def cancel(
        self,
        job_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
    ) -> PublicRlmToolResult:
        self._validate_identity(job_id, "job_id")
        self._validate_identity(idempotency_key, "idempotency_key")
        request_digest = canonical_sha256(
            {
                "command": "cancel",
                "job_id": job_id,
                "expected_revision": expected_revision,
                "idempotency_key": idempotency_key,
            }
        )
        with self._write():
            job = self._job_row(job_id)
            replay = self._command_replay(
                job_id=job_id,
                command_kind="cancel",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
            )
            if replay is not None:
                return replay
            phase = str(job["phase"])
            if phase in {
                "succeeded",
                "failed",
                "cancelled",
                "indeterminate",
                "cancel_requested",
            }:
                raise PublicRlmConflict(
                    "public RLM job is already terminal or cancellation is already "
                    "recorded; replay the original idempotency key"
                )
            self._assert_revision(job, expected_revision)
            now_ms = self._now_ms()
            if phase == "pending_model_call":
                next_phase = "cancelled"
                certainty = OutcomeCertainty.CERTAIN
                failure_code = None
                failure_message = None
                self._connection.execute(
                    """
                    UPDATE public_rlm_calls SET state = 'cancelled', updated_at_unix_ms = ?
                    WHERE job_id = ? AND state = 'pending'
                    """,
                    (now_ms, job_id),
                )
            elif phase == "awaiting_caller_result":
                next_phase = "cancel_requested"
                certainty = OutcomeCertainty.INDETERMINATE
                failure_code = "RLM_CANCEL_REQUESTED_AFTER_CLAIM"
                failure_message = (
                    "AAR recorded cancellation but cannot stop or prove the host-side model call"
                )
            else:  # pragma: no cover - closed phase vocabulary
                raise PublicRlmInvalidTransition(
                    f"cannot cancel public RLM job from {phase}"
                )
            changed = self._connection.execute(
                """
                UPDATE public_rlm_jobs
                SET phase = ?, certainty = ?, revision = revision + 1,
                    failure_code = ?, failure_message = ?, updated_at_unix_ms = ?
                WHERE job_id = ? AND principal_id = ? AND revision = ? AND phase = ?
                """,
                (
                    next_phase,
                    certainty.value,
                    failure_code,
                    failure_message,
                    now_ms,
                    job_id,
                    self._principal_id,
                    expected_revision,
                    phase,
                ),
            ).rowcount
            if changed != 1:  # pragma: no cover - lock plus CAS closes this race
                raise PublicRlmConflict("public RLM cancel lost its revision compare-and-set")
            response = PublicRlmToolResult(job=self._view(self._job_row(job_id)))
            self._save_command(
                job_id=job_id,
                command_kind="cancel",
                idempotency_key=idempotency_key,
                request_digest=request_digest,
                response=response,
            )
            return response

    def close(self) -> None:
        with self._lock:
            self._connection.close()


__all__ = [
    "PublicRlmCapacityExceeded",
    "PublicRlmConflict",
    "PublicRlmCoordinator",
    "PublicRlmError",
    "PublicRlmInvalidTransition",
    "PublicRlmNotFound",
]
