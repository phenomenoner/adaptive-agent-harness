"""Project AAR model evidence into the external AAR-vs-Prime benchmark contract."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from aar.broker_models import ModelResponse
from aar.schemas import BudgetCounter, Digest, PositiveCounter, StrictModel

BenchmarkPurpose = Literal["primary", "coordination", "verification", "retry", "recovery"]
BenchmarkRouteValue = Annotated[
    str,
    Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._~:/+-]*$"),
]
RunId = Annotated[
    str,
    Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._-]{8,128}$"),
]


class BenchmarkRouteFragment(StrictModel):
    """Exact route object accepted by aar-prime.run-manifest.v1."""

    requested_provider: Literal["openai-codex"]
    requested_model: Literal["gpt-5.6-luna"]
    requested_effort: Literal["max"]
    effective_provider: Literal["openai-codex"]
    effective_model: Literal["gpt-5.6-luna"]
    effective_effort: Literal["max"]
    route_policy_digest: Digest
    proof_status: Literal["verified"] = "verified"
    cache_scope: BenchmarkRouteValue | None = None


class BenchmarkUsageLedgerRow(StrictModel):
    """One provider usage row accepted by aar-prime.usage-row.v1.

    ``receipt_digest`` binds the authoritative AAR route receipt. The AAR model
    execution journal separately retains the canonical provider-usage bytes.
    """

    schema_version: Literal["aar-prime.usage-row.v1"] = "aar-prime.usage-row.v1"
    run_id: RunId
    arm: Literal["aar"] = "aar"
    request_index: PositiveCounter
    purpose: BenchmarkPurpose
    provider: Literal["openai-codex"]
    model: Literal["gpt-5.6-luna"]
    reasoning_effort_requested: Literal["max"]
    reasoning_effort_effective: Literal["max"]
    input_tokens: BudgetCounter
    output_tokens: BudgetCounter
    total_tokens: BudgetCounter
    reasoning_tokens: BudgetCounter | None = None
    cache_read_tokens: BudgetCounter | None = None
    cache_write_tokens: BudgetCounter | None = None
    cost_usd: Annotated[float, Field(ge=0, strict=True)] | None = None
    attempt: PositiveCounter
    used_in_final_artifact: bool
    started_at: str | None = None
    completed_at: str | None = None
    receipt_digest: Digest


class AarPrimeBenchmarkEvidenceAdapter:
    """Create bounded route and usage fragments without owning a run manifest."""

    @staticmethod
    def _require_eligible_route(response: ModelResponse) -> None:
        receipt = response.route_receipt
        requested = receipt.requested
        effective = receipt.effective
        exact_route = (
            requested.provider,
            requested.model,
            requested.reasoning_effort,
            effective.provider,
            effective.model,
            effective.reasoning_effort,
        )
        if exact_route != (
            "openai-codex",
            "gpt-5.6-luna",
            "max",
            "openai-codex",
            "gpt-5.6-luna",
            "max",
        ):
            raise ValueError("benchmark evidence requires the exact Luna/max route")
        if requested.fallback_policy != "none" or receipt.fallback_chain:
            raise ValueError("benchmark evidence forbids provider fallback")
        if response.usage.retry_ordinal != 0 or response.usage.wasted:
            raise ValueError(
                "benchmark evidence forbids internal retry without authoritative attempt usage rows"
            )

    def route_fragment(self, response: ModelResponse) -> BenchmarkRouteFragment:
        self._require_eligible_route(response)
        receipt = response.route_receipt
        requested = receipt.requested
        return BenchmarkRouteFragment(
            requested_provider="openai-codex",
            requested_model="gpt-5.6-luna",
            requested_effort="max",
            effective_provider="openai-codex",
            effective_model="gpt-5.6-luna",
            effective_effort="max",
            route_policy_digest=requested.profile_digest,
            cache_scope=requested.cache_policy,
        )

    def usage_row(
        self,
        response: ModelResponse,
        *,
        run_id: str,
        request_index: int,
        purpose: BenchmarkPurpose,
        attempt: int,
        used_in_final_artifact: bool,
    ) -> BenchmarkUsageLedgerRow:
        self._require_eligible_route(response)
        usage = response.usage
        if usage.accounting_source != "provider_reported":
            raise ValueError("benchmark evidence requires provider-reported usage")
        receipt = response.route_receipt
        return BenchmarkUsageLedgerRow(
            run_id=run_id,
            request_index=request_index,
            purpose=purpose,
            provider="openai-codex",
            model="gpt-5.6-luna",
            reasoning_effort_requested="max",
            reasoning_effort_effective="max",
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            total_tokens=usage.total_tokens,
            reasoning_tokens=usage.reasoning_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            attempt=attempt,
            used_in_final_artifact=used_in_final_artifact,
            receipt_digest=receipt.receipt_digest,
        )
