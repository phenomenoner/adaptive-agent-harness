"""Transport-neutral bounded RLM contract models."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aar.broker_models import BrokerCallTrace, BrokerReceipt, BrokerUsage
from aar.canonical import canonical_sha256
from aar.schemas import Digest, OperationRef, OperationState, Revision, StrictModel
from aar.versions import RLM_SCHEMA_VERSION

RlmStrategyName = Literal["baseline", "evidence_synthesis"]
RlmActionKind = Literal["evidence.query", "model.request"]


class RlmJobSpec(StrictModel):
    schema_version: Literal["aar.rlm.v1"] = RLM_SCHEMA_VERSION
    query: Annotated[str, Field(min_length=1, max_length=65_536, strict=True)]
    strategy: RlmStrategyName
    max_steps: Annotated[int, Field(ge=1, le=32, strict=True)]

    @model_validator(mode="after")
    def strategy_fits_bound(self) -> Self:
        minimum = 1 if self.strategy == "baseline" else 2
        if self.max_steps < minimum:
            raise ValueError(
                f"strategy {self.strategy} requires at least {minimum} bounded steps"
            )
        return self


class RlmAction(StrictModel):
    schema_version: Literal["aar.rlm.v1"] = RLM_SCHEMA_VERSION
    kind: RlmActionKind
    text: str


class RlmStep(StrictModel):
    schema_version: Literal["aar.rlm.v1"] = RLM_SCHEMA_VERSION
    index: Revision
    action: RlmActionKind
    request_digest: Digest
    broker_call_sequence: int
    grant_id: str
    receipt: BrokerReceipt


class RlmResult(StrictModel):
    schema_version: Literal["aar.rlm.v1"] = RLM_SCHEMA_VERSION
    operation: OperationRef
    strategy: RlmStrategyName
    answer: str
    steps: tuple[RlmStep, ...]
    usage: BrokerUsage
    broker_trace: tuple[BrokerCallTrace, ...]
    trace_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        operation: OperationRef,
        strategy: RlmStrategyName,
        answer: str,
        steps: tuple[RlmStep, ...],
        usage: BrokerUsage,
        broker_trace: tuple[BrokerCallTrace, ...],
    ) -> RlmResult:
        payload = {
            "operation": operation,
            "strategy": strategy,
            "answer": answer,
            "steps": steps,
            "usage": usage,
            "broker_trace": broker_trace,
        }
        return cls(
            operation=operation,
            strategy=strategy,
            answer=answer,
            steps=steps,
            usage=usage,
            broker_trace=broker_trace,
            trace_digest=canonical_sha256(payload),
        )

    @model_validator(mode="after")
    def trace_is_content_bound(self) -> Self:
        payload = {
            "operation": self.operation,
            "strategy": self.strategy,
            "answer": self.answer,
            "steps": self.steps,
            "usage": self.usage,
            "broker_trace": self.broker_trace,
        }
        if self.trace_digest != canonical_sha256(payload):
            raise ValueError("RLM trace digest does not match canonical trace bytes")
        return self


class RlmJobSnapshot(StrictModel):
    schema_version: Literal["aar.rlm.v1"] = RLM_SCHEMA_VERSION
    operation: OperationRef
    state: OperationState
    spec: RlmJobSpec
    steps: tuple[RlmStep, ...]
    usage: BrokerUsage
    result: RlmResult | None = None


RLM_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "rlm_action": RlmAction,
    "rlm_job_snapshot": RlmJobSnapshot,
    "rlm_job_spec": RlmJobSpec,
    "rlm_result": RlmResult,
    "rlm_step": RlmStep,
}
