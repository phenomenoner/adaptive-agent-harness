from __future__ import annotations

from pathlib import Path

from aar.benchmark import load_pack, run_pack

ROOT = Path(__file__).resolve().parents[1]


def test_evidence_rlm_benchmark_reports_bounded_improvement(tmp_path: Path) -> None:
    pack = load_pack(ROOT / "benchmarks" / "rlm-evidence-v1.json")
    report = run_pack(pack, tmp_path)

    assert report.summary.case_count == 3
    assert report.summary.baseline_evidence_hits == 0
    assert report.summary.rlm_evidence_hits == 4
    assert report.summary.evidence_hit_delta == 4
    assert report.summary.broker_call_delta == 3
    assert report.summary.input_token_delta > 0
    assert report.summary.all_safety_boundaries_preserved
    assert "not semantic answer correctness" in report.summary.interpretation
    assert report.report_digest.startswith("sha256:")
    for case in report.cases:
        assert case.baseline.metrics.broker_calls == 1
        assert case.rlm.metrics.broker_calls == 2
        assert case.rlm.metrics.evidence_hits == case.rlm.metrics.evidence_expected
        assert not case.rlm.metrics.reconciliation_required
        assert case.rlm.metrics.observed_elapsed_ms <= case.rlm.metrics.latency_budget_ms
