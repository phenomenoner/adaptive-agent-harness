"""Provider-neutral model routing, durable execution, and conformance drivers."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from aar.broker_models import (
    BrokerContext,
    EffectiveModelRoute,
    ModelRequest,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.schemas import OperationRef


class ModelBrokerError(RuntimeError):
    """Base error for model routing rejected before a valid terminal response."""


class ModelRouteNotFound(ModelBrokerError):
    """The requested owner-authorized profile does not exist."""


class ModelRouteBindingStale(ModelBrokerError):
    """A binding no longer matches the active route catalog."""


class ModelRouteDrift(ModelBrokerError):
    """A driver returned a route receipt for anything but the exact bound profile."""


class ModelCallIndeterminate(ModelBrokerError):
    """A provider send may have occurred but no terminal receipt is retained."""


class ModelProviderOutcomeUnknown(ModelBrokerError):
    """A driver reports that provider execution may have occurred and needs lookup."""


class ModelProviderFailure(ModelBrokerError):
    """A certain provider failure with a bounded, retention-safe message."""


class ModelReceiptLookupUnavailable(ModelBrokerError):
    """The selected driver cannot look up an indeterminate provider call."""


class ModelReceiptLookupFailed(ModelBrokerError):
    """A provider receipt lookup failed without resolving the provider outcome."""


class UnsupportedModelJournalSchema(ModelBrokerError):
    """The durable model journal migration history is unsupported or tampered."""


MODEL_JOURNAL_SCHEMA_VERSION = 1

MODEL_JOURNAL_V1_DDL = """
CREATE TABLE IF NOT EXISTS model_schema_migrations (
    version INTEGER PRIMARY KEY,
    migration_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_route_bindings (
    operation_id TEXT PRIMARY KEY,
    profile_id TEXT NOT NULL,
    catalog_digest TEXT NOT NULL,
    profile_digest TEXT NOT NULL,
    binding_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_executions (
    operation_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    request_json TEXT NOT NULL,
    binding_json TEXT NOT NULL,
    state TEXT NOT NULL,
    response_digest TEXT,
    response_json TEXT,
    usage_json TEXT,
    failure_code TEXT,
    PRIMARY KEY(operation_id, idempotency_key)
);
""".strip()

_MODEL_JOURNAL_V1_COLUMNS = {
    "model_schema_migrations": (
        ("version", "INTEGER", 0, 1),
        ("migration_digest", "TEXT", 1, 0),
    ),
    "model_route_bindings": (
        ("operation_id", "TEXT", 0, 1),
        ("profile_id", "TEXT", 1, 0),
        ("catalog_digest", "TEXT", 1, 0),
        ("profile_digest", "TEXT", 1, 0),
        ("binding_json", "TEXT", 1, 0),
    ),
    "model_executions": (
        ("operation_id", "TEXT", 1, 1),
        ("idempotency_key", "TEXT", 1, 2),
        ("request_digest", "TEXT", 1, 0),
        ("request_json", "TEXT", 1, 0),
        ("binding_json", "TEXT", 1, 0),
        ("state", "TEXT", 1, 0),
        ("response_digest", "TEXT", 0, 0),
        ("response_json", "TEXT", 0, 0),
        ("usage_json", "TEXT", 0, 0),
        ("failure_code", "TEXT", 0, 0),
    ),
}


class ModelBroker(Protocol):
    def request(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse: ...

    def reconcile(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse | None: ...

    def close(self) -> None: ...


class ModelBrokerRegistry(Protocol):
    def bind(self, profile_id: str) -> ModelRouteBinding: ...

    def resolve(self, binding: ModelRouteBinding) -> ModelBroker: ...

    def request(
        self,
        binding: ModelRouteBinding,
        request: ModelRequest,
        context: BrokerContext,
    ) -> ModelResponse: ...

    def reconcile(
        self,
        binding: ModelRouteBinding,
        request: ModelRequest,
        context: BrokerContext,
    ) -> ModelResponse | None: ...

    def describe(self) -> ModelRouteCatalog: ...

    def close(self) -> None: ...


class StaticModelBrokerRegistry:
    """Resolve only exact profiles from one immutable owner-controlled catalog."""

    def __init__(
        self,
        catalog: ModelRouteCatalog,
        *,
        brokers: Mapping[str, ModelBroker],
    ) -> None:
        profile_ids = {item.profile_id for item in catalog.profiles}
        if set(brokers) != profile_ids:
            raise ValueError("model broker mapping must exactly cover the route catalog")
        self._catalog = catalog
        self._profiles = {item.profile_id: item for item in catalog.profiles}
        self._brokers = dict(brokers)
        self._closed = False

    def bind(self, profile_id: str) -> ModelRouteBinding:
        if self._closed:
            raise ModelBrokerError("model broker registry is closed")
        try:
            profile = self._profiles[profile_id]
        except KeyError as error:
            raise ModelRouteNotFound(profile_id) from error
        return ModelRouteBinding.issue(self._catalog, profile)

    def resolve(self, binding: ModelRouteBinding) -> ModelBroker:
        if self._closed:
            raise ModelBrokerError("model broker registry is closed")
        if binding.catalog_digest != self._catalog.catalog_digest:
            raise ModelRouteBindingStale("model route catalog digest changed")
        current = self.bind(binding.profile_id)
        if current != binding:
            raise ModelRouteBindingStale("model route profile digest changed")
        return self._brokers[binding.profile_id]

    def request(
        self,
        binding: ModelRouteBinding,
        request: ModelRequest,
        context: BrokerContext,
    ) -> ModelResponse:
        broker = self.resolve(binding)
        sanitized_error: ModelBrokerError | None = None
        response: ModelResponse | None = None
        try:
            response = broker.request(request, context, binding)
        except ModelProviderOutcomeUnknown:
            sanitized_error = ModelProviderOutcomeUnknown(
                "model provider outcome is unknown; receipt lookup is required"
            )
        except ModelProviderFailure:
            sanitized_error = ModelProviderFailure("model provider call failed")
        except ModelRouteDrift:
            sanitized_error = ModelRouteDrift("model provider route receipt drifted")
        if sanitized_error is not None:
            raise sanitized_error
        assert response is not None
        if response.route_receipt.requested != binding:
            raise ModelRouteDrift("model response does not attest the exact bound route")
        return response

    def reconcile(
        self,
        binding: ModelRouteBinding,
        request: ModelRequest,
        context: BrokerContext,
    ) -> ModelResponse | None:
        broker = self.resolve(binding)
        sanitized_error: ModelBrokerError | None = None
        response: ModelResponse | None = None
        try:
            response = broker.reconcile(request, context, binding)
        except ModelReceiptLookupUnavailable:
            sanitized_error = ModelReceiptLookupUnavailable(
                "model provider receipt lookup is unavailable"
            )
        except ModelReceiptLookupFailed:
            sanitized_error = ModelReceiptLookupFailed(
                "model provider receipt lookup failed"
            )
        except ModelRouteDrift:
            sanitized_error = ModelRouteDrift("model provider route receipt drifted")
        if sanitized_error is not None:
            raise sanitized_error
        if response is not None and response.route_receipt.requested != binding:
            raise ModelRouteDrift("recovered model response does not attest the exact bound route")
        return response

    def describe(self) -> ModelRouteCatalog:
        return self._catalog

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        seen: set[int] = set()
        for broker in self._brokers.values():
            if id(broker) in seen:
                continue
            seen.add(id(broker))
            broker.close()


class ModelExecutionJournal:
    """Persist exact route bindings plus terminal model response and usage bytes."""

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
        try:
            self._initialize()
        except Exception:
            self._connection.close()
            raise

    def _table_exists(self, table_name: str) -> bool:
        row = self._connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        ).fetchone()
        return row is not None

    def _execute_ddl_script(self, script: str) -> None:
        for statement in script.split(";"):
            statement = statement.strip()
            if statement:
                self._connection.execute(statement)

    def _initialize(self) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if self._table_exists("model_schema_migrations"):
                    rows = self._connection.execute(
                        """
                        SELECT version, migration_digest
                        FROM model_schema_migrations ORDER BY version
                        """
                    ).fetchall()
                    versions = [int(row["version"]) for row in rows]
                    if any(version > MODEL_JOURNAL_SCHEMA_VERSION for version in versions):
                        raise UnsupportedModelJournalSchema(
                            "model journal schema is newer than this runtime"
                        )
                    if versions != list(range(1, len(versions) + 1)):
                        raise UnsupportedModelJournalSchema(
                            f"model journal migration sequence is not contiguous: {versions}"
                        )
                    for row in rows:
                        version = int(row["version"])
                        if str(row["migration_digest"]) != self.migration_digest(version):
                            raise UnsupportedModelJournalSchema(
                                f"model journal migration {version} digest does not match"
                            )
                else:
                    versions = []

                self._apply_v1_schema()
                self._validate_v1_schema()
                if not versions:
                    self._insert_schema_version(MODEL_JOURNAL_SCHEMA_VERSION)
                self._connection.commit()
            except Exception:
                self._connection.rollback()
                raise

    def _apply_v1_schema(self) -> None:
        self._execute_ddl_script(MODEL_JOURNAL_V1_DDL)

    def _validate_v1_schema(self) -> None:
        for table, expected in _MODEL_JOURNAL_V1_COLUMNS.items():
            rows = self._connection.execute(f"PRAGMA table_info({table})").fetchall()
            actual = tuple(
                (str(row["name"]), str(row["type"]), int(row["notnull"]), int(row["pk"]))
                for row in rows
            )
            if actual != expected:
                raise UnsupportedModelJournalSchema(
                    f"model journal schema for {table} does not match v1"
                )
        expected_connection = sqlite3.connect(":memory:")
        try:
            expected_connection.executescript(MODEL_JOURNAL_V1_DDL)
            expected_objects = self._model_schema_objects(expected_connection)
        finally:
            expected_connection.close()
        actual_objects = self._model_schema_objects(self._connection)
        if actual_objects != expected_objects:
            raise UnsupportedModelJournalSchema(
                "model journal physical schema objects do not match v1"
            )

    @staticmethod
    def _model_schema_objects(
        connection: sqlite3.Connection,
    ) -> tuple[tuple[object, ...], ...]:
        objects = tuple(
            (
                str(row[0]),
                str(row[1]),
                str(row[2]),
            )
            for row in connection.execute(
                """
                SELECT type, name, tbl_name
                FROM sqlite_master
                WHERE name GLOB 'model_*' OR tbl_name GLOB 'model_*'
                ORDER BY type, name
                """
            ).fetchall()
        )
        indexes: list[tuple[object, ...]] = []
        for table in _MODEL_JOURNAL_V1_COLUMNS:
            for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
                name = str(row[1])
                columns = tuple(
                    str(column[2])
                    for column in connection.execute(
                        f"PRAGMA index_info({name})"
                    ).fetchall()
                )
                indexes.append(
                    (
                        "index_shape",
                        table,
                        name,
                        int(row[2]),
                        str(row[3]),
                        int(row[4]),
                        columns,
                    )
                )
        return objects + tuple(sorted(indexes))

    @staticmethod
    def migration_digest(version: int) -> str:
        if version != MODEL_JOURNAL_SCHEMA_VERSION:
            return canonical_sha256(
                {"registry": "aar.runtime.model-execution-journal", "version": version}
            )
        return canonical_sha256(
            {
                "registry": "aar.runtime.model-execution-journal",
                "version": version,
                "ddl": MODEL_JOURNAL_V1_DDL,
            }
        )

    def _insert_schema_version(self, version: int) -> None:
        self._connection.execute(
            """
            INSERT INTO model_schema_migrations(version, migration_digest)
            VALUES (?, ?)
            """,
            (version, self.migration_digest(version)),
        )

    def schema_versions(self) -> tuple[int, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT version FROM model_schema_migrations ORDER BY version"
            ).fetchall()
        return tuple(int(row["version"]) for row in rows)

    def bind_operation(
        self,
        operation: OperationRef,
        binding: ModelRouteBinding,
    ) -> ModelRouteBinding:
        with self._lock, self._connection:
            return self.bind_operation_in_transaction(
                self._connection,
                operation,
                binding,
            )

    def bind_operation_in_transaction(
        self,
        connection: sqlite3.Connection,
        operation: OperationRef,
        binding: ModelRouteBinding,
    ) -> ModelRouteBinding:
        binding_json = canonical_json_bytes(binding).decode()
        row = connection.execute(
            "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        if row is not None:
            existing = ModelRouteBinding.model_validate_json(
                str(row["binding_json"]), strict=True
            )
            if existing != binding or str(row["binding_json"]) != binding_json:
                raise ModelRouteBindingStale(
                    "operation already binds different model route bytes"
                )
            return existing
        connection.execute(
            """
            INSERT INTO model_route_bindings(
                operation_id, profile_id, catalog_digest, profile_digest, binding_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                operation.value,
                binding.profile_id,
                binding.catalog_digest,
                binding.profile_digest,
                binding_json,
            ),
        )
        return binding

    def binding(self, operation: OperationRef) -> ModelRouteBinding:
        with self._lock:
            row = self._connection.execute(
                "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        if row is None:
            raise ModelRouteNotFound(f"operation has no bound model route: {operation.value}")
        return ModelRouteBinding.model_validate_json(str(row["binding_json"]), strict=True)

    def invoke(
        self,
        *,
        operation: OperationRef,
        request: ModelRequest,
        context: BrokerContext,
        registry: ModelBrokerRegistry,
    ) -> ModelResponse:
        binding = self.binding(operation)
        request_json = canonical_json_bytes(request).decode()
        binding_json = canonical_json_bytes(binding).decode()
        request_digest = canonical_sha256(request)
        with self._lock, self._connection:
            row = self._connection.execute(
                """
                SELECT * FROM model_executions
                WHERE operation_id = ? AND idempotency_key = ?
                """,
                (operation.value, context.idempotency_key),
            ).fetchone()
            if row is not None:
                if (
                    str(row["request_digest"]) != request_digest
                    or str(row["request_json"]) != request_json
                    or str(row["binding_json"]) != binding_json
                ):
                    raise ModelRouteBindingStale(
                        "model idempotency identity binds different request or route bytes"
                    )
                if row["state"] != "result_committed" or row["response_json"] is None:
                    raise ModelCallIndeterminate(
                        "model call has no authoritative terminal response"
                    )
                return self._validated_response_row(row, expected_binding=binding)
            self._connection.execute(
                """
                INSERT INTO model_executions(
                    operation_id, idempotency_key, request_digest, request_json,
                    binding_json, state
                ) VALUES (?, ?, ?, ?, ?, 'provider_send_started')
                """,
                (
                    operation.value,
                    context.idempotency_key,
                    request_digest,
                    request_json,
                    binding_json,
                ),
            )
        provider_failure: ModelProviderFailure | None = None
        response: ModelResponse | None = None
        try:
            response = registry.request(binding, request, context)
        except ModelRouteDrift as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions
                    SET state = 'route_drift_quarantined', failure_code = ?
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (type(error).__name__, operation.value, context.idempotency_key),
                )
            raise
        except ModelProviderOutcomeUnknown as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions
                    SET state = 'provider_call_indeterminate', failure_code = ?
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (type(error).__name__, operation.value, context.idempotency_key),
                )
            raise ModelCallIndeterminate(
                "provider call outcome is unknown; receipt lookup is required"
            ) from error
        except ModelProviderFailure as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions
                    SET state = 'provider_failed_certain', failure_code = ?
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (type(error).__name__, operation.value, context.idempotency_key),
                )
            provider_failure = ModelProviderFailure("model provider call failed")
        except ModelBrokerError as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions
                    SET state = 'provider_failed_certain', failure_code = ?
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (type(error).__name__, operation.value, context.idempotency_key),
                )
            raise
        except Exception as error:
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions
                    SET state = 'provider_failed_certain', failure_code = ?
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (type(error).__name__, operation.value, context.idempotency_key),
                )
            provider_failure = ModelProviderFailure("model provider call failed")
        if provider_failure is not None:
            raise provider_failure
        assert response is not None
        response_json = canonical_json_bytes(response).decode()
        response_digest = canonical_sha256(response)
        usage_json = canonical_json_bytes(response.usage).decode()
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE model_executions
                SET state = 'provider_terminal_receipt', response_digest = ?, response_json = ?,
                    usage_json = ?, failure_code = NULL
                WHERE operation_id = ? AND idempotency_key = ?
                  AND state = 'provider_send_started'
                """,
                (
                    response_digest,
                    response_json,
                    usage_json,
                    operation.value,
                    context.idempotency_key,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ModelCallIndeterminate(
                    "model execution changed before terminal receipt commit"
                )
        self._after_terminal_receipt()
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE model_executions SET state = 'result_committed'
                WHERE operation_id = ? AND idempotency_key = ?
                  AND state = 'provider_terminal_receipt'
                """,
                (operation.value, context.idempotency_key),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                raise ModelCallIndeterminate(
                    "model execution changed before result commit"
                )
        return response

    def _after_terminal_receipt(self) -> None:
        """Lifecycle hook after durable receipt and before result/usage visibility."""

    def reconcile(
        self,
        *,
        operation: OperationRef,
        request: ModelRequest,
        context: BrokerContext,
        registry: ModelBrokerRegistry,
    ) -> ModelResponse | None:
        binding = self.binding(operation)
        request_json = canonical_json_bytes(request).decode()
        binding_json = canonical_json_bytes(binding).decode()
        request_digest = canonical_sha256(request)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM model_executions
                WHERE operation_id = ? AND idempotency_key = ?
                """,
                (operation.value, context.idempotency_key),
            ).fetchone()
        if row is None:
            raise ModelCallIndeterminate("model execution intent is unavailable")
        if (
            str(row["request_digest"]) != request_digest
            or str(row["request_json"]) != request_json
            or str(row["binding_json"]) != binding_json
        ):
            raise ModelRouteBindingStale(
                "model reconciliation identity binds different request or route bytes"
            )
        state = str(row["state"])
        if state == "result_committed":
            return self._validated_response_row(row, expected_binding=binding)
        if state == "provider_terminal_receipt":
            response = self._validated_response_row(row, expected_binding=binding)
            with self._lock, self._connection:
                self._connection.execute(
                    """
                    UPDATE model_executions SET state = 'result_committed'
                    WHERE operation_id = ? AND idempotency_key = ?
                      AND state = 'provider_terminal_receipt'
                    """,
                    (operation.value, context.idempotency_key),
                )
                if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                    current = self._connection.execute(
                        """
                        SELECT * FROM model_executions
                        WHERE operation_id = ? AND idempotency_key = ?
                        """,
                        (operation.value, context.idempotency_key),
                    ).fetchone()
                    if current is None or str(current["state"]) != "result_committed":
                        raise ModelCallIndeterminate(
                            "model receipt changed during local result recovery"
                        )
                    return self._validated_response_row(
                        current,
                        expected_binding=binding,
                    )
            return response
        if state not in {"provider_send_started", "provider_call_indeterminate"}:
            raise ModelCallIndeterminate(f"model execution is not lookup-reconcilable: {state}")
        try:
            response = registry.reconcile(binding, request, context)
        except ModelReceiptLookupUnavailable:
            self._quarantine(
                operation,
                context.idempotency_key,
                state="receipt_lookup_unavailable_quarantined",
                failure_code="ModelReceiptLookupUnavailable",
            )
            raise
        except ModelReceiptLookupFailed:
            self._quarantine(
                operation,
                context.idempotency_key,
                state="receipt_lookup_failed_quarantined",
                failure_code="ModelReceiptLookupFailed",
            )
            raise
        except (ModelRouteBindingStale, ModelRouteDrift) as error:
            self._quarantine(
                operation,
                context.idempotency_key,
                state="route_drift_quarantined",
                failure_code=type(error).__name__,
            )
            if isinstance(error, ModelRouteBindingStale):
                raise ModelRouteDrift(
                    "active model route catalog drifted from the durable binding"
                ) from error
            raise
        except (TypeError, ValueError) as error:
            self._quarantine(
                operation,
                context.idempotency_key,
                state="receipt_invalid_quarantined",
                failure_code=type(error).__name__,
            )
            raise ModelRouteDrift("provider receipt lookup returned invalid evidence") from error
        if response is None:
            return None
        response_json = canonical_json_bytes(response).decode()
        response_digest = canonical_sha256(response)
        usage_json = canonical_json_bytes(response.usage).decode()
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE model_executions
                SET state = 'result_committed', response_digest = ?, response_json = ?,
                    usage_json = ?, failure_code = NULL
                WHERE operation_id = ? AND idempotency_key = ?
                  AND state IN ('provider_send_started', 'provider_call_indeterminate')
                """,
                (
                    response_digest,
                    response_json,
                    usage_json,
                    operation.value,
                    context.idempotency_key,
                ),
            )
            if self._connection.execute("SELECT changes()").fetchone()[0] != 1:
                current = self._connection.execute(
                    """
                    SELECT * FROM model_executions
                    WHERE operation_id = ? AND idempotency_key = ?
                    """,
                    (operation.value, context.idempotency_key),
                ).fetchone()
                if current is None or str(current["state"]) != "result_committed":
                    raise ModelCallIndeterminate("model execution changed during result commit")
                existing = self._validated_response_row(
                    current,
                    expected_binding=binding,
                )
                if existing != response:
                    raise ModelRouteDrift(
                        "recovered model response conflicts with committed result"
                    )
                return existing
        return response

    def _validated_response_row(
        self,
        row: sqlite3.Row,
        *,
        expected_binding: ModelRouteBinding,
    ) -> ModelResponse:
        if row["response_json"] is None:
            raise ModelCallIndeterminate("model result state has no response bytes")
        response = ModelResponse.model_validate_json(str(row["response_json"]), strict=True)
        if str(row["response_digest"]) != canonical_sha256(response):
            raise ModelRouteDrift("stored model response digest mismatch")
        if str(row["binding_json"]) != canonical_json_bytes(expected_binding).decode():
            raise ModelRouteDrift("stored model execution binding differs from operation authority")
        if response.route_receipt.requested != expected_binding:
            raise ModelRouteDrift("stored model response binds a different route authority")
        if row["usage_json"] is None:
            raise ModelRouteDrift("stored model response has no usage projection")
        usage = ModelUsageRecord.model_validate_json(str(row["usage_json"]), strict=True)
        if usage != response.usage:
            raise ModelRouteDrift("stored model usage projection differs from response evidence")
        return response

    def _quarantine(
        self,
        operation: OperationRef,
        idempotency_key: str,
        *,
        state: str,
        failure_code: str,
    ) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                UPDATE model_executions SET state = ?, failure_code = ?
                WHERE operation_id = ? AND idempotency_key = ?
                  AND state != 'result_committed'
                """,
                (state, failure_code, operation.value, idempotency_key),
            )

    def usage_records(self, operation: OperationRef) -> tuple[ModelUsageRecord, ...]:
        binding = self.binding(operation)
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM model_executions
                WHERE operation_id = ? AND state = 'result_committed'
                ORDER BY idempotency_key
                """,
                (operation.value,),
            ).fetchall()
        return tuple(
            self._validated_response_row(row, expected_binding=binding).usage for row in rows
        )

    def response(self, operation: OperationRef, *, ordinal: int) -> ModelResponse:
        if ordinal < 0:
            raise ValueError("model response ordinal must be non-negative")
        binding = self.binding(operation)
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM model_executions
                WHERE operation_id = ? AND state = 'result_committed'
                ORDER BY rowid
                LIMIT 1 OFFSET ?
                """,
                (operation.value, ordinal),
            ).fetchone()
        if row is None or row["response_json"] is None:
            raise ModelCallIndeterminate("model response is not durably committed")
        return self._validated_response_row(row, expected_binding=binding)

    def close(self) -> None:
        with self._lock:
            self._connection.close()


class ReferenceModelBroker:
    """Credential-free deterministic broker used only for conformance and reference execution."""

    def __init__(self, *, output_prefix: str = "deterministic") -> None:
        self._output_prefix = output_prefix
        self._closed = False

    def request(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse:
        if self._closed:
            raise ModelBrokerError("reference model broker is closed")
        digest = canonical_sha256(
            {
                "prompt": request.prompt,
                "parent": context.parent_operation.value,
                "grant": context.grant_id,
                "route": binding,
            }
        )
        output = f"{self._output_prefix}:{digest.removeprefix('sha256:')[:16]}"
        effective = EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=binding.provider,
            model=binding.model,
            reasoning_effort=binding.reasoning_effort,
        )
        receipt = ModelRouteReceipt.issue(
            requested=binding,
            effective=effective,
            finish_reason="stop",
            provider_response_id=f"reference-{digest.removeprefix('sha256:')[:24]}",
        )
        input_tokens = max(1, len(request.prompt.split()))
        output_tokens = max(1, len(output.split()))
        return ModelResponse(
            output_text=output,
            route_receipt=receipt,
            usage=ModelUsageRecord(
                accounting_source="reference",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
            ),
        )

    def reconcile(
        self,
        request: ModelRequest,
        context: BrokerContext,
        binding: ModelRouteBinding,
    ) -> ModelResponse | None:
        del request, context, binding
        return None

    def close(self) -> None:
        self._closed = True
