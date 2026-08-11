"""Deterministic baseline-versus-RLM benchmark over transport-neutral brokers."""

from __future__ import annotations

import argparse
import tempfile
import time
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aar.broker_models import BrokerUsage, ModelRequest
from aar.canonical import canonical_sha256, pretty_json_bytes
from aar.rlm_models import RlmJobSpec, RlmResult
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import (
    Budget,
    Grant,
    HostRef,
    LaneRef,
    OperationRef,
    OutcomeCertainty,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
    StrictModel,
)

BENCHMARK_SCHEMA_VERSION = "aar.rlm-benchmark.v1"
BENCHMARK_NOW_MS = 1_700_000_000_000


class BenchmarkCase(StrictModel):
    case_id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,63}$", strict=True)]
    query: Annotated[str, Field(min_length=1, max_length=8_192, strict=True)]
    evidence_records: tuple[str, ...]
    expected_matches: tuple[str, ...]

    @model_validator(mode="after")
    def expectations_are_present(self) -> Self:
        if not self.expected_matches:
            raise ValueError("benchmark case requires at least one expected evidence match")
        missing = set(self.expected_matches) - set(self.evidence_records)
        if missing:
            raise ValueError("expected evidence matches must occur in evidence_records")
        return self


class BenchmarkPack(StrictModel):
    schema_version: Literal["aar.rlm-benchmark.v1"] = BENCHMARK_SCHEMA_VERSION
    name: str
    cases: tuple[BenchmarkCase, ...]


class BenchmarkMetrics(StrictModel):
    evidence_hits: int = Field(ge=0, strict=True)
    evidence_expected: int = Field(ge=1, strict=True)
    observed_elapsed_ms: int = Field(ge=0, strict=True)
    latency_budget_ms: int = Field(ge=1, strict=True)
    broker_calls: int = Field(ge=0, strict=True)
    usage: BrokerUsage
    certainty: OutcomeCertainty
    reconciliation_required: bool
    provider_credentials_available: bool
    effect_execution_available: bool
    final_delivery_available: bool


class BenchmarkObservation(StrictModel):
    mode: Literal["broker_baseline", "evidence_rlm"]
    answer_digest: str
    metrics: BenchmarkMetrics


class BenchmarkCaseResult(StrictModel):
    case_id: str
    baseline: BenchmarkObservation
    rlm: BenchmarkObservation
    evidence_hit_delta: int
    broker_call_delta: int
    input_token_delta: int


class BenchmarkSummary(StrictModel):
    case_count: int = Field(ge=1, strict=True)
    baseline_evidence_hits: int = Field(ge=0, strict=True)
    rlm_evidence_hits: int = Field(ge=0, strict=True)
    evidence_hit_delta: int
    broker_call_delta: int
    input_token_delta: int
    all_safety_boundaries_preserved: bool
    interpretation: str


class BenchmarkReport(StrictModel):
    schema_version: Literal["aar.rlm-benchmark.v1"] = BENCHMARK_SCHEMA_VERSION
    pack_name: str
    cases: tuple[BenchmarkCaseResult, ...]
    summary: BenchmarkSummary
    report_digest: str

    @classmethod
    def issue(
        cls,
        *,
        pack_name: str,
        cases: tuple[BenchmarkCaseResult, ...],
        summary: BenchmarkSummary,
    ) -> BenchmarkReport:
        payload = _semantic_report_payload(pack_name, cases, summary)
        return cls(
            pack_name=pack_name,
            cases=cases,
            summary=summary,
            report_digest=canonical_sha256(payload),
        )

    @model_validator(mode="after")
    def digest_is_content_bound(self) -> Self:
        payload = _semantic_report_payload(self.pack_name, self.cases, self.summary)
        if self.report_digest != canonical_sha256(payload):
            raise ValueError("benchmark report digest does not match canonical report bytes")
        return self


def _semantic_report_payload(
    pack_name: str,
    cases: tuple[BenchmarkCaseResult, ...],
    summary: BenchmarkSummary,
) -> dict[str, object]:
    """Bind semantic outcomes while retaining wall-clock timing as noncanonical telemetry."""

    semantic_cases: list[dict[str, object]] = []
    for case in cases:
        payload = case.model_dump(mode="json")
        for mode in ("baseline", "rlm"):
            observation = payload[mode]
            assert isinstance(observation, dict)
            metrics = observation["metrics"]
            assert isinstance(metrics, dict)
            metrics.pop("observed_elapsed_ms", None)
        semantic_cases.append(payload)
    return {
        "pack_name": pack_name,
        "cases": semantic_cases,
        "summary": summary.model_dump(mode="json"),
    }


def load_pack(path: Path) -> BenchmarkPack:
    return BenchmarkPack.model_validate_json(path.read_bytes(), strict=True)


def run_pack(pack: BenchmarkPack, work_root: Path) -> BenchmarkReport:
    work_root.mkdir(parents=True, exist_ok=True)
    results = tuple(_run_case(case, work_root) for case in pack.cases)
    baseline_hits = sum(item.baseline.metrics.evidence_hits for item in results)
    rlm_hits = sum(item.rlm.metrics.evidence_hits for item in results)
    broker_delta = sum(item.broker_call_delta for item in results)
    input_delta = sum(item.input_token_delta for item in results)
    safety_preserved = all(
        not observation.metrics.provider_credentials_available
        and not observation.metrics.effect_execution_available
        and not observation.metrics.final_delivery_available
        for item in results
        for observation in (item.baseline, item.rlm)
    )
    summary = BenchmarkSummary(
        case_count=len(results),
        baseline_evidence_hits=baseline_hits,
        rlm_evidence_hits=rlm_hits,
        evidence_hit_delta=rlm_hits - baseline_hits,
        broker_call_delta=broker_delta,
        input_token_delta=input_delta,
        all_safety_boundaries_preserved=safety_preserved,
        interpretation=(
            "Quality is evidence-retrieval recall against declared matches, not semantic answer "
            "correctness. Latency is locally observed with the explicit request upper bound."
        ),
    )
    return BenchmarkReport.issue(pack_name=pack.name, cases=results, summary=summary)


def _run_case(case: BenchmarkCase, work_root: Path) -> BenchmarkCaseResult:
    baseline = _run_baseline(case, work_root / f"{case.case_id}-baseline.sqlite3")
    rlm = _run_rlm(case, work_root / f"{case.case_id}-rlm.sqlite3")
    return BenchmarkCaseResult(
        case_id=case.case_id,
        baseline=baseline,
        rlm=rlm,
        evidence_hit_delta=rlm.metrics.evidence_hits - baseline.metrics.evidence_hits,
        broker_call_delta=rlm.metrics.broker_calls - baseline.metrics.broker_calls,
        input_token_delta=rlm.metrics.usage.input_tokens - baseline.metrics.usage.input_tokens,
    )


def _run_baseline(case: BenchmarkCase, database: Path) -> BenchmarkObservation:
    host = ReferenceHost(
        database,
        now_ms=lambda: BENCHMARK_NOW_MS,
        evidence_records=case.evidence_records,
        programmable_backend="plain",
    )
    try:
        principal = PrincipalRef(value="principal-benchmark")
        operation = OperationRef(value=f"operation-{case.case_id}-baseline")
        deadline = BENCHMARK_NOW_MS + 10_000
        envelope = RequestEnvelope(
            request_id=f"request-{case.case_id}-baseline",
            idempotency_key=f"idempotency-{case.case_id}-baseline",
            host=HostRef(value="reference-host"),
            principal=principal,
            lane=LaneRef(value="benchmark"),
            session=SessionRef(value="session-benchmark"),
            runtime_generation=host.runtime_generation,
            capability_digest=host.capabilities.digest,
            deadline_unix_ms=deadline,
            grants=(
                Grant(
                    grant_id="grant-model-request",
                    capability="model.request",
                    issued_to=principal,
                    expires_at_unix_ms=deadline,
                ),
            ),
            budget=Budget(
                wall_time_ms=10_000,
                model_requests=1,
                input_tokens=8_192,
                output_tokens=128,
            ),
            trace_id=f"trace-{case.case_id}-baseline",
            input_digest=canonical_sha256({"query": case.query}),
        )
        bound = host.brokers.bind(envelope, operation)
        started = time.perf_counter_ns()
        receipt = bound.model_request(
            ModelRequest(prompt=case.query), step_key="baseline-model"
        )
        elapsed_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        return BenchmarkObservation(
            mode="broker_baseline",
            answer_digest=receipt.digest,
            metrics=_metrics(
                host,
                evidence_hits=0,
                expected=len(case.expected_matches),
                elapsed_ms=elapsed_ms,
                usage=bound.usage,
                broker_calls=len(bound.traces()),
            ),
        )
    finally:
        host.close()


def _run_rlm(case: BenchmarkCase, database: Path) -> BenchmarkObservation:
    host = ReferenceHost(
        database,
        now_ms=lambda: BENCHMARK_NOW_MS,
        evidence_records=case.evidence_records,
        programmable_backend="plain",
    )
    try:
        spec = RlmJobSpec(
            query=case.query,
            strategy="evidence_synthesis",
            max_steps=2,
        )
        envelope = host.request_rlm_envelope(
            request_id=f"request-{case.case_id}-rlm",
            idempotency_key=f"idempotency-{case.case_id}-rlm",
            principal=PrincipalRef(value="principal-benchmark"),
            session=SessionRef(value="session-benchmark"),
            spec=spec,
            deadline_unix_ms=BENCHMARK_NOW_MS + 10_000,
            budget=Budget(
                wall_time_ms=10_000,
                model_requests=1,
                input_tokens=8_192,
                output_tokens=128,
            ),
        )
        started = time.perf_counter_ns()
        record = host.execute_rlm(envelope, spec)
        elapsed_ms = max(0, (time.perf_counter_ns() - started) // 1_000_000)
        result = RlmResult.model_validate_json(record.result_json or "null", strict=True)
        observed = set(result.steps[0].receipt.value.splitlines())
        evidence_hits = len(observed & set(case.expected_matches))
        return BenchmarkObservation(
            mode="evidence_rlm",
            answer_digest=result.steps[-1].receipt.digest,
            metrics=_metrics(
                host,
                evidence_hits=evidence_hits,
                expected=len(case.expected_matches),
                elapsed_ms=elapsed_ms,
                usage=result.usage,
                broker_calls=len(result.broker_trace),
                certainty=record.certainty,
                reconciliation_required=record.reconciliation_required,
            ),
        )
    finally:
        host.close()


def _metrics(
    host: ReferenceHost,
    *,
    evidence_hits: int,
    expected: int,
    elapsed_ms: int,
    usage: BrokerUsage,
    broker_calls: int,
    certainty: OutcomeCertainty = OutcomeCertainty.CERTAIN,
    reconciliation_required: bool = False,
) -> BenchmarkMetrics:
    capabilities = {item.name for item in host.capabilities.capabilities}
    return BenchmarkMetrics(
        evidence_hits=evidence_hits,
        evidence_expected=expected,
        observed_elapsed_ms=elapsed_ms,
        latency_budget_ms=10_000,
        broker_calls=broker_calls,
        usage=usage,
        certainty=certainty,
        reconciliation_required=reconciliation_required,
        provider_credentials_available="provider.credentials" in capabilities,
        effect_execution_available=hasattr(host.effects, "execute"),
        final_delivery_available="final.deliver" in capabilities,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pack",
        type=Path,
        default=Path("benchmarks/rlm-evidence-v1.json"),
    )
    parser.add_argument("--work-root", type=Path)
    args = parser.parse_args(argv)
    pack = load_pack(args.pack.resolve())
    if args.work_root is not None:
        report = run_pack(pack, args.work_root.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="aar-rlm-benchmark-") as directory:
            report = run_pack(pack, Path(directory))
    print(pretty_json_bytes(report).decode(), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
