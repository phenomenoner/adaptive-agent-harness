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
    BrokerCallTrace,
    BrokerCatalog,
    BrokerContext,
    BrokerContractSet,
    BrokerMethodName,
    BrokerReceipt,
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
                state TEXT NOT NULL,
                response_digest TEXT,
                response_model TEXT NOT NULL,
                response_json TEXT,
                usage_json TEXT NOT NULL,
                failure_code TEXT,
                PRIMARY KEY(operation_id, sequence),
                UNIQUE(operation_id, idempotency_key)
            );
            """
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
    ) -> StrictModel:
        request_digest = canonical_sha256(request)
        with self._lock, self._connection:
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
                    request_digest, state, response_model, usage_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'started', ?, ?)
                """,
                (
                    operation.value,
                    sequence,
                    method,
                    grant_id,
                    idempotency_key,
                    request_digest,
                    response_type.__name__,
                    canonical_json_bytes(usage).decode(),
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
                    )
                )
            return tuple(traces)

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
