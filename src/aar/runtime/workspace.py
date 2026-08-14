"""Deterministic persisted workspace fake used by reference-host conformance."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Protocol

from aar.canonical import canonical_json_bytes
from aar.runtime.models import (
    WorkspaceExecuteSpec,
    WorkspaceExecutionResult,
    WorkspaceHandle,
    WorkspaceSnapshot,
)
from aar.schemas import OperationRef, SessionRef, WorkspaceRef


class WorkspaceError(RuntimeError):
    """Base class for deterministic workspace contract failures."""


class WorkspaceNotFound(WorkspaceError):
    pass


class StaleWorkspaceGeneration(WorkspaceError):
    pass


class WorkspaceRevisionConflict(WorkspaceError):
    pass


class WorkspaceSessionMismatch(WorkspaceError):
    pass


class InvalidWorkspaceMutation(WorkspaceError):
    pass


class SimulatedWorkspaceTransactionLoss(BaseException):
    """Test-only abrupt loss between state update and receipt insertion."""


class MutationWorkspaceBackend(Protocol):
    def create(self, workspace: WorkspaceRef, session: SessionRef) -> WorkspaceHandle: ...

    def execute(
        self,
        operation: OperationRef,
        spec: WorkspaceExecuteSpec,
        *,
        failpoint: str | None = None,
    ) -> WorkspaceExecutionResult: ...

    def inspect(self, handle: WorkspaceHandle) -> WorkspaceSnapshot: ...


class DeterministicWorkspace:
    """A JSON-scalar workspace whose mutation and receipt commit atomically."""

    def __init__(self, database_path: Path) -> None:
        self._connection = sqlite3.connect(database_path.resolve(), isolation_level="IMMEDIATE")
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._lock = threading.RLock()
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS workspaces (
                workspace_id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                state_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workspace_receipts (
                operation_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(workspace_id),
                result_json TEXT NOT NULL
            );
            """
        )

    def create(self, workspace: WorkspaceRef, session: SessionRef) -> WorkspaceHandle:
        with self._lock, self._connection:
            existing = self._connection.execute(
                "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace.value,)
            ).fetchone()
            if existing is None:
                self._connection.execute(
                    """
                    INSERT INTO workspaces(
                        workspace_id, session_id, generation, revision, state_json
                    )
                    VALUES (?, ?, 1, 0, '{}')
                    """,
                    (workspace.value, session.value),
                )
                return WorkspaceHandle(workspace=workspace, generation=1, revision=0)
            if existing["session_id"] != session.value:
                raise WorkspaceError("workspace is bound to another session")
            return self._handle(existing)

    def execute(
        self,
        operation: OperationRef,
        spec: WorkspaceExecuteSpec,
        *,
        failpoint: str | None = None,
    ) -> WorkspaceExecutionResult:
        with self._lock, self._connection:
            prior_receipt = self._connection.execute(
                "SELECT result_json FROM workspace_receipts WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if prior_receipt is not None:
                return WorkspaceExecutionResult.model_validate_json(
                    prior_receipt["result_json"], strict=True
                )

            row = self._workspace_row(spec.workspace)
            self._check_version(row, spec.expected_generation, spec.expected_revision)
            state = json.loads(row["state_json"])
            previous = state.get(spec.key)
            if spec.action == "set":
                state[spec.key] = spec.value
            elif spec.action == "delete":
                state.pop(spec.key, None)
            elif spec.action == "increment":
                current = state.get(spec.key, 0)
                if isinstance(current, bool) or not isinstance(current, int):
                    raise InvalidWorkspaceMutation("increment target is not an integer")
                assert isinstance(spec.value, int) and not isinstance(spec.value, bool)
                state[spec.key] = current + spec.value
            else:  # pragma: no cover - Pydantic closes this branch
                raise InvalidWorkspaceMutation(spec.action)

            revision_before = int(row["revision"])
            revision_after = revision_before + 1
            result = WorkspaceExecutionResult(
                workspace=spec.workspace,
                generation=int(row["generation"]),
                revision_before=revision_before,
                revision_after=revision_after,
                action=spec.action,
                key=spec.key,
                previous=previous,
                value=state.get(spec.key),
            )
            result_json = canonical_json_bytes(result).decode()
            self._connection.execute(
                "UPDATE workspaces SET revision = ?, state_json = ? WHERE workspace_id = ?",
                (
                    revision_after,
                    canonical_json_bytes(state).decode(),
                    spec.workspace.value,
                ),
            )
            if failpoint == "loss_after_state_before_receipt":
                raise SimulatedWorkspaceTransactionLoss()
            self._connection.execute(
                """
                INSERT INTO workspace_receipts(operation_id, workspace_id, result_json)
                VALUES (?, ?, ?)
                """,
                (operation.value, spec.workspace.value, result_json),
            )
            return result

    def inspect(self, handle: WorkspaceHandle) -> WorkspaceSnapshot:
        row = self._workspace_row(handle.workspace)
        self._check_version(row, handle.generation, handle.revision)
        state = json.loads(row["state_json"])
        return WorkspaceSnapshot(
            workspace=handle.workspace,
            generation=int(row["generation"]),
            revision=int(row["revision"]),
            values=tuple(sorted(state.items())),
        )

    def current_handle(self, workspace: WorkspaceRef) -> WorkspaceHandle:
        return self._handle(self._workspace_row(workspace))

    def count(self) -> int:
        with self._lock:
            row = self._connection.execute("SELECT COUNT(*) AS count FROM workspaces").fetchone()
            assert row is not None
            return int(row["count"])

    def attach(
        self,
        workspace: WorkspaceRef,
        session: SessionRef,
        expected_generation: int,
        expected_revision: int,
    ) -> WorkspaceHandle:
        row = self._workspace_row(workspace)
        if row["session_id"] != session.value:
            raise WorkspaceSessionMismatch("workspace is bound to another session")
        self._check_version(row, expected_generation, expected_revision)
        return self._handle(row)

    def assert_session(self, workspace: WorkspaceRef, session: SessionRef) -> None:
        row = self._workspace_row(workspace)
        if row["session_id"] != session.value:
            raise WorkspaceSessionMismatch("workspace is bound to another session")

    def receipt(self, operation: OperationRef) -> WorkspaceExecutionResult | None:
        row = self._connection.execute(
            "SELECT result_json FROM workspace_receipts WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        if row is None:
            return None
        return WorkspaceExecutionResult.model_validate_json(row["result_json"], strict=True)

    def _workspace_row(self, workspace: WorkspaceRef) -> sqlite3.Row:
        row = self._connection.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?", (workspace.value,)
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound(workspace.value)
        return row

    @staticmethod
    def _check_version(row: sqlite3.Row, generation: int, revision: int) -> None:
        if int(row["generation"]) != generation:
            raise StaleWorkspaceGeneration(
                f"workspace generation is {row['generation']}, not {generation}"
            )
        if int(row["revision"]) != revision:
            raise WorkspaceRevisionConflict(
                f"workspace revision is {row['revision']}, not {revision}"
            )

    @staticmethod
    def _handle(row: sqlite3.Row) -> WorkspaceHandle:
        return WorkspaceHandle(
            workspace=WorkspaceRef(value=str(row["workspace_id"])),
            generation=int(row["generation"]),
            revision=int(row["revision"]),
        )

    def close(self) -> None:
        self._connection.close()
