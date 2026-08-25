from __future__ import annotations

import json
from pathlib import Path

import pytest

from aar.provider_ready_package_factory import (
    PACKAGE_FACTORY_DECLARATIONS,
    PACKAGE_FACTORY_WHEEL_MEMBER,
    PackageFactoryBindingError,
    factory_entries_from_member_digests,
)

ROOT = Path(__file__).resolve().parents[1]
DIGEST = "sha256:" + "1" * 64


def test_package_factory_declarations_match_frozen_broker_catalog() -> None:
    catalog = json.loads((ROOT / "schemas" / "aar-broker-catalog-v2.json").read_bytes())
    observed = tuple(
        (
            declaration.method,
            declaration.contract_id,
            declaration.request_schema_digest,
            declaration.response_schema_digest,
        )
        for declaration in PACKAGE_FACTORY_DECLARATIONS
    )
    expected = tuple(
        (
            contract["method"],
            contract["contract_id"],
            contract["request_schema_digest"],
            contract["response_schema_digest"],
        )
        for contract in catalog["contracts"]
    )

    assert observed == expected
    assert len(PACKAGE_FACTORY_DECLARATIONS) == 6
    assert tuple(item.factory_id for item in PACKAGE_FACTORY_DECLARATIONS) == (
        "aar.factory.model-request.v1",
        "aar.factory.subagent-submit.v1",
        "aar.factory.subagent-result.v1",
        "aar.factory.evidence-query.v1",
        "aar.factory.artifact-put.v1",
        "aar.factory.effect-propose.v1",
    )
    assert {item.wheel_member for item in PACKAGE_FACTORY_DECLARATIONS} == {
        "aar/runtime/brokers.py"
    }
    artifact = next(item for item in PACKAGE_FACTORY_DECLARATIONS if item.method == "artifact.put")
    assert artifact.contract_id == "aar.artifact-stage.v1"


def test_receipt_entries_are_method_scoped_but_share_exact_member_digest() -> None:
    entries = factory_entries_from_member_digests({PACKAGE_FACTORY_WHEEL_MEMBER: DIGEST})

    assert len(entries) == 6
    assert tuple(entry.factory_id for entry in entries) == tuple(
        sorted(item.factory_id for item in PACKAGE_FACTORY_DECLARATIONS)
    )
    assert {entry.wheel_member for entry in entries} == {PACKAGE_FACTORY_WHEEL_MEMBER}
    assert {entry.implementation_digest for entry in entries} == {DIGEST}


def test_receipt_projection_rejects_absent_package_member() -> None:
    with pytest.raises(PackageFactoryBindingError, match="wheel member is absent"):
        factory_entries_from_member_digests({})
