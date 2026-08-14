"""Public caller-delegated RLM contract models."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aar.canonical import canonical_sha256
from aar.schemas import (
    Digest,
    FailureEnvelope,
    OpaqueToken,
    OutcomeCertainty,
    Revision,
    StrictModel,
)

PublicRlmStrategy = Literal["single_call", "iterative_refinement"]
PublicRlmPhase = Literal[
    "pending_model_call",
    "awaiting_caller_result",
    "cancel_requested",
    "succeeded",
    "failed",
    "cancelled",
    "indeterminate",
]
PublicRlmCallOutcome = Literal["succeeded", "failed_certain", "outcome_unknown"]
PublicRlmProvenanceAssurance = Literal["caller_reported", "host_receipt_bound"]

_PUBLIC_RLM_SCHEMA_VERSION = "aar.public-rlm.v1"
_PUBLIC_RLM_STRATEGY_VERSION = "aar.public-rlm-strategy.v1"

_ExecutorKind = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
_ModelName = Annotated[str, Field(min_length=1, max_length=128, strict=True)]
_ReasoningEffort = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
_OutputTokenLimit = Annotated[int, Field(ge=1, le=32_768, strict=True)]
_ResultByteLimit = Annotated[int, Field(ge=1, le=262_144, strict=True)]
_OutputText = Annotated[str, Field(min_length=1, max_length=1_048_576, strict=True)]
_TokenCount = Annotated[int, Field(ge=0, le=100_000_000, strict=True)]
_ByteCount = Annotated[int, Field(ge=0, strict=True)]
_FailureCode = Annotated[str, Field(min_length=1, max_length=64, strict=True)]
_FailureMessage = Annotated[str, Field(min_length=1, max_length=512, strict=True)]
_HostReceiptId = Annotated[str, Field(min_length=1, max_length=256, strict=True)]


def _without_digest(model: StrictModel, digest_field: str) -> dict[str, object]:
    return model.model_dump(mode="json", exclude={digest_field})


def _derived_identifier(prefix: str, material: object) -> str:
    return f"{prefix}-{canonical_sha256(material).removeprefix('sha256:')[:32]}"


def _reject_edge_whitespace(field_name: str, value: str | None) -> None:
    if value is not None and value != value.strip():
        raise ValueError(f"{field_name} must not have leading or trailing whitespace")


def _require_receipt_pair(host_receipt_id: str | None, host_receipt_digest: str | None) -> None:
    if (host_receipt_id is None) != (host_receipt_digest is None):
        raise ValueError("host receipt id and digest must be supplied together")


def _validate_step_sequence(steps: tuple[PublicRlmStepSummary, ...]) -> None:
    indexes = [step.step_index for step in steps]
    if indexes != list(range(len(steps))):
        raise ValueError("RLM steps must be contiguous from index zero")
    call_ids = [step.call_id for step in steps]
    if len(call_ids) != len(set(call_ids)):
        raise ValueError("RLM steps must have unique call ids")


class PublicRlmJobSpec(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    query: Annotated[str, Field(min_length=1, max_length=16_384, strict=True)]
    strategy: PublicRlmStrategy
    executor_kind: _ExecutorKind
    requested_model: _ModelName
    requested_reasoning_effort: _ReasoningEffort | None = None
    max_model_calls: Annotated[int, Field(ge=1, le=8, strict=True)]
    max_output_tokens_per_call: _OutputTokenLimit
    max_result_bytes_per_call: _ResultByteLimit
    model_selection_owner: Literal["caller"] = "caller"

    @model_validator(mode="after")
    def strategy_fits_call_bound(self) -> Self:
        if self.strategy == "single_call" and self.max_model_calls != 1:
            raise ValueError("single_call strategy requires exactly one model call")
        if self.strategy == "iterative_refinement" and self.max_model_calls < 2:
            raise ValueError("iterative_refinement requires at least two model calls")
        _reject_edge_whitespace("executor_kind", self.executor_kind)
        _reject_edge_whitespace("requested_model", self.requested_model)
        _reject_edge_whitespace(
            "requested_reasoning_effort", self.requested_reasoning_effort
        )
        return self


class PublicRlmCallSpec(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    step_index: Revision
    call_id: OpaqueToken
    strategy: PublicRlmStrategy
    strategy_version: Literal["aar.public-rlm-strategy.v1"] = "aar.public-rlm-strategy.v1"
    prompt: Annotated[str, Field(min_length=1, max_length=65_536, strict=True)]
    prompt_digest: Digest
    max_output_tokens: _OutputTokenLimit
    max_result_bytes: _ResultByteLimit
    tools_allowed: Literal[False] = False
    model_selection_owner: Literal["caller"] = "caller"
    spec_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        job_id: str,
        step_index: int,
        strategy: PublicRlmStrategy,
        prompt: str,
        max_output_tokens: int,
        max_result_bytes: int,
    ) -> Self:
        prompt_digest = canonical_sha256(prompt)
        call_id = _derived_identifier(
            "call",
            {"job_id": job_id, "step_index": step_index, "prompt": prompt},
        )
        payload = {
            "schema_version": _PUBLIC_RLM_SCHEMA_VERSION,
            "job_id": job_id,
            "step_index": step_index,
            "call_id": call_id,
            "strategy": strategy,
            "strategy_version": _PUBLIC_RLM_STRATEGY_VERSION,
            "prompt": prompt,
            "prompt_digest": prompt_digest,
            "max_output_tokens": max_output_tokens,
            "max_result_bytes": max_result_bytes,
            "tools_allowed": False,
            "model_selection_owner": "caller",
        }
        return cls(**payload, spec_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def digests_and_call_id_are_content_bound(self) -> Self:
        expected_prompt_digest = canonical_sha256(self.prompt)
        if self.prompt_digest != expected_prompt_digest:
            raise ValueError("RLM prompt digest does not match canonical prompt bytes")
        expected_call_id = _derived_identifier(
            "call",
            {"job_id": self.job_id, "step_index": self.step_index, "prompt": self.prompt},
        )
        if self.call_id != expected_call_id:
            raise ValueError("RLM call id does not match canonical call material")
        if self.spec_digest != canonical_sha256(_without_digest(self, "spec_digest")):
            raise ValueError("RLM call specification digest mismatch")
        return self


class PublicRlmClaimRequest(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    call_id: OpaqueToken
    expected_revision: Revision
    call_spec_digest: Digest
    idempotency_key: OpaqueToken


class PublicRlmExecutionTicket(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    call_id: OpaqueToken
    step_index: Revision
    call_spec_digest: Digest
    job_spec_digest: Digest
    executor_kind: _ExecutorKind
    requested_model: _ModelName
    requested_reasoning_effort: _ReasoningEffort | None = None
    requested_max_output_tokens: _OutputTokenLimit
    claim_idempotency_key: OpaqueToken
    ticket_id: OpaqueToken
    ticket_digest: Digest

    @classmethod
    def issue(
        cls,
        call_spec: PublicRlmCallSpec,
        job_spec: PublicRlmJobSpec,
        claim_request: PublicRlmClaimRequest,
    ) -> Self:
        if claim_request.job_id != call_spec.job_id:
            raise ValueError("claim request job id does not match call specification")
        if claim_request.call_id != call_spec.call_id:
            raise ValueError("claim request call id does not match call specification")
        if claim_request.call_spec_digest != call_spec.spec_digest:
            raise ValueError("claim request call specification digest does not match")
        if (
            call_spec.strategy != job_spec.strategy
            or call_spec.max_output_tokens != job_spec.max_output_tokens_per_call
            or call_spec.max_result_bytes != job_spec.max_result_bytes_per_call
        ):
            raise ValueError("call specification does not inherit the fixed job bounds")
        payload = {
            "schema_version": _PUBLIC_RLM_SCHEMA_VERSION,
            "job_id": call_spec.job_id,
            "call_id": call_spec.call_id,
            "step_index": call_spec.step_index,
            "call_spec_digest": call_spec.spec_digest,
            "job_spec_digest": canonical_sha256(job_spec),
            "executor_kind": job_spec.executor_kind,
            "requested_model": job_spec.requested_model,
            "requested_reasoning_effort": job_spec.requested_reasoning_effort,
            "requested_max_output_tokens": job_spec.max_output_tokens_per_call,
            "claim_idempotency_key": claim_request.idempotency_key,
        }
        ticket_id = _derived_identifier("ticket", payload)
        ticket_payload = {**payload, "ticket_id": ticket_id}
        return cls(
            **ticket_payload,
            ticket_digest=canonical_sha256(ticket_payload),
        )

    @model_validator(mode="after")
    def ticket_is_content_bound(self) -> Self:
        _reject_edge_whitespace("executor_kind", self.executor_kind)
        _reject_edge_whitespace("requested_model", self.requested_model)
        _reject_edge_whitespace("requested_reasoning_effort", self.requested_reasoning_effort)
        id_material = self.model_dump(
            mode="json",
            exclude={"ticket_id", "ticket_digest"},
        )
        expected_ticket_id = _derived_identifier("ticket", id_material)
        if self.ticket_id != expected_ticket_id:
            raise ValueError("RLM execution ticket id mismatch")
        if self.ticket_digest != canonical_sha256(_without_digest(self, "ticket_digest")):
            raise ValueError("RLM execution ticket digest mismatch")
        return self


class PublicRlmCommitRequest(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    call_id: OpaqueToken
    expected_revision: Revision
    ticket_digest: Digest
    outcome: PublicRlmCallOutcome
    output_text: _OutputText | None = None
    effective_model: _ModelName | None = None
    effective_reasoning_effort: _ReasoningEffort | None = None
    input_tokens: _TokenCount | None = None
    output_tokens: _TokenCount | None = None
    failure_code: _FailureCode | None = None
    failure_message: _FailureMessage | None = None
    host_receipt_id: _HostReceiptId | None = None
    host_receipt_digest: Digest | None = None
    idempotency_key: OpaqueToken

    @model_validator(mode="after")
    def outcome_and_route_fields_are_coherent(self) -> Self:
        if self.outcome == "succeeded":
            if self.output_text is None:
                raise ValueError("succeeded commit requires output_text")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded commit cannot contain failure fields")
        elif self.output_text is not None:
            raise ValueError("non-success commit cannot contain output_text")
        if self.outcome == "failed_certain" and self.failure_code is None:
            raise ValueError("failed_certain commit requires failure_code")
        _require_receipt_pair(self.host_receipt_id, self.host_receipt_digest)
        if self.effective_reasoning_effort is not None and self.effective_model is None:
            raise ValueError("effective reasoning effort requires effective model")
        _reject_edge_whitespace("effective_model", self.effective_model)
        _reject_edge_whitespace(
            "effective_reasoning_effort", self.effective_reasoning_effort
        )
        return self


class PublicRlmCallerReceipt(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    ticket_digest: Digest
    outcome: PublicRlmCallOutcome
    output_text: _OutputText | None = None
    output_digest: Digest | None = None
    output_size_bytes: _ByteCount
    requested_model: _ModelName
    requested_reasoning_effort: _ReasoningEffort | None = None
    effective_model: _ModelName | None = None
    effective_reasoning_effort: _ReasoningEffort | None = None
    input_tokens: _TokenCount | None = None
    output_tokens: _TokenCount | None = None
    failure_code: _FailureCode | None = None
    failure_message: _FailureMessage | None = None
    host_receipt_id: _HostReceiptId | None = None
    host_receipt_digest: Digest | None = None
    provenance_assurance: PublicRlmProvenanceAssurance
    route_matched: bool | None = None
    within_output_bound: bool
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        ticket: PublicRlmExecutionTicket,
        request: PublicRlmCommitRequest,
        max_result_bytes: int,
    ) -> Self:
        if request.job_id != ticket.job_id or request.call_id != ticket.call_id:
            raise ValueError("commit request does not target the exact execution ticket")
        if request.ticket_digest != ticket.ticket_digest:
            raise ValueError("commit request ticket digest does not match execution ticket")
        if isinstance(max_result_bytes, bool) or not isinstance(max_result_bytes, int):
            raise ValueError("max_result_bytes must be a strict integer")
        if max_result_bytes < 1:
            raise ValueError("max_result_bytes must be positive")

        output_text = request.output_text
        if output_text is None:
            output_digest = None
            output_size_bytes = 0
            within_output_bound = True
        else:
            output_digest = canonical_sha256(output_text)
            output_size_bytes = len(output_text.encode("utf-8"))
            within_output_bound = output_size_bytes <= max_result_bytes
            if not within_output_bound:
                output_text = None

        route_matched = (
            None
            if request.effective_model is None
            else (
                request.effective_model == ticket.requested_model
                and request.effective_reasoning_effort
                == ticket.requested_reasoning_effort
            )
        )
        provenance_assurance: PublicRlmProvenanceAssurance = (
            "host_receipt_bound"
            if request.host_receipt_id is not None and request.host_receipt_digest is not None
            else "caller_reported"
        )
        payload = {
            "schema_version": _PUBLIC_RLM_SCHEMA_VERSION,
            "ticket_digest": ticket.ticket_digest,
            "outcome": request.outcome,
            "output_text": output_text,
            "output_digest": output_digest,
            "output_size_bytes": output_size_bytes,
            "requested_model": ticket.requested_model,
            "requested_reasoning_effort": ticket.requested_reasoning_effort,
            "effective_model": request.effective_model,
            "effective_reasoning_effort": request.effective_reasoning_effort,
            "input_tokens": request.input_tokens,
            "output_tokens": request.output_tokens,
            "failure_code": request.failure_code,
            "failure_message": request.failure_message,
            "host_receipt_id": request.host_receipt_id,
            "host_receipt_digest": request.host_receipt_digest,
            "provenance_assurance": provenance_assurance,
            "route_matched": route_matched,
            "within_output_bound": within_output_bound,
        }
        return cls(**payload, receipt_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def receipt_is_content_bound_and_coherent(self) -> Self:
        _reject_edge_whitespace("requested_model", self.requested_model)
        _reject_edge_whitespace(
            "requested_reasoning_effort", self.requested_reasoning_effort
        )
        _reject_edge_whitespace("effective_model", self.effective_model)
        _reject_edge_whitespace(
            "effective_reasoning_effort", self.effective_reasoning_effort
        )
        if self.effective_reasoning_effort is not None and self.effective_model is None:
            raise ValueError("effective reasoning effort requires effective model")
        _require_receipt_pair(self.host_receipt_id, self.host_receipt_digest)

        expected_assurance: PublicRlmProvenanceAssurance = (
            "host_receipt_bound"
            if self.host_receipt_id is not None and self.host_receipt_digest is not None
            else "caller_reported"
        )
        if self.provenance_assurance != expected_assurance:
            raise ValueError("caller receipt provenance assurance does not match host receipt")

        expected_route = (
            None
            if self.effective_model is None
            else (
                self.effective_model == self.requested_model
                and self.effective_reasoning_effort == self.requested_reasoning_effort
            )
        )
        if self.route_matched != expected_route:
            raise ValueError("caller receipt route_matched does not match effective route")

        if self.output_text is not None:
            expected_size = len(self.output_text.encode("utf-8"))
            if self.output_size_bytes != expected_size:
                raise ValueError("caller receipt output byte size mismatch")
            if self.output_digest != canonical_sha256(self.output_text):
                raise ValueError("caller receipt output digest mismatch")
            if not self.within_output_bound:
                raise ValueError("retained caller output must be within its result bound")
        elif self.outcome == "succeeded":
            if self.output_digest is None or self.output_size_bytes <= 0:
                raise ValueError("redacted successful receipt must retain output digest and size")
            if self.within_output_bound:
                raise ValueError("redacted successful receipt must be outside its result bound")
        else:
            if self.output_digest is not None or self.output_size_bytes != 0:
                raise ValueError("non-success receipt cannot contain output content")
            if not self.within_output_bound:
                raise ValueError("empty receipt output must be within its result bound")

        if self.outcome == "succeeded":
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded receipt cannot contain failure fields")
        elif self.outcome == "failed_certain" and self.failure_code is None:
            raise ValueError("failed_certain receipt requires failure_code")

        if self.receipt_digest != canonical_sha256(_without_digest(self, "receipt_digest")):
            raise ValueError("caller receipt digest mismatch")
        return self


class PublicRlmStepSummary(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    step_index: Revision
    call_id: OpaqueToken
    prompt_digest: Digest
    ticket_digest: Digest
    result_digest: Digest
    result_size_bytes: _ByteCount
    requested_model: _ModelName
    requested_reasoning_effort: _ReasoningEffort | None = None
    effective_model: _ModelName | None = None
    effective_reasoning_effort: _ReasoningEffort | None = None
    provenance_assurance: PublicRlmProvenanceAssurance
    input_tokens: _TokenCount | None = None
    output_tokens: _TokenCount | None = None

    @classmethod
    def from_committed(
        cls,
        *,
        call_spec: PublicRlmCallSpec,
        ticket: PublicRlmExecutionTicket,
        receipt: PublicRlmCallerReceipt,
    ) -> Self:
        if (
            ticket.job_id != call_spec.job_id
            or ticket.call_id != call_spec.call_id
            or ticket.step_index != call_spec.step_index
            or ticket.call_spec_digest != call_spec.spec_digest
        ):
            raise ValueError("execution ticket is not bound to the committed call specification")
        if receipt.ticket_digest != ticket.ticket_digest:
            raise ValueError("caller receipt is not bound to the execution ticket")
        if (
            receipt.outcome != "succeeded"
            or receipt.output_text is None
            or receipt.output_digest is None
            or not receipt.within_output_bound
            or receipt.output_size_bytes > call_spec.max_result_bytes
        ):
            raise ValueError("step summary requires a successful in-bound caller receipt")
        if (
            receipt.requested_model != ticket.requested_model
            or receipt.requested_reasoning_effort != ticket.requested_reasoning_effort
        ):
            raise ValueError("caller receipt route does not match the execution ticket")
        return cls(
            step_index=call_spec.step_index,
            call_id=call_spec.call_id,
            prompt_digest=call_spec.prompt_digest,
            ticket_digest=ticket.ticket_digest,
            result_digest=receipt.output_digest,
            result_size_bytes=receipt.output_size_bytes,
            requested_model=receipt.requested_model,
            requested_reasoning_effort=receipt.requested_reasoning_effort,
            effective_model=receipt.effective_model,
            effective_reasoning_effort=receipt.effective_reasoning_effort,
            provenance_assurance=receipt.provenance_assurance,
            input_tokens=receipt.input_tokens,
            output_tokens=receipt.output_tokens,
        )

    @classmethod
    def issue(
        cls,
        *,
        call_spec: PublicRlmCallSpec,
        ticket: PublicRlmExecutionTicket,
        receipt: PublicRlmCallerReceipt,
    ) -> Self:
        return cls.from_committed(call_spec=call_spec, ticket=ticket, receipt=receipt)

    @model_validator(mode="after")
    def route_fields_are_coherent(self) -> Self:
        _reject_edge_whitespace("requested_model", self.requested_model)
        _reject_edge_whitespace(
            "requested_reasoning_effort", self.requested_reasoning_effort
        )
        _reject_edge_whitespace("effective_model", self.effective_model)
        _reject_edge_whitespace(
            "effective_reasoning_effort", self.effective_reasoning_effort
        )
        if self.effective_reasoning_effort is not None and self.effective_model is None:
            raise ValueError("effective reasoning effort requires effective model")
        return self


class PublicRlmTerminalResult(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    strategy: PublicRlmStrategy
    answer: Annotated[str, Field(min_length=1, max_length=262_144, strict=True)]
    steps: Annotated[tuple[PublicRlmStepSummary, ...], Field(max_length=8)]
    trace_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        job_id: str,
        strategy: PublicRlmStrategy,
        answer: str,
        steps: tuple[PublicRlmStepSummary, ...],
    ) -> Self:
        payload = {
            "schema_version": _PUBLIC_RLM_SCHEMA_VERSION,
            "job_id": job_id,
            "strategy": strategy,
            "answer": answer,
            "steps": steps,
        }
        return cls(**payload, trace_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def trace_is_content_bound(self) -> Self:
        _validate_step_sequence(self.steps)
        if self.trace_digest != canonical_sha256(_without_digest(self, "trace_digest")):
            raise ValueError("RLM terminal trace digest mismatch")
        return self


class PublicRlmJobView(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job_id: OpaqueToken
    phase: PublicRlmPhase
    certainty: OutcomeCertainty
    revision: Revision
    spec: PublicRlmJobSpec
    calls_committed: Annotated[int, Field(ge=0, le=8, strict=True)]
    pending_call: PublicRlmCallSpec | None = None
    active_ticket: PublicRlmExecutionTicket | None = None
    steps: Annotated[tuple[PublicRlmStepSummary, ...], Field(max_length=8)] = ()
    result: PublicRlmTerminalResult | None = None
    failure_code: _FailureCode | None = None
    failure_message: _FailureMessage | None = None
    state_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        job_id: str,
        phase: PublicRlmPhase,
        certainty: OutcomeCertainty,
        revision: int,
        spec: PublicRlmJobSpec,
        calls_committed: int,
        pending_call: PublicRlmCallSpec | None = None,
        active_ticket: PublicRlmExecutionTicket | None = None,
        steps: tuple[PublicRlmStepSummary, ...] = (),
        result: PublicRlmTerminalResult | None = None,
        failure_code: str | None = None,
        failure_message: str | None = None,
    ) -> Self:
        payload = {
            "schema_version": _PUBLIC_RLM_SCHEMA_VERSION,
            "job_id": job_id,
            "phase": phase,
            "certainty": certainty,
            "revision": revision,
            "spec": spec,
            "calls_committed": calls_committed,
            "pending_call": pending_call,
            "active_ticket": active_ticket,
            "steps": steps,
            "result": result,
            "failure_code": failure_code,
            "failure_message": failure_message,
        }
        return cls(**payload, state_digest=canonical_sha256(payload))

    @model_validator(mode="after")
    def state_is_content_bound_and_phase_coherent(self) -> Self:
        if self.phase in {"cancel_requested", "indeterminate"}:
            if self.certainty is not OutcomeCertainty.INDETERMINATE:
                raise ValueError(
                    "cancel-requested and indeterminate phases require indeterminate certainty"
                )
        elif self.certainty is not OutcomeCertainty.CERTAIN:
            raise ValueError("non-indeterminate phase requires certain certainty")

        if self.phase == "pending_model_call":
            if (
                self.pending_call is None
                or self.active_ticket is not None
                or self.result is not None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError("pending phase requires only a pending call")
        elif self.phase == "awaiting_caller_result":
            if (
                self.active_ticket is None
                or self.pending_call is not None
                or self.result is not None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError("awaiting phase requires only an active ticket")
        elif self.phase == "succeeded":
            if (
                self.result is None
                or self.pending_call is not None
                or self.active_ticket is not None
                or self.failure_code is not None
                or self.failure_message is not None
            ):
                raise ValueError("succeeded phase requires only a terminal result")
        elif self.phase == "failed":
            if (
                self.failure_code is None
                or self.pending_call is not None
                or self.active_ticket is not None
                or self.result is not None
            ):
                raise ValueError("failed phase requires failure_code and no active call")
        elif self.phase == "cancelled":
            if (
                self.pending_call is not None
                or self.active_ticket is not None
                or self.result is not None
            ):
                raise ValueError("cancelled phase cannot retain a call, ticket, or result")
        elif self.phase == "cancel_requested":
            if (
                self.active_ticket is None
                or self.pending_call is not None
                or self.result is not None
                or self.failure_code is None
            ):
                raise ValueError(
                    "cancel_requested phase requires an active ticket and failure code"
                )
        elif self.phase == "indeterminate" and (
            self.failure_code is None
            or self.active_ticket is None
            or self.pending_call is not None
            or self.result is not None
        ):
            raise ValueError(
                "indeterminate phase requires an active ticket and failure code"
            )

        _validate_step_sequence(self.steps)
        if self.calls_committed != len(self.steps):
            raise ValueError("calls_committed must equal the retained step count")
        if self.calls_committed > self.spec.max_model_calls:
            raise ValueError("committed call count exceeds the job budget")
        if (
            self.pending_call is not None
            and self.pending_call.step_index != self.calls_committed
        ):
            raise ValueError("pending call index must follow the committed steps")
        if (
            self.active_ticket is not None
            and self.active_ticket.step_index != self.calls_committed
        ):
            raise ValueError("active ticket index must follow the committed steps")
        if self.result is not None and self.result.steps != self.steps:
            raise ValueError("terminal result steps must equal the job steps")

        if self.pending_call is not None and self.pending_call.job_id != self.job_id:
            raise ValueError("pending call belongs to a different job")
        if self.active_ticket is not None and self.active_ticket.job_id != self.job_id:
            raise ValueError("active ticket belongs to a different job")
        if self.result is not None and (
            self.result.job_id != self.job_id or self.result.strategy != self.spec.strategy
        ):
            raise ValueError("terminal result does not match the job view")
        if self.state_digest != canonical_sha256(_without_digest(self, "state_digest")):
            raise ValueError("RLM job state digest mismatch")
        return self


class PublicRlmToolResult(StrictModel):
    schema_version: Literal["aar.public-rlm.v1"] = "aar.public-rlm.v1"
    job: PublicRlmJobView | None = None
    failure: FailureEnvelope | None = None

    @model_validator(mode="after")
    def exactly_one_result_variant(self) -> Self:
        if (self.job is None) == (self.failure is None):
            raise ValueError("public RLM tool result must contain exactly one of job or failure")
        return self


__all__ = [
    "PublicRlmCallOutcome",
    "PublicRlmCallSpec",
    "PublicRlmCallerReceipt",
    "PublicRlmClaimRequest",
    "PublicRlmCommitRequest",
    "PublicRlmExecutionTicket",
    "PublicRlmJobSpec",
    "PublicRlmJobView",
    "PublicRlmPhase",
    "PublicRlmProvenanceAssurance",
    "PublicRlmStepSummary",
    "PublicRlmStrategy",
    "PublicRlmTerminalResult",
    "PublicRlmToolResult",
]
