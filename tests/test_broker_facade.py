from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path

import pytest

from aar.broker_models import BrokerReceipt, BrokerUsage
from aar.runtime.brokers import (
    ArtifactPutRequest,
    ArtifactReadRequest,
    BrokerBudgetExceeded,
    BrokerCallConflict,
    BrokerCallIndeterminate,
    BrokerGrantDenied,
    BrokerJournal,
    EffectProposal,
    EvidenceQuery,
    ModelRequest,
    SubagentResultRequest,
    SubagentSubmit,
)
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import (
    Budget,
    Grant,
    HostRef,
    LaneRef,
    OperationRef,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
)


class SimulatedBrokerProcessLoss(BaseException):
    pass


def broker_envelope(
    host: ReferenceHost,
    capabilities: tuple[str, ...],
    *,
    budget: Budget | None = None,
) -> RequestEnvelope:
    principal = PrincipalRef(value="principal-broker")
    deadline = 4_102_444_800_000
    grants = tuple(
        sorted(
            (
                Grant(
                    grant_id=f"grant-{capability.replace('.', '-')}",
                    capability=capability,
                    issued_to=principal,
                    expires_at_unix_ms=deadline,
                )
                for capability in capabilities
            ),
            key=lambda grant: grant.grant_id,
        )
    )
    return RequestEnvelope(
        request_id="request-broker-facade",
        idempotency_key="idem-broker-facade",
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="broker"),
        session=SessionRef(value="session-broker"),
        runtime_generation=host.runtime_generation,
        capability_digest=host.capabilities.digest,
        deadline_unix_ms=deadline,
        grants=grants,
        budget=budget
        or Budget(
            wall_time_ms=60_000,
            model_requests=8,
            input_tokens=1_024,
            output_tokens=1_024,
            child_operations=8,
            artifact_bytes=1_024,
        ),
        trace_id="trace-broker-facade",
        input_digest=f"sha256:{'0' * 64}",
    )


def current_effect_grant(envelope: RequestEnvelope) -> Grant:
    return Grant(
        grant_id="current-grant-effect-propose",
        capability="effect.propose",
        issued_to=envelope.principal,
        expires_at_unix_ms=envelope.deadline_unix_ms,
    )


def test_catalog_is_grant_filtered_and_contracts_are_progressively_disclosed(
    tmp_path: Path,
) -> None:
    host = ReferenceHost(tmp_path / "catalog.sqlite3")
    try:
        operation = OperationRef(value="operation-broker-catalog")
        bound = host.brokers.bind(
            broker_envelope(host, ("evidence.query", "model.request")), operation
        )

        catalog = bound.catalog()
        assert [method.name for method in catalog.methods] == [
            "evidence.query",
            "model.request",
        ]
        assert "request_schema_json" not in catalog.model_dump(mode="json")

        described = bound.describe(("model.request",))
        contract = described.contracts[0]
        assert contract.request_model == "ModelRequest"
        assert '"prompt"' in contract.request_schema_json
        assert contract.contract_digest.startswith("sha256:")
        with pytest.raises(BrokerGrantDenied):
            bound.describe(("effect.propose",))
    finally:
        host.close()


def test_idempotent_replay_does_not_consume_budget_twice(tmp_path: Path) -> None:
    host = ReferenceHost(tmp_path / "budget.sqlite3")
    try:
        operation = OperationRef(value="operation-broker-budget")
        envelope = broker_envelope(
            host,
            ("model.request",),
            budget=Budget(
                wall_time_ms=60_000,
                model_requests=1,
                input_tokens=2,
                output_tokens=1,
            ),
        )
        bound = host.brokers.bind(envelope, operation)
        request = ModelRequest(prompt="two tokens")

        first = bound.model_request(request, step_key="step-1")
        assert bound.model_request(request, step_key="step-1") == first
        assert bound.usage.model_requests == 1
        assert len(bound.traces()) == 1
        with pytest.raises(BrokerBudgetExceeded):
            bound.model_request(request, step_key="step-2")
    finally:
        host.close()


def test_pre_provider_admission_rejection_is_safe_to_revalidate_and_replay(
    tmp_path: Path,
) -> None:
    journal = BrokerJournal(tmp_path / "pre-provider-rejection.sqlite3")
    operation = OperationRef(value="operation-pre-provider-rejection")
    request = EffectProposal(
        effect_type="write.file",
        payload_digest=f"sha256:{'a' * 64}",
    )
    response = BrokerReceipt(
        kind="effect",
        handle="proposal-safe-replay",
        digest=f"sha256:{'b' * 64}",
        value="proposal_only",
    )
    budget = Budget(
        wall_time_ms=60_000,
        model_requests=1,
        input_tokens=1,
        output_tokens=1,
        child_operations=1,
        artifact_bytes=1,
    )
    admission_checks = 0
    provider_calls = 0
    reject_second_check = True

    def admission_check() -> None:
        nonlocal admission_checks
        admission_checks += 1
        if reject_second_check and admission_checks == 2:
            raise BrokerGrantDenied("authority changed before provider invocation")

    def call() -> BrokerReceipt:
        nonlocal provider_calls
        provider_calls += 1
        return response

    def invoke() -> BrokerReceipt:
        return journal.invoke(
            operation=operation,
            method="effect.propose",
            grant_id="grant-effect-propose",
            idempotency_key="safe-replay",
            request=request,
            response_type=BrokerReceipt,
            usage=BrokerUsage(),
            budget=budget,
            call=call,
            admission_check=admission_check,
        )

    try:
        with pytest.raises(BrokerGrantDenied):
            invoke()
        assert admission_checks == 2
        assert provider_calls == 0
        trace = journal.traces(operation)[0]
        assert trace.state == "rejected_before_send"
        assert trace.failure_code == "BrokerGrantDenied"

        reject_second_check = False
        assert invoke() == response
        assert admission_checks == 4
        assert provider_calls == 1
        trace = journal.traces(operation)[0]
        assert trace.state == "succeeded"
        assert trace.failure_code is None
    finally:
        journal.close()


def test_provider_failure_remains_non_replayable_with_same_identity(tmp_path: Path) -> None:
    host = ReferenceHost(tmp_path / "provider-failure-no-replay.sqlite3")
    operation = OperationRef(value="operation-provider-failure-no-replay")
    bound = host.brokers.bind(
        broker_envelope(host, ("model.request",)),
        operation,
    )
    provider_calls = 0

    def fail_provider(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise RuntimeError("provider failed after invocation")

    host.models.request = fail_provider  # type: ignore[method-assign]
    request = ModelRequest(prompt="never replay unknown provider work")
    try:
        with pytest.raises(RuntimeError):
            bound.model_request(request, step_key="unsafe-replay")
        with pytest.raises(BrokerCallIndeterminate):
            bound.model_request(request, step_key="unsafe-replay")
        assert provider_calls == 1
    finally:
        host.close()


def test_child_and_artifact_receipts_survive_host_restart(tmp_path: Path) -> None:
    database = tmp_path / "retained.sqlite3"
    operation = OperationRef(value="operation-broker-retained")
    capabilities = (
        "artifact.write",
        "artifact.read",
        "effect.propose",
        "evidence.query",
        "subagent.result",
        "subagent.submit",
    )
    first = ReferenceHost(database, evidence_records=("portable evidence",))
    first_envelope = broker_envelope(first, capabilities)
    bound = first.brokers.bind(first_envelope, operation)
    child = bound.subagent_submit(
        SubagentSubmit(task="bounded task"), step_key="child-submit"
    )
    content = b"artifact-evidence"
    reference = bound.artifact_put(
        ArtifactPutRequest(
            content_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            media_type="text/plain",
            size_bytes=len(content),
        ),
        content,
        step_key="artifact-put",
    )
    evidence = bound.evidence_query(
        EvidenceQuery(text="portable"), step_key="evidence-query"
    )
    proposal = bound.effect_propose(
        EffectProposal(
            effect_type="write.file",
            payload_digest=f"sha256:{'1' * 64}",
        ),
        step_key="effect-proposal",
    )
    assert evidence.value == "portable evidence"
    assert proposal.value == "proposal_only"
    first.close()

    second = ReferenceHost(database, evidence_records=("portable evidence",))
    try:
        rebound = second.brokers.bind(
            first_envelope.model_copy(
                update={"runtime_generation": second.runtime_generation}
            ),
            operation,
        )
        result = rebound.subagent_result(
            SubagentResultRequest(handle=child.handle), step_key="child-result"
        )
        artifact = rebound.artifact_read(
            ArtifactReadRequest(reference=reference), step_key="artifact-read"
        )

        assert result.value == "completed:bounded task"
        assert artifact.content() == content
        assert [trace.method for trace in rebound.traces()] == [
            "subagent.submit",
            "artifact.put",
            "evidence.query",
            "effect.propose",
            "subagent.result",
            "artifact.read",
        ]
        assert not hasattr(rebound, "effect_execute")
    finally:
        second.close()


def test_reconciliation_recovers_artifact_and_subagent_receipts_by_lookup(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-lookups.sqlite3"
    operation = OperationRef(value="operation-broker-reconcile-lookups")
    capabilities = ("artifact.write", "subagent.submit")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, capabilities)
    bound = first.brokers.bind(envelope, operation)
    content = b"recoverable-artifact"
    original_put = first.artifacts.put
    original_submit = first.subagents.submit

    def put_then_lose(*args, **kwargs):
        original_put(*args, **kwargs)
        raise SimulatedBrokerProcessLoss()

    def submit_then_lose(*args, **kwargs):
        original_submit(*args, **kwargs)
        raise SimulatedBrokerProcessLoss()

    first.artifacts.put = put_then_lose  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.artifact_put(
            ArtifactPutRequest(
                content_digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
                media_type="text/plain",
                size_bytes=len(content),
            ),
            content,
            step_key="artifact-lookup-gap",
        )
    first.subagents.submit = submit_then_lose  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.subagent_submit(
            SubagentSubmit(task="recover retained handle"),
            step_key="subagent-lookup-gap",
        )
    first.close()

    second = ReferenceHost(database)
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert not report.unresolved
        assert [call.action for call in report.calls] == [
            "receipt_recovered",
            "receipt_recovered",
        ]
        traces = rebound.traces()
        assert [trace.state for trace in traces] == ["succeeded", "succeeded"]
        assert [trace.reconciliation_action for trace in traces] == [
            "receipt_recovered",
            "receipt_recovered",
        ]
    finally:
        second.close()


def test_reconciliation_replays_only_safe_read_calls(tmp_path: Path) -> None:
    database = tmp_path / "reconcile-reads.sqlite3"
    operation = OperationRef(value="operation-broker-reconcile-reads")
    first = ReferenceHost(database, evidence_records=("durable evidence",))
    envelope = broker_envelope(first, ("artifact.read", "evidence.query"))
    content = b"read-after-loss"
    reference = first.artifacts.put(content, "text/plain", operation)
    bound = first.brokers.bind(envelope, operation)

    def lose_read(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    def lose_query(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.artifacts.read = lose_read  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.artifact_read(
            ArtifactReadRequest(reference=reference),
            step_key="artifact-read-gap",
        )
    first.evidence.query = lose_query  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.evidence_query(
            EvidenceQuery(text="durable"),
            step_key="evidence-read-gap",
        )
    first.close()

    second = ReferenceHost(database, evidence_records=("durable evidence",))
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert not report.unresolved
        assert [call.action for call in report.calls] == ["safe_replay", "safe_replay"]
        assert all(trace.state == "succeeded" for trace in rebound.traces())
    finally:
        second.close()


def test_reconciliation_blocks_provider_on_current_capability_drift(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-capability-drift.sqlite3"
    operation = OperationRef(value="operation-broker-capability-drift")
    first = ReferenceHost(database, evidence_records=("durable evidence",))
    envelope = broker_envelope(first, ("evidence.query",))
    bound = first.brokers.bind(envelope, operation)

    def lose_query(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.evidence.query = lose_query  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.evidence_query(
            EvidenceQuery(text="durable"),
            step_key="capability-drift-gap",
        )
    first.close()

    second = ReferenceHost(database, evidence_records=("durable evidence",))
    provider_calls = 0
    original_query = second.evidence.query

    def count_query(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return original_query(*args, **kwargs)

    second.evidence.query = count_query  # type: ignore[method-assign]
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        with pytest.raises(BrokerGrantDenied, match="current host authority"):
            rebound.reconcile_unresolved(
                current_capability_digest=f"sha256:{'f' * 64}"
            )
        assert provider_calls == 0
        assert rebound.traces()[0].state == "started"
    finally:
        second.close()


def test_indeterminate_effect_proposal_stays_pending_without_provider_replay(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-effect-pending.sqlite3"
    operation = OperationRef(value="operation-broker-effect-pending")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose",))
    bound = first.brokers.bind(envelope, operation)

    def lose_effect(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.effects.propose = lose_effect  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.effect_propose(
            EffectProposal(
                effect_type="effect.notify",
                payload_digest=f"sha256:{'9' * 64}",
            ),
            step_key="effect-gap",
        )
    first.close()

    second = ReferenceHost(database)
    replay_count = 0
    original_propose = second.effects.propose

    def count_proposal(*args, **kwargs):
        nonlocal replay_count
        replay_count += 1
        return original_propose(*args, **kwargs)

    second.effects.propose = count_proposal  # type: ignore[method-assign]
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert report.unresolved
        assert report.calls[0].action == "pending"
        assert report.calls[0].reason_code == "effect_proposal_receipt_unavailable"
        assert replay_count == 0
        assert rebound.traces()[0].state == "started"
    finally:
        second.close()


def test_model_receipt_stays_pending_until_explicit_compensation_proposal(
    tmp_path: Path,
) -> None:
    host = ReferenceHost(tmp_path / "reconcile-compensation.sqlite3")
    operation = OperationRef(value="operation-broker-reconcile-compensation")
    envelope = broker_envelope(host, ("effect.propose", "model.request"))
    bound = host.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    host.models.request = lose_model  # type: ignore[method-assign]
    try:
        with pytest.raises(SimulatedBrokerProcessLoss):
            bound.model_request(ModelRequest(prompt="unknown outcome"), step_key="model-gap")
        pending = bound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert pending.unresolved
        assert pending.calls[0].action == "pending"
        assert bound.traces()[0].state == "started"

        compensated = bound.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=False,
            propose_compensation=True,
        )
        assert compensated.unresolved
        assert compensated.calls[0].action == "compensation_proposed"
        assert compensated.calls[0].compensation_receipt is not None
        traces = bound.traces()
        assert len(traces) == 2
        trace = traces[0]
        assert trace.state == "failed"
        assert trace.reconciliation_action == "compensation_proposed"
        assert trace.compensation_digest is not None
        assert traces[1].method == "effect.propose"
        assert traces[1].state == "succeeded"
        assert not hasattr(bound, "effect_execute")
    finally:
        host.close()


def test_compensation_intent_is_durable_before_provider_invocation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-compensation-intent.sqlite3"
    operation = OperationRef(value="operation-broker-compensation-intent")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose", "model.request"))
    bound = first.brokers.bind(envelope, operation)

    def lose_call(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.models.request = lose_call  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.model_request(ModelRequest(prompt="unknown model"), step_key="model-gap")
    first.effects.propose = lose_call  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=False,
            propose_compensation=True,
        )
    first.close()

    second = ReferenceHost(database)
    replay_count = 0
    original_propose = second.effects.propose

    def count_proposal(*args, **kwargs):
        nonlocal replay_count
        replay_count += 1
        return original_propose(*args, **kwargs)

    second.effects.propose = count_proposal  # type: ignore[method-assign]
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        traces = rebound.traces()
        assert [trace.method for trace in traces] == ["model.request", "effect.propose"]
        assert [trace.state for trace in traces] == ["started", "started"]
        report = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert report.unresolved
        assert [call.action for call in report.calls] == ["pending", "pending"]
        assert replay_count == 0
    finally:
        second.close()


@pytest.mark.parametrize(
    ("mode", "reason_code"),
    (
        ("cancelled", "broker_reconcile_cancelled"),
        ("deadline", "broker_reconcile_deadline_expired"),
        ("grant-drift", "broker_reconcile_grant_drift"),
    ),
)
def test_safe_replay_is_blocked_by_control_or_authority_drift(
    tmp_path: Path,
    mode: str,
    reason_code: str,
) -> None:
    database = tmp_path / f"reconcile-{mode}.sqlite3"
    operation = OperationRef(value=f"operation-broker-reconcile-{mode}")
    first = ReferenceHost(database, evidence_records=("bounded evidence",))
    envelope = broker_envelope(first, ("evidence.query",))
    bound = first.brokers.bind(envelope, operation)

    def lose_query(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.evidence.query = lose_query  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.evidence_query(EvidenceQuery(text="bounded"), step_key="authority-gap")
    first.close()

    now_ms = (
        (lambda: envelope.deadline_unix_ms)
        if mode == "deadline"
        else (lambda: envelope.deadline_unix_ms - 1)
    )
    second = ReferenceHost(
        database,
        now_ms=now_ms,
        evidence_records=("bounded evidence",),
    )
    replay_count = 0
    original_query = second.evidence.query

    def count_query(*args, **kwargs):
        nonlocal replay_count
        replay_count += 1
        return original_query(*args, **kwargs)

    second.evidence.query = count_query  # type: ignore[method-assign]
    try:
        rebound_envelope = envelope.model_copy(
            update={"runtime_generation": second.runtime_generation}
        )
        if mode == "grant-drift":
            rebound_envelope = rebound_envelope.model_copy(
                update={
                    "grants": (
                        rebound_envelope.grants[0].model_copy(
                            update={"grant_id": "grant-evidence-query-drift"}
                        ),
                    )
                }
            )
        report = second.brokers.bind(
            rebound_envelope,
            operation,
        ).reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            cancellation_requested=mode == "cancelled",
        )
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == reason_code
        assert replay_count == 0
        trace = second.brokers.bind(rebound_envelope, operation).traces()[0]
        assert trace.state == "failed"
        assert trace.reconciliation_action == "quarantine"
    finally:
        second.close()


def test_tampered_request_bytes_are_never_replayed(tmp_path: Path) -> None:
    database = tmp_path / "reconcile-tamper.sqlite3"
    operation = OperationRef(value="operation-broker-reconcile-tamper")
    first = ReferenceHost(database, evidence_records=("bounded evidence",))
    envelope = broker_envelope(first, ("evidence.query",))
    bound = first.brokers.bind(envelope, operation)

    def lose_query(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.evidence.query = lose_query  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.evidence_query(EvidenceQuery(text="bounded"), step_key="tamper-gap")
    first.close()

    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            """
            UPDATE broker_calls SET request_json = ?
            WHERE operation_id = ? AND sequence = 1
            """,
            ('{"schema_version":"aar.broker.v1","text":"tampered"}', operation.value),
        )
    connection.close()

    second = ReferenceHost(database, evidence_records=("bounded evidence",))
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        with pytest.raises(BrokerCallConflict):
            rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert rebound.traces()[0].state == "started"
    finally:
        second.close()


def test_missing_legacy_request_bytes_quarantine_without_replay(tmp_path: Path) -> None:
    database = tmp_path / "reconcile-missing-request.sqlite3"
    operation = OperationRef(value="operation-broker-reconcile-missing-request")
    first = ReferenceHost(database, evidence_records=("bounded evidence",))
    envelope = broker_envelope(first, ("evidence.query",))
    bound = first.brokers.bind(envelope, operation)

    def lose_query(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.evidence.query = lose_query  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.evidence_query(EvidenceQuery(text="bounded"), step_key="legacy-gap")
    first.close()

    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            """
            UPDATE broker_calls SET request_json = NULL
            WHERE operation_id = ? AND sequence = 1
            """,
            (operation.value,),
        )
    connection.close()

    second = ReferenceHost(database, evidence_records=("bounded evidence",))
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].reason_code == "broker_request_bytes_unavailable"
        assert rebound.traces()[0].state == "failed"
    finally:
        second.close()


def test_pending_receipt_and_compensation_evidence_survive_reconnect(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-pending-reconnect.sqlite3"
    operation = OperationRef(value="operation-broker-reconcile-pending-reconnect")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose", "model.request"))
    bound = first.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.models.request = lose_model  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.model_request(ModelRequest(prompt="pending reconnect"), step_key="pending-gap")
    pending = bound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
    assert pending.calls[0].action == "pending"
    first.close()

    second = ReferenceHost(database)
    rebound_envelope = envelope.model_copy(
        update={"runtime_generation": second.runtime_generation}
    )
    rebound = second.brokers.bind(rebound_envelope, operation)
    pending_again = rebound.reconcile_unresolved(
                current_capability_digest=envelope.capability_digest,
                cancellation_requested=False,
            )
    assert pending_again.calls[0].action == "pending"
    compensated = rebound.reconcile_unresolved(
        current_capability_digest=envelope.capability_digest,
        current_compensation_grant=current_effect_grant(envelope),
        cancellation_requested=False,
        propose_compensation=True,
    )
    assert compensated.calls[0].action == "compensation_proposed"
    compensation_digest = rebound.traces()[0].compensation_digest
    assert compensation_digest is not None
    second.close()

    third = ReferenceHost(database)
    try:
        final = third.brokers.bind(
            envelope.model_copy(update={"runtime_generation": third.runtime_generation}),
            operation,
        )
        report = final.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=False,
            propose_compensation=True,
        )
        assert report.unresolved
        assert report.calls == ()
        trace = final.traces()[0]
        assert trace.reconciliation_action == "compensation_proposed"
        assert trace.compensation_digest == compensation_digest
    finally:
        third.close()


def test_compensation_requires_original_effect_proposal_authority(tmp_path: Path) -> None:
    host = ReferenceHost(tmp_path / "reconcile-compensation-authority.sqlite3")
    operation = OperationRef(value="operation-broker-compensation-authority")
    envelope = broker_envelope(host, ("model.request",))
    bound = host.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    host.models.request = lose_model  # type: ignore[method-assign]
    try:
        with pytest.raises(SimulatedBrokerProcessLoss):
            bound.model_request(ModelRequest(prompt="no compensation grant"), step_key="gap")
        report = bound.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=False,
            propose_compensation=True,
        )
        assert report.unresolved
        assert report.calls[0].action == "pending"
        assert report.calls[0].compensation_receipt is None
        assert bound.traces()[0].state == "started"
    finally:
        host.close()


@pytest.mark.parametrize("mode", ("cancelled", "deadline"))
def test_compensation_is_forbidden_after_cancellation_or_deadline(
    tmp_path: Path,
    mode: str,
) -> None:
    database = tmp_path / f"reconcile-compensation-{mode}.sqlite3"
    operation = OperationRef(value=f"operation-broker-compensation-{mode}")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose", "model.request"))
    bound = first.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.models.request = lose_model  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.model_request(ModelRequest(prompt=mode), step_key="authority-gap")
    first.close()

    now_ms = (
        (lambda: envelope.deadline_unix_ms)
        if mode == "deadline"
        else (lambda: envelope.deadline_unix_ms - 1)
    )
    second = ReferenceHost(database, now_ms=now_ms)
    proposal_count = 0
    original_propose = second.effects.propose

    def count_proposal(*args, **kwargs):
        nonlocal proposal_count
        proposal_count += 1
        return original_propose(*args, **kwargs)

    second.effects.propose = count_proposal  # type: ignore[method-assign]
    try:
        rebound = second.brokers.bind(
            envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
            operation,
        )
        report = rebound.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=mode == "cancelled",
            propose_compensation=True,
        )
        assert report.unresolved
        assert report.calls[0].action == "quarantine"
        assert report.calls[0].compensation_receipt is None
        assert proposal_count == 0
        assert len(rebound.traces()) == 1
    finally:
        second.close()


def test_compensation_deadline_is_rechecked_after_waiting_for_admission_lock(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-compensation-lock-expiry.sqlite3"
    operation = OperationRef(value="operation-broker-compensation-lock-expiry")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose", "model.request"))
    bound = first.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.models.request = lose_model  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.model_request(ModelRequest(prompt="expire behind lock"), step_key="authority-gap")
    first.close()

    now = [envelope.deadline_unix_ms - 1]
    second = ReferenceHost(database, now_ms=lambda: now[0])
    rebound = second.brokers.bind(
        envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
        operation,
    )
    provider_calls = 0
    original_propose = second.effects.propose

    def count_proposal(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return original_propose(*args, **kwargs)

    second.effects.propose = count_proposal  # type: ignore[method-assign]
    invoke_entered = threading.Event()
    original_invoke = second.brokers._journal.invoke

    def signal_then_invoke(**kwargs):
        invoke_entered.set()
        return original_invoke(**kwargs)

    second.brokers._journal.invoke = signal_then_invoke  # type: ignore[method-assign]
    blocker = sqlite3.connect(database, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    result: list[object] = []
    failures: list[BaseException] = []

    def reconcile() -> None:
        try:
            result.append(
                rebound.reconcile_unresolved(
                    current_capability_digest=envelope.capability_digest,
                    current_compensation_grant=current_effect_grant(envelope),
                    cancellation_requested=False,
                    propose_compensation=True,
                )
            )
        except BaseException as error:
            failures.append(error)

    worker = threading.Thread(target=reconcile)
    worker.start()
    assert invoke_entered.wait(timeout=2)
    now[0] = envelope.deadline_unix_ms
    blocker.execute("COMMIT")
    blocker.close()
    worker.join(timeout=5)

    try:
        assert not worker.is_alive()
        assert failures == []
        assert len(result) == 1
        assert provider_calls == 0
        traces = rebound.traces()
        assert [trace.method for trace in traces] == ["model.request"]
        assert traces[0].compensation_digest is None
    finally:
        second.close()


def test_compensation_deadline_is_rechecked_immediately_before_provider_call(
    tmp_path: Path,
) -> None:
    database = tmp_path / "reconcile-compensation-provider-expiry.sqlite3"
    operation = OperationRef(value="operation-broker-compensation-provider-expiry")
    first = ReferenceHost(database)
    envelope = broker_envelope(first, ("effect.propose", "model.request"))
    bound = first.brokers.bind(envelope, operation)

    def lose_model(*_args, **_kwargs):
        raise SimulatedBrokerProcessLoss()

    first.models.request = lose_model  # type: ignore[method-assign]
    with pytest.raises(SimulatedBrokerProcessLoss):
        bound.model_request(ModelRequest(prompt="expire before provider"), step_key="authority-gap")
    first.close()

    now = [envelope.deadline_unix_ms - 1]
    second = ReferenceHost(database, now_ms=lambda: now[0])
    rebound = second.brokers.bind(
        envelope.model_copy(update={"runtime_generation": second.runtime_generation}),
        operation,
    )
    provider_calls = 0
    original_propose = second.effects.propose

    def count_proposal(*args, **kwargs):
        nonlocal provider_calls
        provider_calls += 1
        return original_propose(*args, **kwargs)

    second.effects.propose = count_proposal  # type: ignore[method-assign]
    original_invoke = second.brokers._journal.invoke

    def expire_after_transaction_check(**kwargs):
        original_check = kwargs["admission_check"]
        checks = 0

        def wrapped_check() -> None:
            nonlocal checks
            original_check()
            checks += 1
            if checks == 1:
                now[0] = envelope.deadline_unix_ms

        kwargs["admission_check"] = wrapped_check
        return original_invoke(**kwargs)

    second.brokers._journal.invoke = expire_after_transaction_check  # type: ignore[method-assign]
    try:
        report = rebound.reconcile_unresolved(
            current_capability_digest=envelope.capability_digest,
            current_compensation_grant=current_effect_grant(envelope),
            cancellation_requested=False,
            propose_compensation=True,
        )
        assert report.unresolved
        assert provider_calls == 0
        traces = rebound.traces()
        assert [trace.method for trace in traces] == ["model.request", "effect.propose"]
        assert traces[1].state == "rejected_before_send"
        assert traces[1].failure_code == "BrokerDeadlineExpired"
    finally:
        second.close()
