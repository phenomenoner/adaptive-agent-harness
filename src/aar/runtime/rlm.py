"""Bounded, persisted RLM state machine over the typed broker facade."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from aar.broker_models import (
    BrokerCallTrace as BrokerCallTrace,
)
from aar.broker_models import (
    BrokerReceipt,
    BrokerUsage,
    EvidenceQuery,
    ModelRequest,
)
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.rlm_models import (
    RLM_SCHEMA_MODELS as RLM_SCHEMA_MODELS,
)
from aar.rlm_models import (
    RlmAction,
    RlmJobSnapshot,
    RlmJobSpec,
    RlmResult,
    RlmStep,
    RlmStrategyName,
)
from aar.rlm_models import (
    RlmActionKind as RlmActionKind,
)
from aar.runtime.brokers import (
    BoundBrokerFacade,
    TypedBrokerFacade,
)
from aar.schemas import OperationRef, OperationState, RequestEnvelope


class RlmStrategy(Protocol):
    name: RlmStrategyName

    def next_action(
        self, spec: RlmJobSpec, steps: tuple[RlmStep, ...]
    ) -> RlmAction | None: ...

    def answer(self, steps: tuple[RlmStep, ...]) -> str: ...


class BaselineStrategy:
    name: RlmStrategyName = "baseline"

    def next_action(
        self, spec: RlmJobSpec, steps: tuple[RlmStep, ...]
    ) -> RlmAction | None:
        if not steps:
            return RlmAction(kind="model.request", text=spec.query)
        return None

    def answer(self, steps: tuple[RlmStep, ...]) -> str:
        return steps[-1].receipt.value


class EvidenceSynthesisStrategy:
    name: RlmStrategyName = "evidence_synthesis"

    def next_action(
        self, spec: RlmJobSpec, steps: tuple[RlmStep, ...]
    ) -> RlmAction | None:
        if not steps:
            return RlmAction(kind="evidence.query", text=spec.query)
        if len(steps) == 1:
            evidence = steps[0].receipt.value or "(no matching evidence)"
            return RlmAction(
                kind="model.request",
                text=f"Question: {spec.query}\nEvidence:\n{evidence}",
            )
        return None

    def answer(self, steps: tuple[RlmStep, ...]) -> str:
        return steps[-1].receipt.value


_STRATEGIES: dict[RlmStrategyName, RlmStrategy] = {
    "baseline": BaselineStrategy(),
    "evidence_synthesis": EvidenceSynthesisStrategy(),
}


def next_rlm_action(
    spec: RlmJobSpec,
    steps: tuple[RlmStep, ...],
) -> RlmAction | None:
    """Return the exact next action from the versioned in-package strategy implementation."""

    return _STRATEGIES[spec.strategy].next_action(spec, steps)


class RlmStore:
    """Persist job state and step receipts separately from outer operation truth."""

    def __init__(self, database_path: Path) -> None:
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path.resolve(),
            isolation_level="IMMEDIATE",
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS rlm_jobs (
                operation_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                spec_json TEXT NOT NULL,
                result_json TEXT
            );

            CREATE TABLE IF NOT EXISTS rlm_steps (
                operation_id TEXT NOT NULL,
                step_index INTEGER NOT NULL,
                step_json TEXT NOT NULL,
                PRIMARY KEY(operation_id, step_index),
                FOREIGN KEY(operation_id) REFERENCES rlm_jobs(operation_id)
            );
            """
        )

    def ensure(self, operation: OperationRef, spec: RlmJobSpec) -> None:
        spec_json = canonical_json_bytes(spec).decode()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT spec_json FROM rlm_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is not None:
                if str(row["spec_json"]) != spec_json:
                    raise ValueError("RLM operation already binds different spec bytes")
                return
            self._connection.execute(
                """
                INSERT INTO rlm_jobs(operation_id, state, spec_json)
                VALUES (?, ?, ?)
                """,
                (operation.value, OperationState.ACCEPTED.value, spec_json),
            )

    def exists(self, operation: OperationRef) -> bool:
        with self._lock:
            row = self._connection.execute(
                "SELECT 1 FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            return row is not None

    def begin(self, operation: OperationRef) -> None:
        self._set_state(
            operation,
            expected={OperationState.ACCEPTED},
            state=OperationState.RUNNING,
        )

    def record_step(self, operation: OperationRef, step: RlmStep) -> None:
        step_json = canonical_json_bytes(step).decode()
        with self._lock, self._connection:
            existing = self._connection.execute(
                """
                SELECT step_json FROM rlm_steps
                WHERE operation_id = ? AND step_index = ?
                """,
                (operation.value, step.index),
            ).fetchone()
            if existing is not None:
                if str(existing["step_json"]) != step_json:
                    raise ValueError("RLM step index already binds different receipt bytes")
                return
            self._connection.execute(
                """
                INSERT INTO rlm_steps(operation_id, step_index, step_json)
                VALUES (?, ?, ?)
                """,
                (operation.value, step.index, step_json),
            )

    def complete(self, operation: OperationRef, result: RlmResult) -> None:
        result_json = canonical_json_bytes(result).decode()
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state, result_json FROM rlm_jobs WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            if row["state"] == OperationState.SUCCEEDED.value:
                if str(row["result_json"]) != result_json:
                    raise ValueError("RLM operation already binds a different result")
                return
            if row["state"] != OperationState.RUNNING.value:
                raise ValueError(f"cannot complete RLM job from {row['state']}")
            self._connection.execute(
                """
                UPDATE rlm_jobs SET state = ?, result_json = ? WHERE operation_id = ?
                """,
                (OperationState.SUCCEEDED.value, result_json, operation.value),
            )

    def mark(self, operation: OperationRef, state: OperationState) -> None:
        if state not in {
            OperationState.CANCELLED,
            OperationState.FAILED,
            OperationState.INDETERMINATE,
            OperationState.TIMED_OUT,
        }:
            raise ValueError(f"unsupported RLM terminal marker: {state.value}")
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            current = OperationState(str(row["state"]))
            if current in {
                OperationState.SUCCEEDED,
                OperationState.CANCELLED,
                OperationState.FAILED,
                OperationState.TIMED_OUT,
            }:
                return
            self._connection.execute(
                "UPDATE rlm_jobs SET state = ? WHERE operation_id = ?",
                (state.value, operation.value),
            )

    def terminal_result(self, operation: OperationRef) -> RlmResult | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT result_json FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None or row["result_json"] is None:
                return None
            return RlmResult.model_validate_json(str(row["result_json"]), strict=True)

    def snapshot(
        self,
        operation: OperationRef,
        *,
        authoritative_state: OperationState | None = None,
        usage: BrokerUsage | None = None,
    ) -> RlmJobSnapshot:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            result = (
                None
                if row["result_json"] is None
                else RlmResult.model_validate_json(str(row["result_json"]), strict=True)
            )
            return RlmJobSnapshot(
                operation=operation,
                state=authoritative_state or OperationState(str(row["state"])),
                spec=RlmJobSpec.model_validate_json(str(row["spec_json"]), strict=True),
                steps=self.steps(operation),
                usage=usage or (BrokerUsage() if result is None else result.usage),
                result=result,
            )

    def steps(self, operation: OperationRef) -> tuple[RlmStep, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT step_json FROM rlm_steps
                WHERE operation_id = ? ORDER BY step_index
                """,
                (operation.value,),
            ).fetchall()
            return tuple(
                RlmStep.model_validate_json(str(row["step_json"]), strict=True)
                for row in rows
            )

    def resume(self, operation: OperationRef) -> None:
        """Return a safely replayable non-terminal job to accepted without dropping steps."""

        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            current = OperationState(str(row["state"]))
            if current is OperationState.ACCEPTED:
                return
            if current not in {OperationState.RUNNING, OperationState.INDETERMINATE}:
                raise ValueError(f"cannot resume RLM job from {current.value}")
            self._connection.execute(
                "UPDATE rlm_jobs SET state = ? WHERE operation_id = ?",
                (OperationState.ACCEPTED.value, operation.value),
            )

    def _set_state(
        self,
        operation: OperationRef,
        *,
        expected: set[OperationState],
        state: OperationState,
    ) -> None:
        with self._lock, self._connection:
            row = self._connection.execute(
                "SELECT state FROM rlm_jobs WHERE operation_id = ?", (operation.value,)
            ).fetchone()
            if row is None:
                raise KeyError(operation.value)
            current = OperationState(str(row["state"]))
            if current not in expected:
                raise ValueError(f"cannot transition RLM job from {current.value}")
            self._connection.execute(
                "UPDATE rlm_jobs SET state = ? WHERE operation_id = ?",
                (state.value, operation.value),
            )

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class SimulatedRlmProcessLoss(BaseException):
    """Test-only abrupt loss that bypasses normal failure mapping."""


class RlmExecutionCancelled(RuntimeError):
    """Durable control requested cancellation at a replay-safe RLM boundary."""


class RlmEngine:
    def __init__(
        self,
        database_path: Path,
        brokers: TypedBrokerFacade,
        now_ms: Callable[[], int],
    ) -> None:
        self.store = RlmStore(database_path)
        self._brokers = brokers
        self._now_ms = now_ms

    def ensure(self, operation: OperationRef, spec: RlmJobSpec) -> None:
        self.store.ensure(operation, spec)

    def run(
        self,
        operation: OperationRef,
        envelope: RequestEnvelope,
        *,
        failpoint: str | None = None,
        cancellation_requested: Callable[[], bool] | None = None,
        heartbeat: Callable[[], None] | None = None,
    ) -> RlmResult:
        cancelled = cancellation_requested or (lambda: False)
        beat = heartbeat or (lambda: None)
        snapshot = self.store.snapshot(operation)
        if cancelled():
            raise RlmExecutionCancelled("cancellation requested before RLM execution")
        beat()
        self.store.begin(operation)
        if failpoint == "process_loss_before_first_broker":
            raise SimulatedRlmProcessLoss()
        strategy = _STRATEGIES[snapshot.spec.strategy]
        bound = self._brokers.bind(envelope, operation)
        steps = self.store.steps(operation)
        while len(steps) < snapshot.spec.max_steps:
            if cancelled():
                raise RlmExecutionCancelled("cancellation requested at RLM step boundary")
            beat()
            if self._now_ms() >= envelope.deadline_unix_ms:
                raise TimeoutError("RLM request deadline expired during execution")
            action = strategy.next_action(snapshot.spec, steps)
            if action is None:
                break
            receipt = self._execute_action(bound, action, len(steps))
            if failpoint == "process_loss_after_broker_receipt":
                raise SimulatedRlmProcessLoss()
            trace = bound.traces()[-1]
            step = RlmStep(
                index=len(steps),
                action=action.kind,
                request_digest=canonical_sha256(action),
                broker_call_sequence=trace.sequence,
                grant_id=trace.grant_id,
                receipt=receipt,
            )
            self.store.record_step(operation, step)
            steps = (*steps, step)
            beat()
        if cancelled():
            raise RlmExecutionCancelled("cancellation requested before RLM completion")
        if strategy.next_action(snapshot.spec, steps) is not None:
            raise RuntimeError("RLM step bound exhausted before strategy completion")
        result = RlmResult.issue(
            operation=operation,
            strategy=snapshot.spec.strategy,
            answer=strategy.answer(steps),
            steps=steps,
            usage=bound.usage,
            broker_trace=bound.traces(),
        )
        if cancelled():
            raise RlmExecutionCancelled("cancellation requested before RLM receipt commit")
        beat()
        self.store.complete(operation, result)
        if failpoint == "process_loss_after_terminal_receipt":
            raise SimulatedRlmProcessLoss()
        return result

    @staticmethod
    def _execute_action(
        bound: BoundBrokerFacade, action: RlmAction, index: int
    ) -> BrokerReceipt:
        step_key = f"rlm-step-{index}"
        if action.kind == "evidence.query":
            return bound.evidence_query(EvidenceQuery(text=action.text), step_key=step_key)
        return bound.model_request(ModelRequest(prompt=action.text), step_key=step_key)

    def close(self) -> None:
        self.store.close()
