from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SDD = ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2"


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def test_implementation_verified_rejects_digest_only_matrix_claim() -> None:
    from aar.acceptance_evidence import AcceptanceClaimError, validate_acceptance_claim

    matrix_path = SDD / "acceptance-matrix.json"
    fault_path = SDD / "fault-matrix.json"
    package_path = SDD / "sdd-package-manifest.json"
    digest_only_claim = {
        "schema_version": "aar.acceptance-results.v1",
        "candidate_id": "candidate-digest-only",
        "claim": "implementation_verified",
        "source_manifest_digest": "sha256:" + "1" * 64,
        "acceptance_matrix_digest": digest_file(matrix_path),
        "fault_matrix_digest": digest_file(fault_path),
        "sdd_package_digest": json.loads(package_path.read_text(encoding="utf-8"))[
            "package_digest"
        ],
        "row_results": [],
        "fault_results": [],
        "evidence": [],
    }

    with pytest.raises(AcceptanceClaimError, match=r"row|fault|evidence"):
        validate_acceptance_claim(
            acceptance_matrix=json.loads(matrix_path.read_text(encoding="utf-8")),
            fault_matrix=json.loads(fault_path.read_text(encoding="utf-8")),
            claim=digest_only_claim,
        )
