from __future__ import annotations

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
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
from aar.schemas import FailureCategory, FailureEnvelope, OutcomeCertainty


def _spec(
    *,
    strategy: str = "single_call",
    max_model_calls: int | None = None,
) -> PublicRlmJobSpec:
    if max_model_calls is None:
        max_model_calls = 1 if strategy == "single_call" else 2
    return PublicRlmJobSpec(
        query="Explain the bounded caller-delegated contract.",
        strategy=strategy,
        executor_kind="codex-host",
        requested_model="model-v1",
        requested_reasoning_effort="high",
        max_model_calls=max_model_calls,
        max_output_tokens_per_call=256,
        max_result_bytes_per_call=1024,
    )


def _call(*, step_index: int = 0) -> PublicRlmCallSpec:
    return PublicRlmCallSpec.issue(
        job_id="job-1",
        step_index=step_index,
        strategy="single_call",
        prompt="Answer the query.",
        max_output_tokens=256,
        max_result_bytes=1024,
    )


def _claim(call: PublicRlmCallSpec) -> PublicRlmClaimRequest:
    return PublicRlmClaimRequest(
        job_id=call.job_id,
        call_id=call.call_id,
        expected_revision=0,
        call_spec_digest=call.spec_digest,
        idempotency_key="claim-1",
    )


def _ticket() -> tuple[PublicRlmCallSpec, PublicRlmExecutionTicket]:
    spec = _spec()
    call = _call()
    return call, PublicRlmExecutionTicket.issue(call, spec, _claim(call))


def _commit(
    ticket: PublicRlmExecutionTicket,
    *,
    output_text: str | None = "A bounded answer.",
    outcome: str = "succeeded",
    **updates: object,
) -> PublicRlmCommitRequest:
    payload: dict[str, object] = {
        "job_id": ticket.job_id,
        "call_id": ticket.call_id,
        "expected_revision": 0,
        "ticket_digest": ticket.ticket_digest,
        "outcome": outcome,
        "output_text": output_text,
        "effective_model": "model-v1",
        "effective_reasoning_effort": "high",
        "input_tokens": 3,
        "output_tokens": 5,
        "idempotency_key": "commit-1",
    }
    payload.update(updates)
    return PublicRlmCommitRequest(**payload)


def _failure() -> FailureEnvelope:
    return FailureEnvelope(
        category=FailureCategory.WORKER,
        code="MODEL_FAILED",
        message="The delegated model failed.",
        retryable=False,
        certainty=OutcomeCertainty.CERTAIN,
    )


def test_issue_and_serialization_round_trip_for_public_rlm_models() -> None:
    spec = _spec()
    call, ticket = _ticket()
    claim = _claim(call)
    commit = _commit(ticket)
    receipt = PublicRlmCallerReceipt.issue(ticket, commit, max_result_bytes=1024)
    step = PublicRlmStepSummary.from_committed(
        call_spec=call,
        ticket=ticket,
        receipt=receipt,
    )
    result = PublicRlmTerminalResult.issue(
        job_id="job-1",
        strategy=spec.strategy,
        answer="A bounded answer.",
        steps=(step,),
    )
    view = PublicRlmJobView.issue(
        job_id="job-1",
        phase="succeeded",
        certainty=OutcomeCertainty.CERTAIN,
        revision=1,
        spec=spec,
        calls_committed=1,
        steps=(step,),
        result=result,
    )
    tool_result = PublicRlmToolResult(job=view)

    for model in (spec, call, claim, ticket, commit, receipt, step, result, view, tool_result):
        assert type(model).model_validate_json(model.model_dump_json(), strict=True) == model


def test_job_spec_enforces_strategy_call_bounds_and_strict_integers() -> None:
    with pytest.raises(ValidationError):
        _spec(max_model_calls=2)
    with pytest.raises(ValidationError):
        _spec(strategy="iterative_refinement", max_model_calls=1)
    with pytest.raises(ValidationError):
        PublicRlmJobSpec(
            query="query",
            strategy="single_call",
            executor_kind="codex-host",
            requested_model="model-v1",
            max_model_calls=True,
            max_output_tokens_per_call=1,
            max_result_bytes_per_call=1,
        )
    with pytest.raises(ValidationError):
        PublicRlmJobSpec(
            query="",
            strategy="single_call",
            executor_kind="codex-host",
            requested_model="model-v1",
            max_model_calls=1,
            max_output_tokens_per_call=1,
            max_result_bytes_per_call=1,
        )


def test_call_spec_uses_deterministic_ids_and_rejects_digest_tampering() -> None:
    first = _call()
    second = _call()
    assert first.call_id == second.call_id
    assert first.call_id.startswith("call-")
    assert len(first.call_id.removeprefix("call-")) == 32

    tampered_prompt = first.model_dump(mode="json")
    tampered_prompt["prompt"] = "A different prompt."
    with pytest.raises(ValidationError):
        PublicRlmCallSpec.model_validate(tampered_prompt, strict=True)

    tampered_id = first.model_dump(mode="json")
    tampered_id["call_id"] = "call-" + "0" * 32
    with pytest.raises(ValidationError):
        PublicRlmCallSpec.model_validate(tampered_id, strict=True)

    tampered_digest = first.model_dump(mode="json")
    tampered_digest["spec_digest"] = canonical_sha256({"tampered": True})
    with pytest.raises(ValidationError):
        PublicRlmCallSpec.model_validate(tampered_digest, strict=True)


def test_job_route_is_trimmed_and_claim_ticket_binds_exact_call() -> None:
    spec = _spec()
    call = _call()
    claim = _claim(call)
    for field_name in ("executor_kind", "requested_model", "requested_reasoning_effort"):
        payload = spec.model_dump(mode="json")
        payload[field_name] = f" {payload[field_name]}"
        with pytest.raises(ValidationError):
            PublicRlmJobSpec.model_validate(payload, strict=True)

    with pytest.raises(ValueError):
        PublicRlmExecutionTicket.issue(
            call,
            spec,
            claim.model_copy(update={"job_id": "other-job"}),
        )
    with pytest.raises(ValueError):
        PublicRlmExecutionTicket.issue(
            call,
            spec,
            claim.model_copy(update={"call_spec_digest": canonical_sha256({"wrong": True})}),
        )
    with pytest.raises(ValueError):
        PublicRlmExecutionTicket.issue(
            call,
            spec.model_copy(update={"max_output_tokens_per_call": 129}),
            claim,
        )

    ticket = PublicRlmExecutionTicket.issue(call, spec, claim)
    assert ticket.executor_kind == spec.executor_kind
    assert ticket.requested_model == spec.requested_model
    assert ticket.requested_reasoning_effort == spec.requested_reasoning_effort
    assert ticket.job_spec_digest == canonical_sha256(spec)
    payload = ticket.model_dump(mode="json")
    payload["ticket_digest"] = canonical_sha256({"tampered": True})
    with pytest.raises(ValidationError):
        PublicRlmExecutionTicket.model_validate(payload, strict=True)


def test_commit_request_enforces_outcome_receipt_and_effective_route_invariants() -> None:
    _call_spec, ticket = _ticket()
    commit = _commit(ticket)

    missing_success_output = commit.model_dump(mode="json")
    missing_success_output["output_text"] = None
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(missing_success_output, strict=True)

    empty_success_output = commit.model_dump(mode="json")
    empty_success_output["output_text"] = ""
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(empty_success_output, strict=True)

    success_failure = commit.model_dump(mode="json")
    success_failure["failure_code"] = "MODEL_FAILED"
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(success_failure, strict=True)

    non_success_output = commit.model_dump(mode="json")
    non_success_output.update(outcome="outcome_unknown")
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(non_success_output, strict=True)

    failed_without_code = commit.model_dump(mode="json")
    failed_without_code.update(outcome="failed_certain", output_text=None)
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(failed_without_code, strict=True)

    with pytest.raises(ValidationError):
        _commit(ticket, effective_model=None, effective_reasoning_effort="high")
    with pytest.raises(ValidationError):
        _commit(ticket, effective_model=" model-v1")

    host_pair_missing_digest = commit.model_dump(mode="json")
    host_pair_missing_digest["host_receipt_id"] = "host-receipt-1"
    with pytest.raises(ValidationError):
        PublicRlmCommitRequest.model_validate(host_pair_missing_digest, strict=True)


def test_caller_receipt_uses_utf8_bytes_redacts_oversize_and_reports_route() -> None:
    _call_spec, ticket = _ticket()
    unicode_commit = _commit(ticket, output_text="é")
    unicode_receipt = PublicRlmCallerReceipt.issue(ticket, unicode_commit, max_result_bytes=2)
    assert unicode_receipt.output_size_bytes == 2
    assert unicode_receipt.output_text == "é"
    assert unicode_receipt.output_digest == canonical_sha256("é")
    assert unicode_receipt.within_output_bound

    oversized_commit = _commit(ticket, output_text="🙂")
    oversized = PublicRlmCallerReceipt.issue(ticket, oversized_commit, max_result_bytes=3)
    assert oversized.output_text is None
    assert oversized.output_size_bytes == 4
    assert oversized.output_digest == canonical_sha256("🙂")
    assert not oversized.within_output_bound

    mismatched_route = PublicRlmCallerReceipt.issue(
        ticket,
        _commit(ticket, effective_model="different-model"),
        max_result_bytes=1024,
    )
    assert mismatched_route.route_matched is False
    no_effective_route = PublicRlmCallerReceipt.issue(
        ticket,
        _commit(ticket, effective_model=None, effective_reasoning_effort=None),
        max_result_bytes=1024,
    )
    assert no_effective_route.route_matched is None


def test_caller_receipt_host_assurance_ticket_mismatch_and_digest_tampering() -> None:
    _call_spec, ticket = _ticket()
    caller_reported = PublicRlmCallerReceipt.issue(
        ticket,
        _commit(ticket),
        max_result_bytes=1024,
    )
    assert caller_reported.provenance_assurance == "caller_reported"

    host_bound_request = _commit(
        ticket,
        host_receipt_id="host-receipt-1",
        host_receipt_digest=canonical_sha256({"host": 1}),
    )
    host_bound = PublicRlmCallerReceipt.issue(ticket, host_bound_request, max_result_bytes=1024)
    assert host_bound.provenance_assurance == "host_receipt_bound"

    with pytest.raises(ValueError):
        PublicRlmCallerReceipt.issue(
            ticket,
            _commit(ticket, ticket_digest=canonical_sha256({"wrong": True})),
            max_result_bytes=1024,
        )

    for field_name, value in (
        ("output_size_bytes", 999),
        ("output_digest", canonical_sha256({"wrong": True})),
        ("receipt_digest", canonical_sha256({"wrong": True})),
    ):
        payload = caller_reported.model_dump(mode="json")
        payload[field_name] = value
        with pytest.raises(ValidationError):
            PublicRlmCallerReceipt.model_validate(payload, strict=True)


def test_step_summary_requires_successful_in_bound_receipt() -> None:
    call, ticket = _ticket()
    failed_receipt = PublicRlmCallerReceipt.issue(
        ticket,
        _commit(
            ticket,
            outcome="failed_certain",
            output_text=None,
            failure_code="MODEL_FAILED",
        ),
        max_result_bytes=1024,
    )
    with pytest.raises(ValueError):
        PublicRlmStepSummary.from_committed(
            call_spec=call,
            ticket=ticket,
            receipt=failed_receipt,
        )

    oversized_receipt = PublicRlmCallerReceipt.issue(
        ticket,
        _commit(ticket, output_text="🙂"),
        max_result_bytes=3,
    )
    with pytest.raises(ValueError):
        PublicRlmStepSummary.from_committed(
            call_spec=call,
            ticket=ticket,
            receipt=oversized_receipt,
        )


def test_terminal_result_binds_trace_and_requires_contiguous_unique_steps() -> None:
    call, ticket = _ticket()
    receipt = PublicRlmCallerReceipt.issue(ticket, _commit(ticket), max_result_bytes=1024)
    step = PublicRlmStepSummary.from_committed(
        call_spec=call,
        ticket=ticket,
        receipt=receipt,
    )
    terminal = PublicRlmTerminalResult.issue(
        job_id="job-1",
        strategy="single_call",
        answer="answer",
        steps=(step,),
    )
    tampered = terminal.model_dump(mode="json")
    tampered["answer"] = "changed"
    with pytest.raises(ValidationError):
        PublicRlmTerminalResult.model_validate(tampered, strict=True)

    with pytest.raises(ValueError):
        PublicRlmTerminalResult.issue(
            job_id="job-1",
            strategy="single_call",
            answer="answer",
            steps=(step.model_copy(update={"step_index": 1}),),
        )
    with pytest.raises(ValueError):
        PublicRlmTerminalResult.issue(
            job_id="job-1",
            strategy="single_call",
            answer="answer",
            steps=(step, step.model_copy(update={"step_index": 1})),
        )


def test_job_view_phase_relationships_are_explicit() -> None:
    spec = _spec()
    call, ticket = _ticket()
    receipt = PublicRlmCallerReceipt.issue(ticket, _commit(ticket), max_result_bytes=1024)
    step = PublicRlmStepSummary.from_committed(
        call_spec=call,
        ticket=ticket,
        receipt=receipt,
    )
    result = PublicRlmTerminalResult.issue(
        job_id="job-1",
        strategy="single_call",
        answer="answer",
        steps=(step,),
    )

    valid_views = (
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="pending_model_call",
            certainty=OutcomeCertainty.CERTAIN,
            revision=0,
            spec=spec,
            calls_committed=0,
            pending_call=call,
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="awaiting_caller_result",
            certainty=OutcomeCertainty.CERTAIN,
            revision=0,
            spec=spec,
            calls_committed=0,
            active_ticket=ticket,
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="cancel_requested",
            certainty=OutcomeCertainty.INDETERMINATE,
            revision=0,
            spec=spec,
            calls_committed=0,
            active_ticket=ticket,
            failure_code="RLM_CANCEL_REQUESTED_AFTER_CLAIM",
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="succeeded",
            certainty=OutcomeCertainty.CERTAIN,
            revision=1,
            spec=spec,
            calls_committed=1,
            steps=(step,),
            result=result,
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="failed",
            certainty=OutcomeCertainty.CERTAIN,
            revision=1,
            spec=spec,
            calls_committed=0,
            failure_code="MODEL_FAILED",
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="cancelled",
            certainty=OutcomeCertainty.CERTAIN,
            revision=1,
            spec=spec,
            calls_committed=0,
        ),
        PublicRlmJobView.issue(
            job_id="job-1",
            phase="indeterminate",
            certainty=OutcomeCertainty.INDETERMINATE,
            revision=1,
            spec=spec,
            calls_committed=0,
            active_ticket=ticket,
            failure_code="OUTCOME_UNKNOWN",
        ),
    )
    assert [view.phase for view in valid_views] == [
        "pending_model_call",
        "awaiting_caller_result",
        "cancel_requested",
        "succeeded",
        "failed",
        "cancelled",
        "indeterminate",
    ]

    common = {
        "job_id": "job-1",
        "certainty": OutcomeCertainty.CERTAIN,
        "revision": 0,
        "spec": spec,
        "calls_committed": 0,
    }
    invalid_views = (
        {**common, "phase": "pending_model_call"},
        {**common, "phase": "awaiting_caller_result"},
        {**common, "phase": "succeeded"},
        {**common, "phase": "failed"},
        {**common, "phase": "cancelled", "active_ticket": ticket},
        {**common, "phase": "cancel_requested"},
        {
            **common,
            "phase": "indeterminate",
            "certainty": OutcomeCertainty.INDETERMINATE,
        },
    )
    for payload in invalid_views:
        with pytest.raises(ValueError):
            PublicRlmJobView.issue(**payload)

    tampered = valid_views[3].model_dump(mode="json")
    tampered["job_id"] = "other-job"
    with pytest.raises(ValidationError):
        PublicRlmJobView.model_validate(tampered, strict=True)


def test_tool_result_requires_exactly_one_of_job_or_failure() -> None:
    spec = _spec()
    job = PublicRlmJobView.issue(
        job_id="job-1",
        phase="cancelled",
        certainty=OutcomeCertainty.CERTAIN,
        revision=1,
        spec=spec,
        calls_committed=0,
    )
    failure = _failure()
    assert PublicRlmToolResult(job=job).job == job
    assert PublicRlmToolResult(failure=failure).failure == failure
    with pytest.raises(ValidationError):
        PublicRlmToolResult()
    with pytest.raises(ValidationError):
        PublicRlmToolResult(job=job, failure=failure)
