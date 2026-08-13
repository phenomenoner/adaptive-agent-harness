from __future__ import annotations

import hashlib
import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator

from aar.broker_models import (
    AccountingSource,
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteProfile,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.integrations.benchmark import AarPrimeBenchmarkEvidenceAdapter


def _provider_response(
    *,
    accounting_source: AccountingSource = "provider_reported",
    retry_ordinal: int = 0,
) -> ModelResponse:
    profile = ModelRouteProfile(
        profile_id="benchmark-luna-max-v1",
        provider_driver="owner-gateway-v1",
        provider="openai-codex",
        model="gpt-5.6-luna",
        reasoning_effort="max",
        max_output_tokens=8192,
        fallback_policy="none",
        cache_policy="run-scoped",
    )
    binding = ModelRouteBinding.issue(ModelRouteCatalog.issue((profile,)), profile)
    receipt = ModelRouteReceipt.issue(
        requested=binding,
        effective=EffectiveModelRoute(
            provider_driver=binding.provider_driver,
            provider=binding.provider,
            model=binding.model,
            reasoning_effort=binding.reasoning_effort,
        ),
        finish_reason="stop",
        provider_response_id="aar-provider-request-1",
        lookup_supported=True,
    )
    return ModelResponse(
        output_text="must not enter benchmark evidence",
        route_receipt=receipt,
        usage=ModelUsageRecord(
            accounting_source=accounting_source,
            input_tokens=21,
            output_tokens=8,
            cache_read_tokens=None,
            cache_write_tokens=None,
            reasoning_tokens=5,
            total_tokens=29,
            retry_ordinal=retry_ordinal,
            wasted=False,
        ),
    )


def test_benchmark_adapter_emits_exact_route_and_usage_fragments() -> None:
    response = _provider_response()
    adapter = AarPrimeBenchmarkEvidenceAdapter()

    route = adapter.route_fragment(response)
    usage = adapter.usage_row(
        response,
        run_id="run-aar-synthetic-001",
        request_index=1,
        purpose="primary",
        attempt=1,
        used_in_final_artifact=True,
    )

    assert route.model_dump(mode="json") == {
        "requested_provider": "openai-codex",
        "requested_model": "gpt-5.6-luna",
        "requested_effort": "max",
        "effective_provider": "openai-codex",
        "effective_model": "gpt-5.6-luna",
        "effective_effort": "max",
        "route_policy_digest": response.route_receipt.requested.profile_digest,
        "proof_status": "verified",
        "cache_scope": "run-scoped",
    }
    assert usage.model_dump(mode="json") == {
        "schema_version": "aar-prime.usage-row.v1",
        "run_id": "run-aar-synthetic-001",
        "arm": "aar",
        "request_index": 1,
        "purpose": "primary",
        "provider": "openai-codex",
        "model": "gpt-5.6-luna",
        "reasoning_effort_requested": "max",
        "reasoning_effort_effective": "max",
        "input_tokens": 21,
        "output_tokens": 8,
        "total_tokens": 29,
        "reasoning_tokens": 5,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "cost_usd": None,
        "attempt": 1,
        "used_in_final_artifact": True,
        "started_at": None,
        "completed_at": None,
        "receipt_digest": response.route_receipt.receipt_digest,
    }
    retained = (str(route.model_dump()) + str(usage.model_dump())).encode()
    assert b"must not enter benchmark evidence" not in retained


def test_benchmark_fragments_validate_against_pinned_external_contracts() -> None:
    contract_root = files("aar.integrations").joinpath("contracts")
    manifest = json.loads(contract_root.joinpath("manifest.json").read_text())
    assert manifest["source_project"] == "adaptive-agent-harness"
    assert manifest["published_with"] == "0.3.0a2"
    assert "imported_at" not in manifest
    assert all(
        item["source_path"].startswith("src/aar/integrations/contracts/")
        and item["schema_id"].startswith(
            "https://github.com/phenomenoner/adaptive-agent-harness/"
        )
        for item in manifest["contracts"]
    )
    expected = {
        item["path"]: item["sha256"] for item in manifest["contracts"]
    }
    assert expected == {
        "run-manifest.schema.json": (
            "eea2641dd1679f10932e6a420b175ac895bdd54026b5dbe15a1de4db5d113beb"
        ),
        "usage-ledger-row.schema.json": (
            "12b27dfad6fccabdd5d997354a57341cff6ba72cd8ca7064fe61927f642b139c"
        ),
    }
    loaded: dict[str, dict] = {}
    for path, digest in expected.items():
        content = contract_root.joinpath(path).read_bytes()
        assert hashlib.sha256(content).hexdigest() == digest
        loaded[path] = json.loads(content)

    response = _provider_response()
    adapter = AarPrimeBenchmarkEvidenceAdapter()
    route = adapter.route_fragment(response).model_dump(mode="json")
    usage = adapter.usage_row(
        response,
        run_id="run-aar-synthetic-001",
        request_index=1,
        purpose="primary",
        attempt=1,
        used_in_final_artifact=True,
    ).model_dump(mode="json")
    Draft202012Validator(
        loaded["run-manifest.schema.json"]["properties"]["route"]
    ).validate(route)
    Draft202012Validator(loaded["usage-ledger-row.schema.json"]).validate(usage)


def test_benchmark_adapter_rejects_non_provider_usage() -> None:
    response = _provider_response(accounting_source="reference")

    with pytest.raises(ValueError, match="provider-reported"):
        AarPrimeBenchmarkEvidenceAdapter().usage_row(
            response,
            run_id="run-aar-synthetic-001",
            request_index=1,
            purpose="primary",
            attempt=1,
            used_in_final_artifact=True,
        )


def test_benchmark_adapter_rejects_internal_retry_without_attempt_usage_rows() -> None:
    response = _provider_response(retry_ordinal=1)
    adapter = AarPrimeBenchmarkEvidenceAdapter()

    with pytest.raises(ValueError, match="internal retry"):
        adapter.route_fragment(response)
    with pytest.raises(ValueError, match="internal retry"):
        adapter.usage_row(
            response,
            run_id="run-aar-synthetic-001",
            request_index=1,
            purpose="retry",
            attempt=2,
            used_in_final_artifact=False,
        )
