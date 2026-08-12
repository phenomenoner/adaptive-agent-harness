"""Typed, credential-free broker facades and deterministic reference fakes."""

from __future__ import annotations

import base64
import hashlib
import json
import sqlite3
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Literal, cast

from aar.broker_models import (
    _METHODS,
    ArtifactPutRequest,
    ArtifactReadRequest,
    ArtifactReadResult,
    BrokerCallReconciliation,
    BrokerCallTrace,
    BrokerCatalog,
    BrokerContext,
    BrokerContractSet,
    BrokerMethodName,
    BrokerReceipt,
    BrokerReconciliationAction,
    BrokerReconciliationReport,
    BrokerUsage,
    EffectProposal,
    EvidenceQuery,
    ModelRequest,
    RetainedSubagentHandle,
    SubagentResultRequest,
    SubagentSubmit,
    _contract,
    _summary,
)
from aar.broker_models import (
    BROKER_SCHEMA_MODELS as BROKER_SCHEMA_MODELS,
)
from aar.broker_models import (
    BrokerMethodContract as BrokerMethodContract,
)
from aar.broker_models import (
    BrokerMethodSummary as BrokerMethodSummary,
)
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.schemas import (
    ArtifactIdRef,
    ArtifactReference,
    Budget,
    Grant,
    OperationRef,
    RequestEnvelope,
    StrictModel,
)


class BrokerFacadeError(RuntimeError):
    """Base error for a broker call rejected before provider invocation."""


class BrokerGrantDenied(BrokerFacadeError):
    """The bound envelope has no valid grant for the requested broker method."""


class BrokerBudgetExceeded(BrokerFacadeError):
    """The next call would exceed the host-authoritative request budget."""


class BrokerDeadlineExpired(BrokerFacadeError):
    """The bound request deadline expired before provider invocation."""


class BrokerCallConflict(BrokerFacadeError):
    """One broker idempotency identity was reused for different request bytes."""


class BrokerCallIndeterminate(BrokerFacadeError):
    """A prior broker call started but has no authoritative terminal receipt."""


class BrokerJournal:
    """Persist broker call identities and terminal receipts for reconciliation."""

    def __init__(self, database_path: Path) -> None:
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            database_path.resolve(),
            isolation_level="IMMEDIATE",
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS broker_calls (
                operation_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                method TEXT NOT NULL,
                grant_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_digest TEXT NOT NULL,
                request_json TEXT,
                state TEXT NOT NULL,
                response_digest TEXT,
                response_model TEXT NOT NULL,
                response_json TEXT,
                usage_json TEXT NOT NULL,
                failure_code TEXT,
                reconciliation_action TEXT,
                authority_digest TEXT,
                reconciliation_json TEXT,
                compensation_json TEXT,
                reconciled_at_unix_ms INTEGER,
                control_revision INTEGER,
                PRIMARY KEY(operation_id, sequence),
                UNIQUE(operation_id, idempotency_key)
            );
            """
        )
        columns = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(broker_calls)")
        }
        additions = {
            "request_json": "TEXT",
            "reconciliation_action": "TEXT",
            "authority_digest": "TEXT",
            "reconciliation_json": "TEXT",
            "compensation_json": "TEXT",
            "reconciled_at_unix_ms": "INTEGER",
            "control_revision": "INTEGER",
        }
        with self._connection:
            for name, column_type in additions.items():
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE broker_calls ADD COLUMN {name} {column_type}"
                    )

    def invoke(
        self,
        *,
        operation: OperationRef,
        method: BrokerMethodName,
        grant_id: str,
        idempotency_key: str,
        request: StrictModel,
        response_type: type[StrictModel],
        usage: BrokerUsage,
        budget: Budget,
        call: Callable[[], StrictModel],
        expected_control_revision: int | None = None,
        require_not_cancelled: bool = False,
    ) -> StrictModel:
        request_digest = canonical_sha256(request)
        with self._lock, self._connection:
            if expected_control_revision is not None or require_not_cancelled:
                self._connection.execute(
                    """
                    UPDATE operation_controls SET control_revision = control_revision
                    WHERE operation_id = ?
                    """,
                    (operation.value,),
                )
                control_revision, cancellation_requested = (
                    self._operation_control_state_unlocked(operation)
                )
                if (
                    expected_control_revision is not None
                    and control_revision != expected_control_revision
                ):
                    raise BrokerCallConflict(
                        "operation control revision changed before broker admission"
                    )
                if require_not_cancelled and cancellation_requested:
                    raise BrokerCallConflict(
                        "operation cancellation prevents broker admission"
                    )
            existing = self._connection.execute(
                """
                SELECT * FROM broker_calls
                WHERE operation_id = ? AND idempotency_key = ?
                """,
                (operation.value, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["request_digest"] != request_digest:
                    raise BrokerCallConflict(
                        "broker idempotency key already binds different request bytes"
                    )
                if existing["state"] != "succeeded" or existing["response_json"] is None:
                    raise BrokerCallIndeterminate(
                        "broker call has no authoritative terminal receipt"
                    )
                response = response_type.model_validate_json(
                    str(existing["response_json"]), strict=True
                )
                if existing["response_digest"] != canonical_sha256(response):
                    raise BrokerCallConflict(
                        "broker success receipt digest does not match stored response bytes"
                    )
                return response
            current_usage = BrokerUsage()
            for usage_row in self._connection.execute(
                """
                SELECT usage_json FROM broker_calls
                WHERE operation_id = ? AND state = 'succeeded'
                """,
                (operation.value,),
            ).fetchall():
                current_usage = current_usage.plus(
                    BrokerUsage.model_validate_json(
                        str(usage_row["usage_json"]), strict=True
                    )
                )
            _assert_budget(budget, current_usage.plus(usage))
            row = self._connection.execute(
                """
                SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence
                FROM broker_calls WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            assert row is not None
            sequence = int(row["next_sequence"])
            self._connection.execute(
                """
                INSERT INTO broker_calls(
                    operation_id, sequence, method, grant_id, idempotency_key,
                    request_digest, request_json, state, response_model, usage_json,
                    control_revision
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'started', ?, ?, ?)
                """,
                (
                    operation.value,
                    sequence,
                    method,
                    grant_id,
                    idempotency_key,
                    request_digest,
                    canonical_json_bytes(request).decode(),
                    response_type.__name__,
                    canonical_json_bytes(usage).decode(),
                    expected_control_revision,
                ),
            )
        try:
            response = call()
            if not isinstance(response, response_type):
                raise TypeError(
                    f"broker method {method} returned {type(response).__name__}, "
                    f"expected {response_type.__name__}"
                )
        except Exception as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE broker_calls SET state = 'failed', failure_code = ?
                    WHERE operation_id = ? AND sequence = ?
                    """,
                    (type(error).__name__, operation.value, sequence),
                )
            raise
        response_json = canonical_json_bytes(response).decode()
        response_digest = canonical_sha256(response)
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE broker_calls
                SET state = 'succeeded', response_digest = ?, response_json = ?
                WHERE operation_id = ? AND sequence = ?
                """,
                (response_digest, response_json, operation.value, sequence),
            )
        return response

    def operation_control_state(self, operation: OperationRef) -> tuple[int, bool]:
        with self._lock:
            return self._operation_control_state_unlocked(operation)

    def _operation_control_state_unlocked(
        self, operation: OperationRef
    ) -> tuple[int, bool]:
        row = self._connection.execute(
            """
            SELECT control_revision, cancellation_requested
            FROM operation_controls WHERE operation_id = ?
            """,
            (operation.value,),
        ).fetchone()
        if row is None:
            return 0, False
        return int(row["control_revision"]), bool(row["cancellation_requested"])

    def usage(self, operation: OperationRef) -> BrokerUsage:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT usage_json FROM broker_calls
                WHERE operation_id = ? AND state = 'succeeded'
                ORDER BY sequence
                """,
                (operation.value,),
            ).fetchall()
            total = BrokerUsage()
            for row in rows:
                total = total.plus(
                    BrokerUsage.model_validate_json(str(row["usage_json"]), strict=True)
                )
            return total

    def traces(self, operation: OperationRef) -> tuple[BrokerCallTrace, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM broker_calls WHERE operation_id = ? ORDER BY sequence",
                (operation.value,),
            ).fetchall()
            traces: list[BrokerCallTrace] = []
            for row in rows:
                raw_method = str(row["method"])
                if raw_method not in _METHODS:
                    raise BrokerCallConflict("broker journal contains an unknown method")
                method = cast(BrokerMethodName, raw_method)
                raw_state = str(row["state"])
                failure_code = (
                    None if row["failure_code"] is None else str(row["failure_code"])
                )
                if raw_state in {"started", "succeeded", "failed"}:
                    state = cast(
                        Literal["started", "succeeded", "failed"], raw_state
                    )
                else:
                    state = "failed"
                    failure_code = "BrokerCallStateInvalid"
                response_digest = (
                    None
                    if row["response_digest"] is None
                    else str(row["response_digest"])
                )
                if state == "succeeded":
                    response_json = row["response_json"]
                    try:
                        valid_response_digest = (
                            response_json is not None
                            and response_digest
                            == canonical_sha256(json.loads(str(response_json)))
                        )
                    except (TypeError, ValueError, json.JSONDecodeError):
                        valid_response_digest = False
                    if not valid_response_digest:
                        state = "failed"
                        failure_code = "BrokerReceiptDigestMismatch"
                raw_action = row["reconciliation_action"]
                valid_actions = {
                    "receipt_recovered",
                    "safe_replay",
                    "pending",
                    "quarantine",
                    "compensation_proposed",
                }
                reconciliation_action = None
                if raw_action is not None:
                    if str(raw_action) not in valid_actions:
                        state = "failed"
                        failure_code = "BrokerReconciliationActionInvalid"
                    else:
                        reconciliation_action = cast(
                            BrokerReconciliationAction, str(raw_action)
                        )
                reconciliation_digest = None
                compensation_digest = None
                try:
                    if row["reconciliation_json"] is not None:
                        reconciliation_digest = canonical_sha256(
                            json.loads(str(row["reconciliation_json"]))
                        )
                    if row["compensation_json"] is not None:
                        compensation_digest = canonical_sha256(
                            json.loads(str(row["compensation_json"]))
                        )
                except (TypeError, ValueError, json.JSONDecodeError):
                    state = "failed"
                    failure_code = "BrokerReconciliationEvidenceInvalid"
                traces.append(
                    BrokerCallTrace(
                        sequence=int(row["sequence"]),
                        parent_operation=operation,
                        method=method,
                        grant_id=str(row["grant_id"]),
                        idempotency_key=str(row["idempotency_key"]),
                        request_digest=str(row["request_digest"]),
                        state=state,
                        response_digest=response_digest,
                        response_model=str(row["response_model"]),
                        usage_delta=BrokerUsage.model_validate_json(
                            str(row["usage_json"]), strict=True
                        ),
                        failure_code=failure_code,
                        reconciliation_action=reconciliation_action,
                        authority_digest=(
                            None
                            if row["authority_digest"] is None
                            else str(row["authority_digest"])
                        ),
                        reconciliation_digest=reconciliation_digest,
                        compensation_digest=compensation_digest,
                    )
                )
            return tuple(traces)

    def started_calls(self, operation: OperationRef) -> tuple[dict[str, object], ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM broker_calls
                WHERE operation_id = ? AND state = 'started'
                ORDER BY sequence
                """,
                (operation.value,),
            ).fetchall()
            return tuple(dict(row) for row in rows)

    def resolve_started_call(
        self,
        *,
        operation: OperationRef,
        sequence: int,
        method: BrokerMethodName,
        request_digest: str,
        authority_digest: str,
        response: StrictModel,
        action: Literal["receipt_recovered", "safe_replay"],
        reason_code: str,
        now_unix_ms: int,
    ) -> BrokerCallReconciliation:
        response_json = canonical_json_bytes(response).decode()
        response_digest = canonical_sha256(response)
        reconciliation = BrokerCallReconciliation(
            sequence=sequence,
            method=method,
            action=action,
            reason_code=reason_code,
            request_digest=request_digest,
            authority_digest=authority_digest,
            response_digest=response_digest,
        )
        reconciliation_json = canonical_json_bytes(reconciliation).decode()
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT * FROM broker_calls
                WHERE operation_id = ? AND sequence = ?
                """,
                (operation.value, sequence),
            ).fetchone()
            if row is None:
                raise KeyError(f"{operation.value}:{sequence}")
            if str(row["request_digest"]) != request_digest:
                raise BrokerCallConflict("broker request digest changed during reconciliation")
            if str(row["response_model"]) != type(response).__name__:
                raise BrokerCallConflict("broker response model changed during reconciliation")
            if str(row["state"]) == "succeeded":
                if (
                    row["response_json"] != response_json
                    or row["response_digest"] != response_digest
                ):
                    raise BrokerCallConflict(
                        "terminal broker receipt conflicts with reconciled response"
                    )
                return reconciliation
            if str(row["state"]) != "started":
                raise BrokerCallConflict("broker call is no longer reconcilable")
            self._connection.execute(
                """
                UPDATE broker_calls
                SET state = 'succeeded', response_digest = ?, response_json = ?,
                    failure_code = NULL, reconciliation_action = ?,
                    authority_digest = ?, reconciliation_json = ?,
                    compensation_json = NULL, reconciled_at_unix_ms = ?
                WHERE operation_id = ? AND sequence = ? AND state = 'started'
                """,
                (
                    response_digest,
                    response_json,
                    action,
                    authority_digest,
                    reconciliation_json,
                    now_unix_ms,
                    operation.value,
                    sequence,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise BrokerCallConflict("broker call changed during reconciliation")
        return reconciliation

    def record_unresolved_reconciliation(
        self,
        *,
        operation: OperationRef,
        sequence: int,
        method: BrokerMethodName,
        request_digest: str,
        authority_digest: str,
        action: Literal["pending", "quarantine", "compensation_proposed"],
        reason_code: str,
        now_unix_ms: int,
        compensation_receipt: BrokerReceipt | None = None,
    ) -> BrokerCallReconciliation:
        reconciliation = BrokerCallReconciliation(
            sequence=sequence,
            method=method,
            action=action,
            reason_code=reason_code,
            request_digest=request_digest,
            authority_digest=authority_digest,
            compensation_receipt=compensation_receipt,
        )
        reconciliation_json = canonical_json_bytes(reconciliation).decode()
        target_state = "started" if action == "pending" else "failed"
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT state, request_digest, reconciliation_json
                FROM broker_calls WHERE operation_id = ? AND sequence = ?
                """,
                (operation.value, sequence),
            ).fetchone()
            if row is None:
                raise KeyError(f"{operation.value}:{sequence}")
            if str(row["request_digest"]) != request_digest:
                raise BrokerCallConflict("broker request digest changed during reconciliation")
            if str(row["state"]) != "started":
                existing_json = row["reconciliation_json"]
                if existing_json == reconciliation_json:
                    return reconciliation
                raise BrokerCallConflict("broker call is no longer reconcilable")
            self._connection.execute(
                """
                UPDATE broker_calls
                SET state = ?, failure_code = ?, reconciliation_action = ?,
                    authority_digest = ?, reconciliation_json = ?, compensation_json = ?,
                    reconciled_at_unix_ms = ?
                WHERE operation_id = ? AND sequence = ? AND state = 'started'
                """,
                (
                    target_state,
                    reason_code,
                    action,
                    authority_digest,
                    reconciliation_json,
                    None
                    if compensation_receipt is None
                    else canonical_json_bytes(compensation_receipt).decode(),
                    now_unix_ms,
                    operation.value,
                    sequence,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise BrokerCallConflict("broker call changed during reconciliation")
        return reconciliation

    def has_unresolved_calls(self, operation: OperationRef) -> bool:
        """Return whether replay would cross a broker call without a success receipt."""

        with self._lock:
            row = self._connection.execute(
                """
                SELECT 1 FROM broker_calls
                WHERE operation_id = ? AND state != 'succeeded'
                LIMIT 1
                """,
                (operation.value,),
            ).fetchone()
            return row is not None

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class FakeArtifactBroker:
    def __init__(self, database_path: Path | None = None) -> None:
        self._content: dict[str, bytes] = {}
        self._connection: sqlite3.Connection | None = None
        if database_path is not None:
            self._connection = sqlite3.connect(
                database_path.resolve(), isolation_level="IMMEDIATE"
            )
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_artifacts (
                    digest TEXT PRIMARY KEY,
                    content BLOB NOT NULL
                )
                """
            )

    def put(
        self,
        content: bytes,
        media_type: str,
        created_by: OperationRef,
        *,
        redacted: bool = False,
    ) -> ArtifactReference:
        digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        artifact = ArtifactIdRef(value=f"artifact-{digest.removeprefix('sha256:')[:32]}")
        if self._connection is None:
            self._content[digest] = bytes(content)
        else:
            with self._connection:
                self._connection.execute(
                    "INSERT OR IGNORE INTO broker_artifacts(digest, content) VALUES (?, ?)",
                    (digest, bytes(content)),
                )
        return ArtifactReference(
            artifact=artifact,
            digest=digest,
            media_type=media_type,
            size_bytes=len(content),
            created_by=created_by,
            redacted=redacted,
        )

    def lookup(
        self,
        request: ArtifactPutRequest,
        created_by: OperationRef,
    ) -> ArtifactReference | None:
        artifact = ArtifactReference(
            artifact=ArtifactIdRef(
                value=(
                    "artifact-"
                    + request.content_digest.removeprefix("sha256:")[:32]
                )
            ),
            digest=request.content_digest,
            media_type=request.media_type,
            size_bytes=request.size_bytes,
            created_by=created_by,
            redacted=request.redacted,
        )
        try:
            self.read(artifact)
        except KeyError:
            return None
        return artifact

    def read(self, reference: ArtifactReference) -> bytes:
        if self._connection is None:
            content = self._content[reference.digest]
        else:
            row = self._connection.execute(
                "SELECT content FROM broker_artifacts WHERE digest = ?", (reference.digest,)
            ).fetchone()
            if row is None:
                raise KeyError(reference.digest)
            content = bytes(row[0])
        if len(content) != reference.size_bytes:
            raise RuntimeError("artifact size mismatch")
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != reference.digest:
            raise RuntimeError("artifact digest mismatch")
        return content

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()


class FakeModelBroker:
    def request(self, prompt: str, context: BrokerContext) -> BrokerReceipt:
        payload = {
            "prompt": prompt,
            "parent": context.parent_operation.value,
            "grant": context.grant_id,
        }
        digest = canonical_sha256(payload)
        return BrokerReceipt(
            kind="model",
            handle=f"model-{digest.removeprefix('sha256:')[:24]}",
            digest=digest,
            value=f"deterministic:{digest.removeprefix('sha256:')[:16]}",
        )


class FakeSubagentBroker:
    def __init__(self, database_path: Path | None = None) -> None:
        self._results: dict[str, BrokerReceipt] = {}
        self._connection: sqlite3.Connection | None = None
        if database_path is not None:
            self._connection = sqlite3.connect(
                database_path.resolve(), isolation_level="IMMEDIATE"
            )
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS broker_children (
                    handle TEXT PRIMARY KEY,
                    receipt_json TEXT NOT NULL
                )
                """
            )

    def submit(self, task: str, context: BrokerContext) -> str:
        digest = canonical_sha256(
            {"task": task, "parent": context.parent_operation.value, "grant": context.grant_id}
        )
        handle = f"child-{digest.removeprefix('sha256:')[:24]}"
        receipt = BrokerReceipt(
            kind="subagent",
            handle=handle,
            digest=digest,
            value=f"completed:{task}",
        )
        if self._connection is None:
            self._results[handle] = receipt
        else:
            with self._connection:
                self._connection.execute(
                    """
                    INSERT OR IGNORE INTO broker_children(handle, receipt_json)
                    VALUES (?, ?)
                    """,
                    (handle, canonical_json_bytes(receipt).decode()),
                )
        return handle

    def lookup_submission(self, task: str, context: BrokerContext) -> str | None:
        digest = canonical_sha256(
            {"task": task, "parent": context.parent_operation.value, "grant": context.grant_id}
        )
        handle = f"child-{digest.removeprefix('sha256:')[:24]}"
        if self._connection is None:
            return handle if handle in self._results else None
        row = self._connection.execute(
            "SELECT 1 FROM broker_children WHERE handle = ?",
            (handle,),
        ).fetchone()
        return handle if row is not None else None

    def result(self, handle: str) -> BrokerReceipt:
        if self._connection is None:
            return self._results[handle]
        row = self._connection.execute(
            "SELECT receipt_json FROM broker_children WHERE handle = ?", (handle,)
        ).fetchone()
        if row is None:
            raise KeyError(handle)
        return BrokerReceipt.model_validate_json(str(row[0]), strict=True)

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()


class FakeEffectBroker:
    """Records proposals only; it deliberately has no execute/apply method."""

    def propose(
        self, effect_type: str, payload_digest: str, context: BrokerContext
    ) -> BrokerReceipt:
        digest = canonical_sha256(
            {
                "effect_type": effect_type,
                "payload_digest": payload_digest,
                "parent": context.parent_operation.value,
                "grant": context.grant_id,
            }
        )
        return BrokerReceipt(
            kind="effect_proposal",
            handle=f"proposal-{digest.removeprefix('sha256:')[:24]}",
            digest=digest,
            value="proposal_only",
        )


class FakeEvidenceProvider:
    def __init__(self, records: Iterable[str] = ()) -> None:
        self._records = tuple(sorted(records))

    def query(self, text: str, context: BrokerContext) -> BrokerReceipt:
        matches = tuple(record for record in self._records if text.lower() in record.lower())
        digest = canonical_sha256(
            {
                "query": text,
                "matches": matches,
                "parent": context.parent_operation.value,
                "grant": context.grant_id,
            }
        )
        return BrokerReceipt(
            kind="evidence",
            handle=f"evidence-{digest.removeprefix('sha256:')[:24]}",
            digest=digest,
            value="\n".join(matches),
        )


class TypedBrokerFacade:
    """Progressively disclose and bind typed broker methods to one envelope."""

    def __init__(
        self,
        database_path: Path,
        *,
        artifacts: FakeArtifactBroker,
        models: FakeModelBroker,
        subagents: FakeSubagentBroker,
        effects: FakeEffectBroker,
        evidence: FakeEvidenceProvider,
        now_ms: Callable[[], int],
    ) -> None:
        self._journal = BrokerJournal(database_path)
        self._artifacts = artifacts
        self._models = models
        self._subagents = subagents
        self._effects = effects
        self._evidence = evidence
        self._now_ms = now_ms

    def bind(
        self, envelope: RequestEnvelope, operation: OperationRef
    ) -> BoundBrokerFacade:
        return BoundBrokerFacade(self, envelope, operation)

    def has_unresolved_calls(self, operation: OperationRef) -> bool:
        return self._journal.has_unresolved_calls(operation)

    def close(self) -> None:
        self._journal.close()


class BoundBrokerFacade:
    """One operation-scoped broker view with grants and budgets enforced per call."""

    def __init__(
        self,
        facade: TypedBrokerFacade,
        envelope: RequestEnvelope,
        operation: OperationRef,
    ) -> None:
        self._facade = facade
        self._envelope = envelope
        self._operation = operation

    def catalog(self) -> BrokerCatalog:
        granted = {grant.capability for grant in self._envelope.grants}
        methods = tuple(
            _summary(name) for name in sorted(_METHODS) if _METHODS[name][0] in granted
        )
        return BrokerCatalog(methods=methods)

    def describe(self, names: Iterable[BrokerMethodName]) -> BrokerContractSet:
        granted = {item.name for item in self.catalog().methods}
        requested = tuple(sorted(set(names)))
        denied = [name for name in requested if name not in granted]
        if denied:
            raise BrokerGrantDenied(
                "broker contracts are unavailable without grants: " + ", ".join(denied)
            )
        return BrokerContractSet(contracts=tuple(_contract(name) for name in requested))

    @property
    def usage(self) -> BrokerUsage:
        return self._facade._journal.usage(self._operation)

    def traces(self) -> tuple[BrokerCallTrace, ...]:
        return self._facade._journal.traces(self._operation)

    def reconcile_unresolved(
        self,
        *,
        current_capability_digest: str,
        current_compensation_grant: Grant | None = None,
        propose_compensation: bool = False,
        cancellation_requested: bool | None = None,
    ) -> BrokerReconciliationReport:
        caller_cancellation_requested = bool(cancellation_requested)
        if current_capability_digest != self._envelope.capability_digest:
            raise BrokerGrantDenied(
                "operation capability profile does not match current host authority"
            )
        if propose_compensation:
            if current_compensation_grant is None:
                raise BrokerGrantDenied(
                    "current effect.propose grant is required for compensation"
                )
            if (
                current_compensation_grant.capability != "effect.propose"
                or current_compensation_grant.issued_to != self._envelope.principal
            ):
                raise BrokerGrantDenied(
                    "current effect.propose grant is invalid for compensation"
                )
        reconciliations: list[BrokerCallReconciliation] = []
        for row in self._facade._journal.started_calls(self._operation):
            raw_method = str(row["method"])
            if raw_method not in _METHODS:
                raise BrokerCallConflict("broker journal contains an unknown method")
            method = cast(BrokerMethodName, raw_method)
            sequence = int(row["sequence"])
            request_digest = str(row["request_digest"])
            control_revision, durable_cancellation_requested = (
                self._facade._journal.operation_control_state(self._operation)
            )
            cancellation_requested = (
                caller_cancellation_requested or durable_cancellation_requested
            )
            authority_digest = canonical_sha256(
                {
                    "operation": self._operation,
                    "method": method,
                    "grant_id": str(row["grant_id"]),
                    "request_digest": request_digest,
                    "deadline_unix_ms": self._envelope.deadline_unix_ms,
                    "capability_digest": current_capability_digest,
                    "control_revision": control_revision,
                    "cancellation_requested": cancellation_requested,
                }
            )

            def unresolved(
                reason_code: str,
                *,
                pending: bool = False,
                bound_method: BrokerMethodName = method,
                bound_sequence: int = sequence,
                bound_request_digest: str = request_digest,
                bound_authority_digest: str = authority_digest,
                bound_control_revision: int = control_revision,
                bound_cancellation_requested: bool = cancellation_requested,
            ) -> BrokerCallReconciliation:
                compensation: BrokerReceipt | None = None
                action: Literal["pending", "quarantine", "compensation_proposed"]
                action = "pending" if pending else "quarantine"
                if (
                    propose_compensation
                    and not bound_cancellation_requested
                    and self._facade._now_ms() < self._envelope.deadline_unix_ms
                    and bound_method
                    in {"artifact.put", "model.request", "subagent.submit"}
                ):
                    try:
                        assert current_compensation_grant is not None
                        compensation_key = canonical_sha256(
                            {
                                "operation": self._operation,
                                "sequence": bound_sequence,
                                "request_digest": bound_request_digest,
                                "action": "compensation_proposal",
                                "control_revision": bound_control_revision,
                            }
                        )
                        compensation = self._propose_compensation(
                            EffectProposal(
                                effect_type="effect.compensate",
                                payload_digest=bound_request_digest,
                            ),
                            step_key=(
                                "compensate-"
                                + compensation_key.removeprefix("sha256:")[:32]
                            ),
                            grant=current_compensation_grant,
                            control_revision=bound_control_revision,
                        )
                        action = "compensation_proposed"
                    except BrokerFacadeError:
                        compensation = None
                return self._facade._journal.record_unresolved_reconciliation(
                    operation=self._operation,
                    sequence=bound_sequence,
                    method=bound_method,
                    request_digest=bound_request_digest,
                    authority_digest=bound_authority_digest,
                    action=action,
                    reason_code=reason_code,
                    now_unix_ms=self._facade._now_ms(),
                    compensation_receipt=compensation,
                )

            if cancellation_requested:
                reconciliations.append(unresolved("broker_reconcile_cancelled"))
                continue
            if self._facade._now_ms() >= self._envelope.deadline_unix_ms:
                reconciliations.append(unresolved("broker_reconcile_deadline_expired"))
                continue
            request_json = row.get("request_json")
            if request_json is None:
                reconciliations.append(unresolved("broker_request_bytes_unavailable"))
                continue
            request_type = _METHODS[method][3]
            request = request_type.model_validate_json(str(request_json), strict=True)
            if canonical_sha256(request) != request_digest:
                raise BrokerCallConflict("broker request bytes do not match stored digest")
            if method == "effect.propose":
                reconciliations.append(
                    unresolved("effect_proposal_receipt_unavailable", pending=True)
                )
                continue
            try:
                grant = self._grant(method)
            except BrokerGrantDenied:
                reconciliations.append(unresolved("broker_reconcile_authority_denied"))
                continue
            if grant.grant_id != str(row["grant_id"]):
                reconciliations.append(unresolved("broker_reconcile_grant_drift"))
                continue
            context = BrokerContext(
                parent_operation=self._operation,
                grant_id=grant.grant_id,
                deadline_unix_ms=self._envelope.deadline_unix_ms,
                idempotency_key=str(row["idempotency_key"]),
            )
            response: StrictModel | None = None
            action: Literal["receipt_recovered", "safe_replay"] = "safe_replay"
            reason_code = "broker_safe_read_replayed"
            try:
                if method == "artifact.put":
                    assert isinstance(request, ArtifactPutRequest)
                    response = self._facade._artifacts.lookup(request, self._operation)
                    action = "receipt_recovered"
                    reason_code = "artifact_receipt_recovered"
                    if response is None:
                        reconciliations.append(
                            unresolved("artifact_receipt_unavailable", pending=True)
                        )
                        continue
                elif method == "artifact.read":
                    assert isinstance(request, ArtifactReadRequest)
                    content = self._facade._artifacts.read(request.reference)
                    response = ArtifactReadResult(
                        reference=request.reference,
                        content_base64=base64.b64encode(content).decode("ascii"),
                    )
                elif method == "evidence.query":
                    assert isinstance(request, EvidenceQuery)
                    response = self._facade._evidence.query(request.text, context)
                elif method == "model.request":
                    reconciliations.append(
                        unresolved("model_receipt_unavailable", pending=True)
                    )
                    continue
                elif method == "subagent.result":
                    assert isinstance(request, SubagentResultRequest)
                    try:
                        response = self._facade._subagents.result(request.handle)
                    except KeyError:
                        reconciliations.append(
                            unresolved("subagent_result_pending", pending=True)
                        )
                        continue
                else:
                    assert isinstance(request, SubagentSubmit)
                    handle = self._facade._subagents.lookup_submission(request.task, context)
                    if handle is None:
                        reconciliations.append(
                            unresolved(
                                "subagent_submission_receipt_unavailable",
                                pending=True,
                            )
                        )
                        continue
                    response = RetainedSubagentHandle(handle=handle)
                    action = "receipt_recovered"
                    reason_code = "subagent_handle_recovered"
            except (KeyError, RuntimeError, ValueError):
                reconciliations.append(unresolved("broker_provider_conflict"))
                continue
            assert response is not None
            reconciliations.append(
                self._facade._journal.resolve_started_call(
                    operation=self._operation,
                    sequence=sequence,
                    method=method,
                    request_digest=request_digest,
                    authority_digest=authority_digest,
                    response=response,
                    action=action,
                    reason_code=reason_code,
                    now_unix_ms=self._facade._now_ms(),
                )
            )
        return BrokerReconciliationReport.issue(
            operation=self._operation,
            calls=tuple(reconciliations),
            unresolved=self._facade._journal.has_unresolved_calls(self._operation),
        )

    def model_request(self, request: ModelRequest, *, step_key: str) -> BrokerReceipt:
        usage = BrokerUsage(
            model_requests=1,
            input_tokens=_token_units(request.prompt),
            output_tokens=1,
        )
        context, key = self._prepare("model.request", step_key)
        return self._invoke(
            "model.request",
            key,
            context,
            request,
            BrokerReceipt,
            usage,
            lambda: self._facade._models.request(request.prompt, context),
        )

    def evidence_query(self, request: EvidenceQuery, *, step_key: str) -> BrokerReceipt:
        usage = BrokerUsage()
        context, key = self._prepare("evidence.query", step_key)
        return self._invoke(
            "evidence.query",
            key,
            context,
            request,
            BrokerReceipt,
            usage,
            lambda: self._facade._evidence.query(request.text, context),
        )

    def subagent_submit(
        self, request: SubagentSubmit, *, step_key: str
    ) -> RetainedSubagentHandle:
        usage = BrokerUsage(child_operations=1)
        context, key = self._prepare("subagent.submit", step_key)

        def submit() -> RetainedSubagentHandle:
            return RetainedSubagentHandle(
                handle=self._facade._subagents.submit(request.task, context)
            )

        return self._invoke(
            "subagent.submit",
            key,
            context,
            request,
            RetainedSubagentHandle,
            usage,
            submit,
        )

    def subagent_result(
        self, request: SubagentResultRequest, *, step_key: str
    ) -> BrokerReceipt:
        usage = BrokerUsage()
        context, key = self._prepare("subagent.result", step_key)
        return self._invoke(
            "subagent.result",
            key,
            context,
            request,
            BrokerReceipt,
            usage,
            lambda: self._facade._subagents.result(request.handle),
        )

    def effect_propose(self, request: EffectProposal, *, step_key: str) -> BrokerReceipt:
        usage = BrokerUsage()
        context, key = self._prepare("effect.propose", step_key)
        return self._invoke(
            "effect.propose",
            key,
            context,
            request,
            BrokerReceipt,
            usage,
            lambda: self._facade._effects.propose(
                request.effect_type, request.payload_digest, context
            ),
        )

    def _propose_compensation(
        self,
        request: EffectProposal,
        *,
        step_key: str,
        grant: Grant,
        control_revision: int,
    ) -> BrokerReceipt:
        now = self._facade._now_ms()
        deadline = min(
            self._envelope.deadline_unix_ms,
            grant.expires_at_unix_ms,
        )
        if now >= deadline:
            raise BrokerDeadlineExpired("compensation authority deadline has expired")
        if (
            grant.capability != "effect.propose"
            or grant.issued_to != self._envelope.principal
        ):
            raise BrokerGrantDenied(
                "current effect.propose grant is invalid for compensation"
            )
        self._grant("effect.propose")
        key_digest = canonical_sha256(
            {
                "operation": self._operation.value,
                "request": self._envelope.idempotency_key,
                "method": "effect.propose",
                "step": step_key,
                "grant_id": grant.grant_id,
                "control_revision": control_revision,
            }
        )
        key = f"broker-{key_digest.removeprefix('sha256:')[:32]}"
        context = BrokerContext(
            parent_operation=self._operation,
            grant_id=grant.grant_id,
            deadline_unix_ms=deadline,
            idempotency_key=key,
        )
        return cast(
            BrokerReceipt,
            self._facade._journal.invoke(
                operation=self._operation,
                method="effect.propose",
                grant_id=grant.grant_id,
                idempotency_key=key,
                request=request,
                response_type=BrokerReceipt,
                usage=BrokerUsage(),
                budget=self._envelope.budget,
                call=lambda: self._facade._effects.propose(
                    request.effect_type, request.payload_digest, context
                ),
                expected_control_revision=control_revision,
                require_not_cancelled=True,
            ),
        )

    def artifact_put(
        self,
        request: ArtifactPutRequest,
        content: bytes,
        *,
        step_key: str,
    ) -> ArtifactReference:
        actual_digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
        if actual_digest != request.content_digest or len(content) != request.size_bytes:
            raise ValueError("artifact content does not match declared digest and size")
        usage = BrokerUsage(artifact_bytes=len(content))
        context, key = self._prepare("artifact.put", step_key)
        return self._invoke(
            "artifact.put",
            key,
            context,
            request,
            ArtifactReference,
            usage,
            lambda: self._facade._artifacts.put(
                content,
                request.media_type,
                self._operation,
                redacted=request.redacted,
            ),
        )

    def artifact_read(
        self, request: ArtifactReadRequest, *, step_key: str
    ) -> ArtifactReadResult:
        usage = BrokerUsage(artifact_bytes=request.reference.size_bytes)
        context, key = self._prepare("artifact.read", step_key)

        def read() -> ArtifactReadResult:
            content = self._facade._artifacts.read(request.reference)
            return ArtifactReadResult(
                reference=request.reference,
                content_base64=base64.b64encode(content).decode("ascii"),
            )

        return self._invoke(
            "artifact.read",
            key,
            context,
            request,
            ArtifactReadResult,
            usage,
            read,
        )

    def _prepare(
        self,
        method: BrokerMethodName,
        step_key: str,
    ) -> tuple[BrokerContext, str]:
        now = self._facade._now_ms()
        if now >= self._envelope.deadline_unix_ms:
            raise BrokerDeadlineExpired("broker request deadline has expired")
        grant = self._grant(method)
        key_digest = canonical_sha256(
            {
                "operation": self._operation.value,
                "request": self._envelope.idempotency_key,
                "method": method,
                "step": step_key,
            }
        )
        key = f"broker-{key_digest.removeprefix('sha256:')[:32]}"
        return (
            BrokerContext(
                parent_operation=self._operation,
                grant_id=grant.grant_id,
                deadline_unix_ms=self._envelope.deadline_unix_ms,
                idempotency_key=key,
            ),
            key,
        )

    def _grant(self, method: BrokerMethodName) -> Grant:
        required_capability = _METHODS[method][0]
        matching = [
            grant
            for grant in self._envelope.grants
            if grant.capability == required_capability
            and grant.issued_to == self._envelope.principal
            and grant.expires_at_unix_ms >= self._envelope.deadline_unix_ms
        ]
        if not matching:
            raise BrokerGrantDenied(
                f"valid {required_capability} grant is absent for {method}"
            )
        return matching[0]

    def _invoke(
        self,
        method: BrokerMethodName,
        key: str,
        context: BrokerContext,
        request: StrictModel,
        response_type: type[StrictModel],
        usage: BrokerUsage,
        call: Callable[[], StrictModel],
    ):
        return self._facade._journal.invoke(
            operation=self._operation,
            method=method,
            grant_id=context.grant_id,
            idempotency_key=key,
            request=request,
            response_type=response_type,
            usage=usage,
            budget=self._envelope.budget,
            call=call,
        )


def _token_units(text: str) -> int:
    return max(1, len(text.split()))


def _assert_budget(budget: Budget, usage: BrokerUsage) -> None:
    counters = (
        "model_requests",
        "input_tokens",
        "output_tokens",
        "child_operations",
        "artifact_bytes",
    )
    exceeded = [name for name in counters if getattr(usage, name) > getattr(budget, name)]
    if exceeded:
        raise BrokerBudgetExceeded(
            "broker budget exceeded: " + ", ".join(sorted(exceeded))
        )
