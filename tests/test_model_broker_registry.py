from __future__ import annotations

import asyncio
import hashlib
import shutil
import sqlite3
import threading
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

from aar.broker_models import (
    BrokerContext,
    EffectiveModelRoute,
    ModelRequest,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteProfile,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.mcp import build_server as build_public_server
from aar.providers.gateway import (
    GatewayCallOutcomeUnknown,
    GatewayDriverManifest,
    GatewayModelResult,
    GatewayProviderCall,
    GatewayProviderFailure,
    OwnerGatewayModelBroker,
    parse_sampling_gateway_result,
)
from aar.rlm_models import RlmJobSpec, RlmResult
from aar.runtime.model_broker import (
    ModelBrokerError,
    ModelExecutionJournal,
    ModelProviderFailure,
    ModelProviderOutcomeUnknown,
    ModelReceiptLookupFailed,
    ModelRouteDrift,
    ModelRouteNotFound,
    ReferenceModelBroker,
    StaticModelBrokerRegistry,
    UnsupportedModelJournalSchema,
)
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.supervisor import SupervisorService
from aar.schemas import Budget, OperationRef, OperationState, PrincipalRef, SessionRef


def _context() -> BrokerContext:
    return BrokerContext(
        parent_operation=OperationRef(value="operation-model-registry"),
        grant_id="grant-model-request",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="model-call-1",
    )


def _create_legacy_model_journal(
    database: Path,
) -> tuple[
    OperationRef,
    ModelRouteBinding,
    ModelRequest,
    ModelResponse,
]:
    profile = ModelRouteProfile(
        profile_id="legacy-model-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="legacy-deterministic",
        max_output_tokens=32,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    binding = ModelRouteBinding.issue(catalog, profile)
    operation = OperationRef(value="operation-legacy-model-journal")
    request = ModelRequest(prompt="preserve these exact legacy bytes")
    context = BrokerContext(
        parent_operation=operation,
        grant_id="grant-legacy-model-journal",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="legacy-model-call-1",
    )
    response = ReferenceModelBroker(output_prefix="legacy").request(request, context, binding)
    binding_json = canonical_json_bytes(binding).decode()
    request_json = canonical_json_bytes(request).decode()
    response_json = canonical_json_bytes(response).decode()
    usage_json = canonical_json_bytes(response.usage).decode()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE model_route_bindings (
                operation_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                catalog_digest TEXT NOT NULL,
                profile_digest TEXT NOT NULL,
                binding_json TEXT NOT NULL
            );
            CREATE TABLE model_executions (
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
            """
        )
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
        connection.execute(
            """
            INSERT INTO model_executions(
                operation_id, idempotency_key, request_digest, request_json,
                binding_json, state, response_digest, response_json, usage_json
            ) VALUES (?, ?, ?, ?, ?, 'result_committed', ?, ?, ?)
            """,
            (
                operation.value,
                context.idempotency_key,
                canonical_sha256(request),
                request_json,
                binding_json,
                canonical_sha256(response),
                response_json,
                usage_json,
            ),
        )
    return operation, binding, request, response


def test_model_journal_migration_preserves_legacy_bytes_and_rollback_copy(
    tmp_path: Path,
) -> None:
    database = tmp_path / "legacy-model-journal.sqlite3"
    operation, binding, request, response = _create_legacy_model_journal(database)
    rollback_copy = tmp_path / "legacy-model-journal.rollback.sqlite3"
    shutil.copy2(database, rollback_copy)
    rollback_digest = hashlib.sha256(rollback_copy.read_bytes()).hexdigest()

    with sqlite3.connect(database) as connection:
        before_binding = connection.execute(
            "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        before_execution = connection.execute(
            """
            SELECT request_json, binding_json, response_json, usage_json
            FROM model_executions WHERE operation_id = ?
            """,
            (operation.value,),
        ).fetchone()

    journal = ModelExecutionJournal(database)
    try:
        assert journal.schema_versions() == (1,)
        assert journal.binding(operation) == binding
        assert journal.response(operation, ordinal=0) == response
        assert journal.usage_records(operation) == (response.usage,)
        with sqlite3.connect(database) as connection:
            after_binding = connection.execute(
                "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            after_execution = connection.execute(
                """
                SELECT request_json, binding_json, response_json, usage_json
                FROM model_executions WHERE operation_id = ?
                """,
                (operation.value,),
            ).fetchone()
            migration = connection.execute(
                """
                SELECT version, migration_digest
                FROM model_schema_migrations ORDER BY version
                """
            ).fetchall()
        assert after_binding == before_binding
        assert after_execution == before_execution
        assert migration == [(1, ModelExecutionJournal.migration_digest(1))]
        assert canonical_json_bytes(request).decode() == before_execution[0]
    finally:
        journal.close()

    assert hashlib.sha256(rollback_copy.read_bytes()).hexdigest() == rollback_digest
    with sqlite3.connect(rollback_copy) as legacy_reader:
        tables = {
            str(row[0])
            for row in legacy_reader.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "model_schema_migrations" not in tables
        assert (
            legacy_reader.execute(
                "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
            == before_binding
        )


def test_interrupted_model_journal_migration_rolls_back_and_reopen_converges(
    tmp_path: Path,
) -> None:
    class InterruptedModelExecutionJournal(ModelExecutionJournal):
        def _apply_v1_schema(self) -> None:
            super()._apply_v1_schema()
            raise RuntimeError("simulated model journal migration interruption")

    database = tmp_path / "interrupted-model-journal.sqlite3"
    operation, binding, _request, response = _create_legacy_model_journal(database)

    with pytest.raises(RuntimeError, match="simulated model journal migration interruption"):
        InterruptedModelExecutionJournal(database)

    with sqlite3.connect(database) as connection:
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "model_schema_migrations" not in tables
        assert connection.execute(
            "SELECT binding_json FROM model_route_bindings WHERE operation_id = ?",
            (operation.value,),
        ).fetchone() == (canonical_json_bytes(binding).decode(),)

    recovered = ModelExecutionJournal(database)
    try:
        assert recovered.schema_versions() == (1,)
        assert recovered.binding(operation) == binding
        assert recovered.response(operation, ordinal=0) == response
        assert recovered.usage_records(operation) == (response.usage,)
    finally:
        recovered.close()


@pytest.mark.parametrize("tamper", ["newer", "digest"])
def test_model_journal_schema_history_fails_closed(
    tmp_path: Path,
    tamper: str,
) -> None:
    database = tmp_path / f"model-journal-{tamper}.sqlite3"
    journal = ModelExecutionJournal(database)
    journal.close()
    with sqlite3.connect(database) as connection:
        if tamper == "newer":
            connection.execute(
                """
                INSERT INTO model_schema_migrations(version, migration_digest)
                VALUES (?, ?)
                """,
                (2, canonical_sha256({"unsupported": 2})),
            )
        else:
            connection.execute(
                "UPDATE model_schema_migrations SET migration_digest = ? WHERE version = 1",
                (canonical_sha256({"tampered": True}),),
            )

    expected = "newer" if tamper == "newer" else "digest"
    with pytest.raises(UnsupportedModelJournalSchema, match=expected):
        ModelExecutionJournal(database)


def test_model_journal_rejects_malformed_schema_with_valid_version_digest(
    tmp_path: Path,
) -> None:
    database = tmp_path / "malformed-model-journal.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE model_schema_migrations (
                version INTEGER PRIMARY KEY,
                migration_digest TEXT NOT NULL
            );
            CREATE TABLE model_route_bindings (
                operation_id TEXT PRIMARY KEY,
                profile_id TEXT NOT NULL,
                catalog_digest TEXT NOT NULL,
                profile_digest TEXT NOT NULL,
                binding_json TEXT NOT NULL
            );
            CREATE TABLE model_executions (
                operation_id TEXT PRIMARY KEY,
                state TEXT NOT NULL
            );
            """
        )
        connection.execute(
            "INSERT INTO model_schema_migrations(version, migration_digest) VALUES (1, ?)",
            (ModelExecutionJournal.migration_digest(1),),
        )

    with pytest.raises(UnsupportedModelJournalSchema, match="schema"):
        ModelExecutionJournal(database)


@pytest.mark.parametrize(
    "extra_schema",
    (
        "CREATE INDEX model_executions_state_idx ON model_executions(state)",
        """
        CREATE TRIGGER model_executions_guard
        AFTER INSERT ON model_executions BEGIN
            SELECT 1;
        END
        """,
        "CREATE VIEW model_execution_states AS SELECT state FROM model_executions",
    ),
)
def test_model_journal_rejects_unexpected_physical_schema_objects(
    tmp_path: Path,
    extra_schema: str,
) -> None:
    database = tmp_path / "unexpected-model-schema.sqlite3"
    journal = ModelExecutionJournal(database)
    journal.close()
    with sqlite3.connect(database) as connection:
        connection.execute(extra_schema)

    with pytest.raises(UnsupportedModelJournalSchema, match="physical schema"):
        ModelExecutionJournal(database)


def test_model_route_catalog_rejects_unbounded_profile_projection() -> None:
    profiles = tuple(
        ModelRouteProfile(
            profile_id=f"bounded-profile-{index:02d}",
            provider_driver="owner-gateway-v1",
            provider="provider",
            model="model",
            max_output_tokens=64,
        )
        for index in range(65)
    )

    with pytest.raises(ValueError, match="at most 64"):
        ModelRouteCatalog.issue(profiles)


def test_model_route_binding_rejects_profile_outside_catalog() -> None:
    authorized = ModelRouteProfile(
        profile_id="authorized-profile-v1",
        provider_driver="reference-driver-v1",
        provider="reference",
        model="authorized-model",
        max_output_tokens=64,
    )
    foreign = authorized.model_copy(
        update={"profile_id": "foreign-profile-v1", "model": "foreign-model"}
    )
    catalog = ModelRouteCatalog.issue((authorized,))

    with pytest.raises(ValueError, match="profile is not an exact member"):
        ModelRouteBinding.issue(catalog, foreign)


def test_atomic_model_route_admission_rolls_back_operation_and_binding_together(
    tmp_path: Path,
) -> None:
    profile = ModelRouteProfile(
        profile_id="atomic-admission-v1",
        provider_driver="reference-driver-v1",
        provider="reference",
        model="atomic-model",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker()},
    )
    database = tmp_path / "atomic-model-admission.sqlite3"
    host = ReferenceHost(
        database,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="atomic admission", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-atomic-admission",
        idempotency_key="idempotency-atomic-admission",
        principal=PrincipalRef(value="principal-atomic-admission"),
        session=SessionRef(value="session-atomic-admission"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    assert host.model_executions is not None
    original = host.model_executions.bind_operation_in_transaction

    def lose_after_binding(connection, operation, binding):
        original(connection, operation, binding)
        raise RuntimeError("simulated process loss during atomic route admission")

    host.model_executions.bind_operation_in_transaction = lose_after_binding  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError, match="atomic route admission"):
            host.submit_rlm(envelope, spec)
    finally:
        host.close()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM operations").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM model_route_bindings").fetchone() == (0,)

    reopened = ReferenceHost(
        database,
        model_broker_registry=StaticModelBrokerRegistry(
            catalog,
            brokers={profile.profile_id: ReferenceModelBroker()},
        ),
        default_model_route_profile=profile.profile_id,
    )
    try:
        accepted = reopened.submit_rlm(
            envelope.model_copy(update={"runtime_generation": reopened.runtime_generation}),
            spec,
        )
        assert accepted.state is OperationState.ACCEPTED
        assert reopened.model_route_binding(accepted.operation).profile_id == profile.profile_id
    finally:
        reopened.close()


@pytest.mark.parametrize("tamper", ("response_binding", "usage_projection"))
def test_model_journal_rejects_cross_table_response_tampering(
    tmp_path: Path,
    tamper: str,
) -> None:
    profile = ModelRouteProfile(
        profile_id="relational-integrity-v1",
        provider_driver="reference-driver-v1",
        provider="reference",
        model="integrity-model",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    binding = ModelRouteBinding.issue(catalog, profile)
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker()},
    )
    database = tmp_path / f"relational-{tamper}.sqlite3"
    journal = ModelExecutionJournal(database)
    operation = OperationRef(value=f"operation-relational-{tamper}")
    request = ModelRequest(prompt="bind this response to durable authority")
    context = BrokerContext(
        parent_operation=operation,
        grant_id="grant-relational-integrity",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="model-call-relational-integrity",
    )
    journal.bind_operation(operation, binding)
    original = journal.invoke(
        operation=operation,
        request=request,
        context=context,
        registry=registry,
    )
    journal.close()

    with sqlite3.connect(database) as connection:
        if tamper == "response_binding":
            foreign_profile = ModelRouteProfile(
                profile_id="foreign-route-v1",
                provider_driver="reference-driver-v1",
                provider="foreign-provider",
                model="foreign-model",
                max_output_tokens=64,
            )
            foreign_catalog = ModelRouteCatalog.issue((foreign_profile,))
            foreign = ModelRouteBinding.issue(foreign_catalog, foreign_profile)
            forged = ReferenceModelBroker(output_prefix="forged").request(
                request,
                context,
                foreign,
            )
            connection.execute(
                """
                UPDATE model_executions SET response_json = ?, response_digest = ?
                WHERE operation_id = ?
                """,
                (
                    canonical_json_bytes(forged).decode(),
                    canonical_sha256(forged),
                    operation.value,
                ),
            )
        else:
            forged_usage = original.usage.model_copy(
                update={
                    "input_tokens": original.usage.input_tokens + 1,
                    "total_tokens": original.usage.total_tokens + 1,
                }
            )
            connection.execute(
                "UPDATE model_executions SET usage_json = ? WHERE operation_id = ?",
                (canonical_json_bytes(forged_usage).decode(), operation.value),
            )

    reopened = ModelExecutionJournal(database)
    try:
        with pytest.raises(ModelRouteDrift):
            reopened.invoke(
                operation=operation,
                request=request,
                context=context,
                registry=registry,
            )
        with pytest.raises(ModelRouteDrift):
            reopened.response(operation, ordinal=0)
        with pytest.raises(ModelRouteDrift):
            reopened.usage_records(operation)
    finally:
        reopened.close()
        registry.close()


def test_registry_binds_authorized_profile_before_selecting_broker(tmp_path: Path) -> None:
    del tmp_path  # The first registry slice is intentionally credential- and database-free.
    profiles = (
        ModelRouteProfile(
            profile_id="reference-fake-v1",
            provider_driver="reference-fake-driver-v1",
            provider="reference",
            model="deterministic-a",
            reasoning_effort=None,
            max_output_tokens=64,
            fallback_policy="none",
            cache_policy="disabled",
        ),
        ModelRouteProfile(
            profile_id="reference-alt-v1",
            provider_driver="reference-fake-driver-v1",
            provider="reference",
            model="deterministic-b",
            reasoning_effort=None,
            max_output_tokens=64,
            fallback_policy="none",
            cache_policy="disabled",
        ),
    )
    catalog = ModelRouteCatalog.issue(profiles)
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={
            "reference-fake-v1": ReferenceModelBroker(output_prefix="primary"),
            "reference-alt-v1": ReferenceModelBroker(output_prefix="alternate"),
        },
    )

    primary_binding = registry.bind("reference-fake-v1")
    alternate_binding = registry.bind("reference-alt-v1")
    assert primary_binding.catalog_digest == catalog.catalog_digest
    assert primary_binding.profile_digest != alternate_binding.profile_digest

    request = ModelRequest(prompt="route me")
    primary = registry.resolve(primary_binding).request(request, _context(), primary_binding)
    alternate = registry.resolve(alternate_binding).request(request, _context(), alternate_binding)
    assert primary.output_text.startswith("primary:")
    assert alternate.output_text.startswith("alternate:")
    assert primary.route_receipt.requested == primary_binding
    assert alternate.route_receipt.requested == alternate_binding

    with pytest.raises(ModelRouteNotFound):
        registry.bind("missing-profile")


def test_route_catalog_round_trips_canonical_json() -> None:
    profile = ModelRouteProfile(
        profile_id="round-trip-v1",
        provider_driver="driver-v1",
        provider="provider",
        model="model",
        max_output_tokens=32,
    )
    catalog = ModelRouteCatalog.issue((profile,))

    assert (
        ModelRouteCatalog.model_validate_json(canonical_json_bytes(catalog), strict=True) == catalog
    )


def test_route_binding_rejects_profile_digest_drift() -> None:
    profile = ModelRouteProfile(
        profile_id="bound-v1",
        provider_driver="driver-v1",
        provider="provider",
        model="model-a",
        max_output_tokens=32,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    binding = ModelRouteBinding.issue(catalog, profile)
    tampered = binding.model_dump(mode="json")
    tampered["model"] = "model-b"

    with pytest.raises(ValidationError, match="profile digest"):
        ModelRouteBinding.model_validate(tampered, strict=True)


def test_registry_rejects_digest_valid_response_for_a_different_binding() -> None:
    profiles = (
        ModelRouteProfile(
            profile_id="route-a",
            provider_driver="driver-v1",
            provider="provider-a",
            model="model-a",
            reasoning_effort="high",
            max_output_tokens=32,
        ),
        ModelRouteProfile(
            profile_id="route-b",
            provider_driver="driver-v1",
            provider="provider-b",
            model="model-b",
            reasoning_effort="low",
            max_output_tokens=32,
        ),
    )
    catalog = ModelRouteCatalog.issue(profiles)
    route_a = ModelRouteBinding.issue(catalog, catalog.profiles[0])
    route_b = ModelRouteBinding.issue(catalog, catalog.profiles[1])

    class LyingBroker:
        def request(self, request, context, binding):
            del request, context, binding
            effective = EffectiveModelRoute(
                provider_driver=route_b.provider_driver,
                provider=route_b.provider,
                model=route_b.model,
                reasoning_effort=route_b.reasoning_effort,
            )
            return ModelResponse(
                output_text="wrong route",
                route_receipt=ModelRouteReceipt.issue(
                    requested=route_b,
                    effective=effective,
                    finish_reason="stop",
                ),
                usage=ModelUsageRecord(
                    accounting_source="provider_reported",
                    input_tokens=1,
                    output_tokens=2,
                    total_tokens=3,
                ),
            )

        def close(self) -> None:
            pass

    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={"route-a": LyingBroker(), "route-b": ReferenceModelBroker()},
    )

    with pytest.raises(ModelRouteDrift):
        registry.request(route_a, ModelRequest(prompt="do not drift"), _context())


def test_immediate_route_drift_is_durably_quarantined(tmp_path: Path) -> None:
    profile = ModelRouteProfile(
        profile_id="immediate-drift-v1",
        provider_driver="lying-driver-v1",
        provider="expected-provider",
        model="expected-model",
        reasoning_effort=None,
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))

    class LyingBroker:
        def request(self, request, context, binding):
            del request, context
            foreign_profile = ModelRouteProfile(
                profile_id="foreign-immediate-v1",
                provider_driver="lying-driver-v1",
                provider="foreign-provider",
                model="foreign-model",
                reasoning_effort=None,
                max_output_tokens=64,
            )
            foreign_binding = ModelRouteBinding.issue(
                ModelRouteCatalog.issue((foreign_profile,)), foreign_profile
            )
            return ReferenceModelBroker().request(
                ModelRequest(prompt="foreign response"), _context(), foreign_binding
            )

        def reconcile(self, request, context, binding):
            del request, context, binding
            raise AssertionError("immediate route drift must not enter reconciliation")

        def close(self) -> None:
            pass

    database = tmp_path / "immediate-route-drift.sqlite3"
    host = ReferenceHost(
        database,
        model_broker_registry=StaticModelBrokerRegistry(
            catalog, brokers={profile.profile_id: LyingBroker()}
        ),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="reject immediate drift", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-immediate-drift",
        idempotency_key="idempotency-immediate-drift",
        principal=PrincipalRef(value="principal-immediate-drift"),
        session=SessionRef(value="session-immediate-drift"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    failed = host.execute_rlm(envelope, spec)
    operation = failed.operation
    assert failed.state is OperationState.FAILED
    trace = host.brokers.bind(envelope, operation).traces()[0]
    assert trace.state == "failed"
    assert trace.reconciliation_action == "quarantine"
    assert trace.failure_code == "model_route_drift"
    host.close()

    with sqlite3.connect(database) as connection:
        execution = connection.execute(
            "SELECT state, failure_code FROM model_executions WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
    assert execution == ("route_drift_quarantined", "ModelRouteDrift")


def test_reference_host_persists_selected_route_and_provider_usage(tmp_path: Path) -> None:
    profile = ModelRouteProfile(
        profile_id="reference-alt-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="deterministic-alt",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker(output_prefix="alternate")},
    )
    database = tmp_path / "host-route.sqlite3"
    host = ReferenceHost(
        database,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="bound route", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-bound-route",
        idempotency_key="idempotency-bound-route",
        principal=PrincipalRef(value="principal-bound-route"),
        session=SessionRef(value="session-bound-route"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    completed = host.execute_rlm(envelope, spec)
    operation = completed.operation
    assert completed.state is OperationState.SUCCEEDED
    result = RlmResult.model_validate_json(completed.result_json or "null", strict=True)
    assert result.answer.startswith("alternate:")
    assert host.model_route_binding(operation).profile_id == profile.profile_id
    usage = host.model_usage_records(operation)
    assert len(usage) == 1
    assert usage[0].accounting_source == "reference"
    host.close()

    rebound_registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker(output_prefix="alternate")},
    )
    rebound = ReferenceHost(
        database,
        model_broker_registry=rebound_registry,
        default_model_route_profile=profile.profile_id,
    )
    try:
        assert rebound.model_route_binding(operation) == ModelRouteBinding.issue(catalog, profile)
        assert rebound.model_usage_records(operation) == usage
    finally:
        rebound.close()


def test_public_mcp_builder_preserves_model_registry_injection(tmp_path: Path) -> None:
    profile = ModelRouteProfile(
        profile_id="public-builder-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="deterministic-public",
        max_output_tokens=32,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: ReferenceModelBroker()},
    )

    application = build_public_server(
        tmp_path / "public-builder.sqlite3",
        programmable_backend="plain",
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    try:
        assert application.host.model_broker_registry is registry
        assert application.host.default_model_route_profile == profile.profile_id
    finally:
        application.close()


def test_model_registry_failed_child_close_is_retryable_and_fail_closed() -> None:
    class CountingBroker(ReferenceModelBroker):
        def __init__(self, *, fail_first_close: bool = False) -> None:
            super().__init__()
            self.close_calls = 0
            self.fail_first_close = fail_first_close

        def close(self) -> None:
            self.close_calls += 1
            if self.fail_first_close and self.close_calls == 1:
                raise RuntimeError("model broker close failed")
            super().close()

    first_profile = ModelRouteProfile(
        profile_id="close-first-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="close-first",
        max_output_tokens=32,
    )
    retry_profile = ModelRouteProfile(
        profile_id="close-retry-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="close-retry",
        max_output_tokens=32,
    )
    first_broker = CountingBroker()
    retry_broker = CountingBroker(fail_first_close=True)
    registry = StaticModelBrokerRegistry(
        ModelRouteCatalog.issue((first_profile, retry_profile)),
        brokers={
            first_profile.profile_id: first_broker,
            retry_profile.profile_id: retry_broker,
        },
    )

    with pytest.raises(RuntimeError, match="model broker close failed"):
        registry.close()
    assert first_broker.close_calls == 1
    assert first_broker._closed is True
    assert retry_broker.close_calls == 1
    assert retry_broker._closed is False
    with pytest.raises(ModelBrokerError, match="registry is closed"):
        registry.bind(first_profile.profile_id)

    registry.close()
    assert first_broker.close_calls == 1
    assert retry_broker.close_calls == 2
    assert retry_broker._closed is True


def test_model_registry_concurrent_close_is_single_flight() -> None:
    close_started = threading.Event()
    release_close = threading.Event()

    class BlockingBroker(ReferenceModelBroker):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            close_started.set()
            assert release_close.wait(timeout=5)
            if self.close_calls == 1:
                raise RuntimeError("first concurrent close failed")
            super().close()

    profile = ModelRouteProfile(
        profile_id="close-concurrent-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="close-concurrent",
        max_output_tokens=32,
    )
    broker = BlockingBroker()
    registry = StaticModelBrokerRegistry(
        ModelRouteCatalog.issue((profile,)),
        brokers={profile.profile_id: broker},
    )
    errors: list[BaseException] = []
    second_entered = threading.Event()

    def close_registry(*, signal_entry: bool = False) -> None:
        if signal_entry:
            second_entered.set()
        try:
            registry.close()
        except BaseException as error:
            errors.append(error)

    first = threading.Thread(target=close_registry)
    second = threading.Thread(target=close_registry, kwargs={"signal_entry": True})
    first.start()
    assert close_started.wait(timeout=5)
    second.start()
    assert second_entered.wait(timeout=5)
    assert second.is_alive()
    assert broker.close_calls == 1

    release_close.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert not first.is_alive()
    assert not second.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert str(errors[0]) == "first concurrent close failed"
    assert broker.close_calls == 2
    assert broker._closed is True

    registry.close()
    assert broker.close_calls == 2


def test_supervisor_startup_preserves_model_registry_ownership(tmp_path: Path) -> None:
    class ClosingBroker(ReferenceModelBroker):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        def close(self) -> None:
            self.close_calls += 1
            super().close()

    async def scenario() -> None:
        profile = ModelRouteProfile(
            profile_id="supervisor-route-v1",
            provider_driver="reference-fake-driver-v1",
            provider="reference",
            model="deterministic-supervisor",
            max_output_tokens=32,
        )
        broker = ClosingBroker()
        registry = StaticModelBrokerRegistry(
            ModelRouteCatalog.issue((profile,)),
            brokers={profile.profile_id: broker},
        )
        service = SupervisorService(
            tmp_path / "supervisor-runtime",
            programmable_backend="plain",
            transport="tcp",
            model_broker_registry=registry,
            default_model_route_profile=profile.profile_id,
        )
        await service._start()
        try:
            assert service.application is not None
            assert service.application.host.model_broker_registry is registry
        finally:
            await service._shutdown()
        assert broker.close_calls == 1

    asyncio.run(scenario())


def test_owner_gateway_returns_authoritative_route_and_usage_without_persisting_secret(
    tmp_path: Path,
) -> None:
    credential_canary = b"credential-canary-do-not-persist"
    profile = ModelRouteProfile(
        profile_id="gateway-luna-max-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    observed: dict[str, object] = {}

    class Transport:
        def send(self, call, request, binding, credential):
            observed.update(
                call=call,
                request=request,
                binding=binding,
                credential=credential,
            )
            return GatewayModelResult(
                provider_request_id=call.provider_request_id,
                output_text="gateway answer",
                provider="openai-codex",
                model="gpt-5.6-luna",
                reasoning_effort="max",
                finish_reason="stop",
                input_tokens=11,
                output_tokens=7,
                total_tokens=18,
            )

        def lookup(self, call, binding, credential):
            del call, binding, credential
            return None

        def close(self) -> None:
            pass

    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="owner-gateway-v1",
            driver_version="1.0.0",
            lookup_supported=True,
            cancellation_supported=False,
        ),
        transport=Transport(),
        credential_resolver=lambda _binding: credential_canary,
    )
    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: broker},
    )
    database = tmp_path / "gateway.sqlite3"
    host = ReferenceHost(
        database,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="fixed synthetic prompt", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-gateway",
        idempotency_key="idempotency-gateway",
        principal=PrincipalRef(value="principal-gateway"),
        session=SessionRef(value="session-gateway"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    completed = host.execute_rlm(envelope, spec)
    operation = completed.operation
    response = host.model_response(operation, ordinal=0)
    assert response.output_text == "gateway answer"
    assert response.route_receipt.effective.provider == "openai-codex"
    assert response.route_receipt.effective.model == "gpt-5.6-luna"
    assert response.route_receipt.effective.reasoning_effort == "max"
    assert response.usage.accounting_source == "provider_reported"
    assert response.usage.total_tokens == 18
    assert observed["credential"] == credential_canary
    host.close()

    with sqlite3.connect(database) as connection:
        retained = b"\n".join(
            str(value).encode()
            for row in connection.execute(
                "SELECT request_json, binding_json, response_json, usage_json FROM model_executions"
            )
            for value in row
            if value is not None
        )
    assert credential_canary not in retained


def test_owner_gateway_preserves_route_drift_from_transport() -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-drift-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)

    class DriftTransport:
        def send(self, call, request, actual_binding, credential):
            del call, request, actual_binding, credential
            raise ModelRouteDrift("sampling receipt route drifted")

        def lookup(self, call, actual_binding, credential):
            del call, actual_binding, credential
            return None

        def close(self) -> None:
            pass

    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="owner-gateway-v1",
            driver_version="1.0.0",
            lookup_supported=False,
            cancellation_supported=False,
        ),
        transport=DriftTransport(),
        credential_resolver=lambda _binding: b"ephemeral",
    )

    with pytest.raises(ModelRouteDrift, match="sampling receipt route drifted"):
        broker.request(ModelRequest(prompt="reject drift"), _context(), binding)


def test_sampling_gateway_result_parses_exact_route_and_provider_usage() -> None:
    from mcp.types import CreateMessageResult, TextContent

    profile = ModelRouteProfile(
        profile_id="gateway-luna-max-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    catalog = ModelRouteCatalog.issue((profile,))
    binding = ModelRouteBinding.issue(catalog, profile)
    request = ModelRequest(prompt="qualify the route")
    context = _context()
    call = GatewayProviderCall.issue(request, context, binding)
    result = CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text="qualified"),
        model="gpt-5.6-luna",
        stopReason="endTurn",
        _meta={
            "aar.model-receipt.v1": {
                "schema_version": "aar.model-receipt.v1",
                "provider_request_id": call.provider_request_id,
                "provider": "openai-codex",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "finish_reason": "stop",
                "input_tokens": 11,
                "output_tokens": 7,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": 18,
                "retry_count": 0,
                "fallback_chain": [],
            }
        },
    )

    parsed = parse_sampling_gateway_result(call, binding, result)

    assert parsed == GatewayModelResult(
        provider_request_id=call.provider_request_id,
        output_text="qualified",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        finish_reason="stop",
        input_tokens=11,
        output_tokens=7,
        cache_read_tokens=None,
        cache_write_tokens=None,
        reasoning_tokens=None,
        total_tokens=18,
    )


def test_sampling_gateway_retry_count_reaches_usage_ordinal() -> None:
    from mcp.types import CreateMessageResult, TextContent

    profile = ModelRouteProfile(
        profile_id="gateway-luna-retry-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)
    call = GatewayProviderCall.issue(
        ModelRequest(prompt="retain retry ordinal"),
        _context(),
        binding,
    )
    result = CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text="qualified after retry"),
        model="gpt-5.6-luna",
        stopReason="endTurn",
        _meta={
            "aar.model-receipt.v1": {
                "schema_version": "aar.model-receipt.v1",
                "provider_request_id": call.provider_request_id,
                "provider": "openai-codex",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "max",
                "finish_reason": "stop",
                "input_tokens": 5,
                "output_tokens": 3,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
                "reasoning_tokens": None,
                "total_tokens": 8,
                "retry_count": 2,
                "fallback_chain": [],
            }
        },
    )

    parsed = parse_sampling_gateway_result(call, binding, result)

    assert parsed.retry_ordinal == 2


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ({"provider": "other-provider"}, "route drifted"),
        ({"model": "other-model"}, "route drifted"),
        ({"reasoning_effort": "low"}, "route drifted"),
        ({"provider_request_id": "aar-other-request"}, "identity"),
        (
            {
                "fallback_chain": [
                    {
                        "provider": "other-provider",
                        "model": "other-model",
                        "reasoning_effort": "max",
                    }
                ]
            },
            "forbidden fallback",
        ),
        ({"total_tokens": 99}, "usage total"),
        ({"cache_read_tokens": 5}, "disabled cache policy"),
        ({"cache_write_tokens": 5}, "disabled cache policy"),
        ({"input_tokens": None}, "receipt schema is invalid"),
    ),
)
def test_sampling_gateway_receipt_drift_fails_closed(
    mutation: dict[str, object],
    expected: str,
) -> None:
    from mcp.types import CreateMessageResult, TextContent

    profile = ModelRouteProfile(
        profile_id="gateway-luna-drift-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)
    call = GatewayProviderCall.issue(
        ModelRequest(prompt="reject receipt drift"),
        _context(),
        binding,
    )
    receipt: dict[str, object] = {
        "schema_version": "aar.model-receipt.v1",
        "provider_request_id": call.provider_request_id,
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort": "max",
        "finish_reason": "stop",
        "input_tokens": 5,
        "output_tokens": 3,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "reasoning_tokens": None,
        "total_tokens": 8,
        "retry_count": 0,
        "fallback_chain": [],
    }
    receipt.update(mutation)
    result = CreateMessageResult(
        role="assistant",
        content=TextContent(type="text", text="must not be accepted"),
        model=str(receipt["model"]),
        stopReason="endTurn",
        _meta={"aar.model-receipt.v1": receipt},
    )

    with pytest.raises(ModelRouteDrift, match=expected):
        parse_sampling_gateway_result(call, binding, result)


def test_owner_gateway_classifies_malformed_sampling_receipt_as_route_drift() -> None:
    from mcp.types import CreateMessageResult, TextContent

    profile = ModelRouteProfile(
        profile_id="gateway-malformed-receipt-v1",
        provider_driver="hermes-mcp-sampling-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
        fallback_policy="none",
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)

    class MalformedReceiptTransport:
        def send(self, call, request, actual_binding, credential):
            del request, credential
            result = CreateMessageResult(
                role="assistant",
                content=TextContent(type="text", text="untrusted"),
                model="gpt-5.6-luna",
                stopReason="endTurn",
                _meta={
                    "aar.model-receipt.v1": {
                        "schema_version": "aar.model-receipt.v1",
                        "provider_request_id": call.provider_request_id,
                        "provider": "openai-codex",
                        "model": "gpt-5.6-luna",
                        "reasoning_effort": "max",
                        "finish_reason": "stop",
                        "input_tokens": "not-an-integer",
                        "output_tokens": 1,
                        "total_tokens": 1,
                        "retry_count": 0,
                        "fallback_chain": [],
                    }
                },
            )
            return parse_sampling_gateway_result(call, actual_binding, result)

        def lookup(self, call, actual_binding, credential):
            del call, actual_binding, credential
            return None

        def close(self) -> None:
            pass

    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="hermes-mcp-sampling-v1",
            driver_version="1.0.0",
            lookup_supported=False,
            cancellation_supported=False,
        ),
        transport=MalformedReceiptTransport(),
        credential_resolver=lambda _binding: b"ephemeral",
    )

    with pytest.raises(ModelRouteDrift, match="receipt schema is invalid"):
        broker.request(ModelRequest(prompt="reject malformed receipt"), _context(), binding)


def test_model_route_maximum_is_reserved_before_provider_send(tmp_path: Path) -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-budget-reservation-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0

    class Transport:
        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del call, request, binding, credential
            send_calls += 1
            raise AssertionError("provider send must not occur without route-max budget")

        def lookup(self, call, binding, credential):
            del call, binding, credential
            return None

        def close(self) -> None:
            pass

    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={
            profile.profile_id: OwnerGatewayModelBroker(
                manifest=GatewayDriverManifest(
                    driver_id="owner-gateway-v1",
                    driver_version="1.0.0",
                    lookup_supported=True,
                    cancellation_supported=False,
                ),
                transport=Transport(),
                credential_resolver=lambda _binding: b"ephemeral-budget-token",
            )
        },
    )
    host = ReferenceHost(
        tmp_path / "route-budget-reservation.sqlite3",
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    try:
        spec = RlmJobSpec(query="budget reservation", strategy="baseline", max_steps=1)
        envelope = host.request_rlm_envelope(
            request_id="request-route-budget-reservation",
            idempotency_key="idempotency-route-budget-reservation",
            principal=PrincipalRef(value="principal-route-budget-reservation"),
            session=SessionRef(value="session-route-budget-reservation"),
            spec=spec,
            deadline_unix_ms=host.now_ms() + 60_000,
            budget=Budget(
                wall_time_ms=60_000,
                model_requests=1,
                input_tokens=128,
                output_tokens=8,
            ),
        )
        failed = host.execute_rlm(envelope, spec)
        assert failed.state is OperationState.FAILED
        assert send_calls == 0
        assert host.model_usage_records(failed.operation) == ()
    finally:
        host.close()


def test_provider_overrun_is_retained_and_charged_before_operation_fails(
    tmp_path: Path,
) -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-provider-overrun-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=1,
    )
    catalog = ModelRouteCatalog.issue((profile,))

    class Transport:
        def send(self, call, request, binding, credential):
            del request, binding, credential
            return GatewayModelResult(
                provider_request_id=call.provider_request_id,
                output_text="provider exceeded route maximum",
                provider="openai-codex",
                model="gpt-5.6-luna",
                reasoning_effort="max",
                finish_reason="length",
                input_tokens=10,
                output_tokens=100,
                total_tokens=110,
            )

        def lookup(self, call, binding, credential):
            del call, binding, credential
            return None

        def close(self) -> None:
            pass

    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={
            profile.profile_id: OwnerGatewayModelBroker(
                manifest=GatewayDriverManifest(
                    driver_id="owner-gateway-v1",
                    driver_version="1.0.0",
                    lookup_supported=True,
                    cancellation_supported=False,
                ),
                transport=Transport(),
                credential_resolver=lambda _binding: b"ephemeral-overrun-token",
            )
        },
    )
    host = ReferenceHost(
        tmp_path / "provider-overrun.sqlite3",
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    try:
        spec = RlmJobSpec(query="retain overrun", strategy="baseline", max_steps=1)
        envelope = host.request_rlm_envelope(
            request_id="request-provider-overrun",
            idempotency_key="idempotency-provider-overrun",
            principal=PrincipalRef(value="principal-provider-overrun"),
            session=SessionRef(value="session-provider-overrun"),
            spec=spec,
            deadline_unix_ms=host.now_ms() + 60_000,
            budget=Budget(
                wall_time_ms=60_000,
                model_requests=1,
                input_tokens=200,
                output_tokens=200,
            ),
        )
        failed = host.execute_rlm(envelope, spec)
        operation = failed.operation
        assert failed.state is OperationState.FAILED
        usage = host.model_usage_records(operation)
        assert len(usage) == 1
        assert usage[0].output_tokens == 100
        bound = host.brokers.bind(envelope, operation)
        assert bound.usage.output_tokens == 100
        assert bound.usage.input_tokens == 10
        trace = bound.traces()[0]
        assert trace.state == "failed"
        assert trace.failure_code == "BrokerBudgetExceeded"
    finally:
        host.close()


def test_gateway_scrubbed_failure_does_not_retain_secret_exception_chain() -> None:
    credential_canary = b"chain-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="gateway-chain-failure-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    binding = ModelRouteBinding.issue(catalog, profile)

    class Transport:
        def send(
            self,
            call: GatewayProviderCall,
            request: ModelRequest,
            binding: ModelRouteBinding,
            credential: bytes,
        ) -> GatewayModelResult:
            del call, request, binding
            raise RuntimeError("provider secret=" + credential.decode("ascii"))

        def lookup(
            self,
            call: GatewayProviderCall,
            binding: ModelRouteBinding,
            credential: bytes,
        ) -> GatewayModelResult | None:
            del call, binding
            raise RuntimeError("lookup secret=" + credential.decode("ascii"))

        def close(self) -> None:
            pass

    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="owner-gateway-v1",
            driver_version="1.0.0",
            lookup_supported=True,
            cancellation_supported=False,
        ),
        transport=Transport(),
        credential_resolver=lambda _binding: credential_canary,
    )
    operation = OperationRef(value="operation-chain-failure")
    request = ModelRequest(prompt="scrub exception chain")
    context = BrokerContext(
        parent_operation=operation,
        grant_id="grant-chain-failure",
        deadline_unix_ms=4_102_444_800_000,
        idempotency_key="idempotency-chain-failure",
    )

    for call in (
        lambda: broker.request(request, context, binding),
        lambda: broker.reconcile(request, context, binding),
    ):
        with pytest.raises((GatewayProviderFailure, ModelReceiptLookupFailed)) as caught:
            call()
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert credential_canary not in repr(caught.value).encode()


def test_gateway_outcome_unknown_is_reissued_without_secret_exception_graph() -> None:
    credential_canary = "outcome-unknown-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="gateway-outcome-unknown-chain-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)
    request = ModelRequest(prompt="lose the terminal outcome")
    context = _context()
    expected_call = GatewayProviderCall.issue(request, context, binding)

    class Transport:
        def send(self, call, request, binding, credential):
            del request, binding, credential
            try:
                raise RuntimeError(credential_canary)
            except RuntimeError as error:
                raise GatewayCallOutcomeUnknown(call.provider_request_id) from error

        def lookup(self, call, binding, credential):
            del call, binding, credential
            return None

        def close(self) -> None:
            pass

    broker = OwnerGatewayModelBroker(
        manifest=GatewayDriverManifest(
            driver_id="owner-gateway-v1",
            driver_version="1.0.0",
            lookup_supported=True,
            cancellation_supported=False,
        ),
        transport=Transport(),
        credential_resolver=lambda _binding: b"ephemeral-test-token",
    )
    try:
        broker.request(request, context, binding)
    except GatewayCallOutcomeUnknown as error:
        assert error.provider_request_id == expected_call.provider_request_id
        assert error.__cause__ is None
        assert error.__context__ is None
        formatted = "".join(traceback.format_exception(error))
        assert credential_canary not in formatted
    else:
        pytest.fail("gateway outcome-unknown exception was not raised")


def test_gateway_certain_failure_is_scrubbed_and_never_replayed(tmp_path: Path) -> None:
    credential_canary = b"failure-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="gateway-certain-failure-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del call, request, binding
            send_calls += 1
            raise RuntimeError("provider rejected credential=" + credential.decode("ascii"))

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del call, binding, credential
            lookup_calls += 1
            return None

        def close(self) -> None:
            pass

    def registry() -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            catalog,
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=True,
                        cancellation_supported=False,
                    ),
                    transport=Transport(),
                    credential_resolver=lambda _binding: credential_canary,
                )
            },
        )

    database = tmp_path / "gateway-certain-failure.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="fail without leaking", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-gateway-certain-failure",
        idempotency_key="idempotency-gateway-certain-failure",
        principal=PrincipalRef(value="principal-gateway-certain-failure"),
        session=SessionRef(value="session-gateway-certain-failure"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    failed = first.execute_rlm(envelope, spec)
    operation = failed.operation
    assert failed.state is OperationState.FAILED
    first.close()

    with sqlite3.connect(database) as connection:
        execution = connection.execute(
            "SELECT state, failure_code FROM model_executions WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        retained = b"\n".join(
            str(value).encode()
            for table in (
                "operations",
                "operation_events",
                "broker_calls",
                "model_route_bindings",
                "model_executions",
            )
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
            if value is not None
        )
    assert execution == ("provider_failed_certain", "ModelProviderFailure")
    if credential_canary in retained:
        pytest.fail("credential canary persisted in durable failure evidence")

    second = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
    )
    try:
        report = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert report.unresolved
        assert report.calls == ()
        assert second.model_usage_records(operation) == ()
        assert send_calls == 1
        assert lookup_calls == 0
    finally:
        second.close()


@pytest.mark.parametrize("already_taxonomized", (False, True))
def test_core_scrubs_unsanitized_driver_failure_before_durable_outer_evidence(
    tmp_path: Path,
    already_taxonomized: bool,
) -> None:
    credential_canary = b"driver-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="unsanitized-driver-v1",
        provider_driver="third-party-driver-v1",
        provider="example-provider",
        model="example-model",
        reasoning_effort=None,
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))

    class UnsanitizedBroker:
        def request(self, request, context, binding):
            del request, context, binding
            error_type = ModelProviderFailure if already_taxonomized else RuntimeError
            try:
                raise RuntimeError("third-party cause secret=" + credential_canary.decode("ascii"))
            except RuntimeError as error:
                raise error_type(
                    "third-party driver leaked secret=" + credential_canary.decode("ascii")
                ) from error

        def reconcile(self, request, context, binding):
            del request, context, binding
            raise AssertionError("certain failure must not be reconciled")

        def close(self) -> None:
            pass

    registry = StaticModelBrokerRegistry(
        catalog,
        brokers={profile.profile_id: UnsanitizedBroker()},
    )
    database = tmp_path / "unsanitized-driver.sqlite3"
    host = ReferenceHost(
        database,
        model_broker_registry=registry,
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="scrub driver failure", strategy="baseline", max_steps=1)
    envelope = host.request_rlm_envelope(
        request_id="request-unsanitized-driver",
        idempotency_key="idempotency-unsanitized-driver",
        principal=PrincipalRef(value="principal-unsanitized-driver"),
        session=SessionRef(value="session-unsanitized-driver"),
        spec=spec,
        deadline_unix_ms=host.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    failed = host.execute_rlm(envelope, spec)
    operation = failed.operation
    assert failed.state is OperationState.FAILED
    host.close()

    with sqlite3.connect(database) as connection:
        execution = connection.execute(
            "SELECT state, failure_code FROM model_executions WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        retained = b"\n".join(
            str(value).encode()
            for table in (
                "operations",
                "operation_events",
                "broker_calls",
                "model_route_bindings",
                "model_executions",
            )
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
            if value is not None
        )
    expected_failure_code = "ModelProviderFailure" if already_taxonomized else "RuntimeError"
    assert execution == ("provider_failed_certain", expected_failure_code)
    if credential_canary in retained:
        pytest.fail("unsanitized driver credential canary persisted in durable evidence")


@pytest.mark.parametrize(
    ("method", "error_type"),
    (
        ("request", ModelProviderFailure),
        ("request", ModelProviderOutcomeUnknown),
        ("reconcile", ModelReceiptLookupFailed),
        ("reconcile", ModelRouteDrift),
    ),
)
def test_registry_reissues_taxonomized_driver_errors_without_secret_graph(
    method: str,
    error_type: type[Exception],
) -> None:
    credential_canary = "taxonomized-driver-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="taxonomy-boundary-v1",
        provider_driver="third-party-driver-v1",
        provider="example-provider",
        model="example-model",
        reasoning_effort=None,
        max_output_tokens=64,
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)

    class TaxonomizedBroker:
        @staticmethod
        def _raise() -> None:
            try:
                raise RuntimeError(credential_canary)
            except RuntimeError as error:
                raise error_type(credential_canary) from error

        def request(
            self,
            request: ModelRequest,
            context: BrokerContext,
            binding: ModelRouteBinding,
        ) -> ModelResponse:
            del request, context, binding
            self._raise()
            raise AssertionError("unreachable")

        def reconcile(
            self,
            request: ModelRequest,
            context: BrokerContext,
            binding: ModelRouteBinding,
        ) -> ModelResponse | None:
            del request, context, binding
            self._raise()
            raise AssertionError("unreachable")

        def close(self) -> None:
            pass

    registry = StaticModelBrokerRegistry(
        ModelRouteCatalog.issue((profile,)),
        brokers={profile.profile_id: TaxonomizedBroker()},
    )
    call = getattr(registry, method)
    try:
        call(binding, ModelRequest(prompt="taxonomy boundary"), _context())
    except error_type as error:
        assert error.__cause__ is None
        assert error.__context__ is None
        formatted = "".join(traceback.format_exception(error))
        assert credential_canary not in formatted
    else:
        pytest.fail(f"{error_type.__name__} was not raised")


@pytest.mark.parametrize(
    (
        "provider_input_tokens",
        "provider_output_tokens",
        "expected_reason",
    ),
    (
        (13, 5, None),
        (13, 65, "model_route_usage_exceeded"),
        (129, 5, "model_host_budget_exceeded"),
        (13, 129, "model_host_budget_exceeded"),
    ),
)
def test_after_send_loss_recovers_by_lookup_without_blind_replay(
    tmp_path: Path,
    provider_input_tokens: int,
    provider_output_tokens: int,
    expected_reason: str | None,
) -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-reconcile-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    provider_store: dict[str, GatewayModelResult] = {}
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def __init__(self, *, lose_after_send: bool) -> None:
            self._lose_after_send = lose_after_send

        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            result = GatewayModelResult(
                provider_request_id=call.provider_request_id,
                output_text="recovered gateway answer",
                provider="openai-codex",
                model="gpt-5.6-luna",
                reasoning_effort="max",
                finish_reason="stop",
                input_tokens=provider_input_tokens,
                output_tokens=provider_output_tokens,
                total_tokens=provider_input_tokens + provider_output_tokens,
            )
            provider_store[call.provider_request_id] = result
            if self._lose_after_send:
                raise GatewayCallOutcomeUnknown(call.provider_request_id)
            return result

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del binding, credential
            lookup_calls += 1
            return provider_store.get(call.provider_request_id)

        def close(self) -> None:
            pass

    def registry(transport: Transport) -> StaticModelBrokerRegistry:
        broker = OwnerGatewayModelBroker(
            manifest=GatewayDriverManifest(
                driver_id="owner-gateway-v1",
                driver_version="1.0.0",
                lookup_supported=True,
                cancellation_supported=False,
            ),
            transport=transport,
            credential_resolver=lambda _binding: b"ephemeral-test-token",
        )
        return StaticModelBrokerRegistry(
            catalog,
            brokers={profile.profile_id: broker},
        )

    database = tmp_path / "after-send-loss.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=True)),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="recover me", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-after-send-loss",
        idempotency_key="idempotency-after-send-loss",
        principal=PrincipalRef(value="principal-after-send-loss"),
        session=SessionRef(value="session-after-send-loss"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=128,
        ),
    )
    indeterminate = first.execute_rlm(envelope, spec)
    operation = indeterminate.operation
    assert indeterminate.state is OperationState.INDETERMINATE
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=False)),
        default_model_route_profile=profile.profile_id,
    )
    try:
        report = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        quarantined = expected_reason is not None
        assert report.unresolved is quarantined
        assert report.calls[0].action == ("quarantine" if quarantined else "receipt_recovered")
        if expected_reason is not None:
            assert report.calls[0].reason_code == expected_reason
        response = second.model_response(operation, ordinal=0)
        assert response.output_text == "recovered gateway answer"
        assert second.model_usage_records(operation) == (response.usage,)
        outer_usage = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).usage
        assert outer_usage.input_tokens == response.usage.input_tokens
        assert outer_usage.output_tokens == response.usage.output_tokens
        assert send_calls == 1
        assert lookup_calls == 1

        repeated = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert repeated.unresolved is quarantined
        assert repeated.calls == ()
        assert second.model_response(operation, ordinal=0) == response
        assert second.model_usage_records(operation) == (response.usage,)
        assert send_calls == 1
        assert lookup_calls == 1
        with sqlite3.connect(database) as connection:
            execution_count = connection.execute(
                "SELECT COUNT(*) FROM model_executions WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        assert execution_count == (1,)
    finally:
        second.close()


def test_lookup_failure_is_scrubbed_quarantined_and_never_retried(tmp_path: Path) -> None:
    credential_canary = b"lookup-secret-canary-do-not-retain"
    profile = ModelRouteProfile(
        profile_id="gateway-lookup-failure-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def __init__(self, *, lose_after_send: bool) -> None:
            self._lose_after_send = lose_after_send

        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            if self._lose_after_send:
                raise GatewayCallOutcomeUnknown(call.provider_request_id)
            raise AssertionError("reconciliation must not replay send")

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del call, binding
            lookup_calls += 1
            raise RuntimeError("lookup rejected credential=" + credential.decode("ascii"))

        def close(self) -> None:
            pass

    def registry(transport: Transport) -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            catalog,
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=True,
                        cancellation_supported=False,
                    ),
                    transport=transport,
                    credential_resolver=lambda _binding: credential_canary,
                )
            },
        )

    database = tmp_path / "lookup-failure.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=True)),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="quarantine failed lookup", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-lookup-failure",
        idempotency_key="idempotency-lookup-failure",
        principal=PrincipalRef(value="principal-lookup-failure"),
        session=SessionRef(value="session-lookup-failure"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    operation = first.execute_rlm(envelope, spec).operation
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=False)),
        default_model_route_profile=profile.profile_id,
    )
    try:
        bound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = bound.reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == "model_receipt_lookup_failed"
        assert second.model_usage_records(operation) == ()
        assert send_calls == 1
        assert lookup_calls == 1

        repeated = bound.reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert repeated.unresolved
        assert repeated.calls == ()
        assert send_calls == 1
        assert lookup_calls == 1
    finally:
        second.close()

    with sqlite3.connect(database) as connection:
        execution = connection.execute(
            "SELECT state, failure_code FROM model_executions WHERE operation_id = ?",
            (operation.value,),
        ).fetchone()
        retained = b"\n".join(
            str(value).encode()
            for table in (
                "operations",
                "operation_events",
                "broker_calls",
                "model_route_bindings",
                "model_executions",
            )
            for row in connection.execute(f"SELECT * FROM {table}").fetchall()
            for value in row
            if value is not None
        )
    assert execution == ("receipt_lookup_failed_quarantined", "ModelReceiptLookupFailed")
    if credential_canary in retained:
        pytest.fail("lookup credential canary persisted in durable evidence")


def test_crash_after_terminal_receipt_recovers_locally_without_provider_lookup(
    tmp_path: Path,
) -> None:
    class ReceiptCommitProcessLoss(BaseException):
        pass

    class CrashAfterReceiptJournal(ModelExecutionJournal):
        def _after_terminal_receipt(self) -> None:
            raise ReceiptCommitProcessLoss()

    profile = ModelRouteProfile(
        profile_id="gateway-receipt-recovery-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            return GatewayModelResult(
                provider_request_id=call.provider_request_id,
                output_text="locally recovered answer",
                provider="openai-codex",
                model="gpt-5.6-luna",
                reasoning_effort="max",
                finish_reason="stop",
                input_tokens=17,
                output_tokens=6,
                total_tokens=23,
            )

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del call, binding, credential
            lookup_calls += 1
            return None

        def close(self) -> None:
            pass

    def registry() -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            catalog,
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=True,
                        cancellation_supported=False,
                    ),
                    transport=Transport(),
                    credential_resolver=lambda _binding: b"ephemeral-test-token",
                )
            },
        )

    database = tmp_path / "after-terminal-receipt.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
        model_execution_journal=CrashAfterReceiptJournal(database),
    )
    spec = RlmJobSpec(query="commit after receipt", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-after-terminal-receipt",
        idempotency_key="idempotency-after-terminal-receipt",
        principal=PrincipalRef(value="principal-after-terminal-receipt"),
        session=SessionRef(value="session-after-terminal-receipt"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    accepted = first.submit_rlm(envelope, spec)
    with pytest.raises(ReceiptCommitProcessLoss):
        first.run_rlm(accepted.operation)
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
    )
    try:
        rebound = envelope.model_copy(update={"runtime_generation": second.runtime_generation})
        report = second.brokers.bind(rebound, accepted.operation).reconcile_unresolved(
            current_capability_digest=second.capabilities.digest
        )
        assert not report.unresolved
        assert report.calls[0].action == "receipt_recovered"
        response = second.model_response(accepted.operation, ordinal=0)
        assert response.output_text == "locally recovered answer"
        assert second.model_usage_records(accepted.operation) == (response.usage,)
        assert send_calls == 1
        assert lookup_calls == 0
    finally:
        second.close()


def test_after_send_ambiguity_quarantines_when_lookup_is_unsupported(
    tmp_path: Path,
) -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-no-lookup-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            raise GatewayCallOutcomeUnknown(call.provider_request_id)

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del call, binding, credential
            lookup_calls += 1
            return None

        def close(self) -> None:
            pass

    def registry() -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            catalog,
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=False,
                        cancellation_supported=False,
                    ),
                    transport=Transport(),
                    credential_resolver=lambda _binding: b"ephemeral-test-token",
                )
            },
        )

    database = tmp_path / "lookup-unsupported.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="do not replay me", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-lookup-unsupported",
        idempotency_key="idempotency-lookup-unsupported",
        principal=PrincipalRef(value="principal-lookup-unsupported"),
        session=SessionRef(value="session-lookup-unsupported"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    indeterminate = first.execute_rlm(envelope, spec)
    operation = indeterminate.operation
    assert indeterminate.state is OperationState.INDETERMINATE
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(),
        default_model_route_profile=profile.profile_id,
    )
    try:
        report = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == "model_receipt_lookup_unavailable"
        assert second.model_usage_records(operation) == ()
        assert send_calls == 1
        assert lookup_calls == 0
        with sqlite3.connect(database) as connection:
            state = connection.execute(
                "SELECT state FROM model_executions WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        assert state == ("receipt_lookup_unavailable_quarantined",)
    finally:
        second.close()


def test_lookup_route_drift_quarantines_without_usage_commit(tmp_path: Path) -> None:
    profile = ModelRouteProfile(
        profile_id="gateway-route-drift-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    catalog = ModelRouteCatalog.issue((profile,))
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def __init__(self, *, lose_after_send: bool) -> None:
            self._lose_after_send = lose_after_send

        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            if self._lose_after_send:
                raise GatewayCallOutcomeUnknown(call.provider_request_id)
            raise AssertionError("reconciliation must not replay send")

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del binding, credential
            lookup_calls += 1
            return GatewayModelResult(
                provider_request_id=call.provider_request_id,
                output_text="wrong route answer",
                provider="openai-codex",
                model="gpt-5.6-sol",
                reasoning_effort="max",
                finish_reason="stop",
                input_tokens=9,
                output_tokens=4,
                total_tokens=13,
            )

        def close(self) -> None:
            pass

    def registry(transport: Transport) -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            catalog,
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=True,
                        cancellation_supported=False,
                    ),
                    transport=transport,
                    credential_resolver=lambda _binding: b"ephemeral-test-token",
                )
            },
        )

    database = tmp_path / "route-drift.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=True)),
        default_model_route_profile=profile.profile_id,
    )
    spec = RlmJobSpec(query="reject drift", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-route-drift",
        idempotency_key="idempotency-route-drift",
        principal=PrincipalRef(value="principal-route-drift"),
        session=SessionRef(value="session-route-drift"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    operation = first.execute_rlm(envelope, spec).operation
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(Transport(lose_after_send=False)),
        default_model_route_profile=profile.profile_id,
    )
    try:
        report = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == "model_route_drift"
        assert second.model_usage_records(operation) == ()
        assert send_calls == 1
        assert lookup_calls == 1
        with sqlite3.connect(database) as connection:
            state = connection.execute(
                "SELECT state FROM model_executions WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        assert state == ("route_drift_quarantined",)
    finally:
        second.close()


def test_restart_catalog_drift_quarantines_before_provider_lookup(tmp_path: Path) -> None:
    original = ModelRouteProfile(
        profile_id="gateway-catalog-drift-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=64,
    )
    changed = original.model_copy(update={"model": "gpt-5.6-sol"})
    send_calls = 0
    lookup_calls = 0

    class Transport:
        def send(self, call, request, binding, credential):
            nonlocal send_calls
            del request, binding, credential
            send_calls += 1
            raise GatewayCallOutcomeUnknown(call.provider_request_id)

        def lookup(self, call, binding, credential):
            nonlocal lookup_calls
            del call, binding, credential
            lookup_calls += 1
            return None

        def close(self) -> None:
            pass

    def registry(profile: ModelRouteProfile) -> StaticModelBrokerRegistry:
        return StaticModelBrokerRegistry(
            ModelRouteCatalog.issue((profile,)),
            brokers={
                profile.profile_id: OwnerGatewayModelBroker(
                    manifest=GatewayDriverManifest(
                        driver_id="owner-gateway-v1",
                        driver_version="1.0.0",
                        lookup_supported=True,
                        cancellation_supported=False,
                    ),
                    transport=Transport(),
                    credential_resolver=lambda _binding: b"ephemeral-test-token",
                )
            },
        )

    database = tmp_path / "restart-catalog-drift.sqlite3"
    first = ReferenceHost(
        database,
        model_broker_registry=registry(original),
        default_model_route_profile=original.profile_id,
    )
    spec = RlmJobSpec(query="bind the original catalog", strategy="baseline", max_steps=1)
    envelope = first.request_rlm_envelope(
        request_id="request-restart-catalog-drift",
        idempotency_key="idempotency-restart-catalog-drift",
        principal=PrincipalRef(value="principal-restart-catalog-drift"),
        session=SessionRef(value="session-restart-catalog-drift"),
        spec=spec,
        deadline_unix_ms=first.now_ms() + 60_000,
        budget=Budget(
            wall_time_ms=60_000,
            model_requests=1,
            input_tokens=128,
            output_tokens=64,
        ),
    )
    operation = first.execute_rlm(envelope, spec).operation
    first.close()

    second = ReferenceHost(
        database,
        model_broker_registry=registry(changed),
        default_model_route_profile=changed.profile_id,
    )
    try:
        report = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        ).reconcile_unresolved(current_capability_digest=second.capabilities.digest)
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == "model_route_drift"
        assert second.model_usage_records(operation) == ()
        assert send_calls == 1
        assert lookup_calls == 0
        with sqlite3.connect(database) as connection:
            state = connection.execute(
                "SELECT state FROM model_executions WHERE operation_id = ?",
                (operation.value,),
            ).fetchone()
        assert state == ("route_drift_quarantined",)
    finally:
        second.close()
