from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aar.runtime.brokers import (
    ArtifactPutRequest,
    ArtifactReadRequest,
    BrokerBudgetExceeded,
    BrokerGrantDenied,
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
