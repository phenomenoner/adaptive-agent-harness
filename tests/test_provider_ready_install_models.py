from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_install_models import (
    INSTALL_CANDIDATE_RECEIPT_SCHEMA_VERSION,
    InstallCandidateFactoryEntry,
    InstallCandidateReceipt,
)
from aar.provider_ready_models import ProviderReadyCandidate

DIGEST = canonical_sha256({"fixture": "install-candidate-receipt"})


def _candidate() -> ProviderReadyCandidate:
    return ProviderReadyCandidate(
        package_version="0.6.0a0",
        source_commit="0" * 40,
        wheel_digest=DIGEST,
        contract_manifest_digest=DIGEST,
        skill_digest=DIGEST,
    )


def _entry() -> InstallCandidateFactoryEntry:
    return InstallCandidateFactoryEntry(
        factory_id="capability_registry",
        wheel_member="aar/capability_registry.py",
        implementation_digest=DIGEST,
    )


def _receipt() -> InstallCandidateReceipt:
    return InstallCandidateReceipt.issue(
        candidate=_candidate(),
        wheel_size_bytes=1,
        wheel_digest=DIGEST,
        contract_manifest_digest=DIGEST,
        skill_digest=DIGEST,
        factory_entries=(_entry(),),
    )


def test_receipt_digest_has_an_independent_oracle() -> None:
    receipt = _receipt()
    payload = receipt.model_dump(mode="json")
    observed = payload.pop("receipt_digest")
    assert observed == canonical_sha256(payload)


def test_receipt_repeated_candidate_bindings_and_digest_fail_independently() -> None:
    payload = _receipt().model_dump(mode="python")
    payload["wheel_digest"] = canonical_sha256({"different": "wheel"})
    payload["receipt_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "receipt_digest"}
    )
    with pytest.raises(ValidationError, match=r"must equal candidate\.wheel_digest"):
        InstallCandidateReceipt.model_validate(payload, strict=True)

    payload = _receipt().model_dump(mode="python")
    payload["receipt_digest"] = canonical_sha256({"stale": True})
    with pytest.raises(ValidationError, match="receipt digest does not match"):
        InstallCandidateReceipt.model_validate(payload, strict=True)


def test_checked_schema_is_exact_current_model_projection() -> None:
    repo = Path(__file__).parents[1]
    checked = json.loads(
        (repo / "schemas/aar-install-candidate-receipt-v1.schema.json").read_text()
    )
    projected = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": INSTALL_CANDIDATE_RECEIPT_SCHEMA_VERSION,
        **InstallCandidateReceipt.model_json_schema(),
    }
    assert checked == projected
    assert checked["properties"]["factory_entries"]["minItems"] == 1
    assert checked["properties"]["factory_entries"]["maxItems"] == 6
