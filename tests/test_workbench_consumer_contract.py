"""Typed consumers bind their installed projections, not JSON schema alone."""
from __future__ import annotations

import json
from copy import deepcopy

import pytest
from mcp.types import CallToolResult
from pydantic import ValidationError

import aar.rlm_workbench_models as models
from aar.canonical import canonical_sha256


def parse(payload: dict, use_json: bool):
    if use_json:
        return models.RlmWorkbenchCapability.model_validate_json(json.dumps(payload), strict=True)
    return models.RlmWorkbenchCapability.model_validate(payload, strict=True)


@pytest.mark.parametrize('use_json', [False, True], ids=['python-object', 'json-wire'])
def test_builder_capability_survives_mcp_envelope_and_strict_consumer(use_json: bool) -> None:
    # An empty backend map is a truthful unconfigured contract, not readiness.
    payload = models.build_workbench_capability({}).model_dump(mode='json')
    envelope = CallToolResult.model_validate(
        {'content': [], 'structuredContent': payload, 'isError': False}, strict=True
    )
    assert envelope.is_error is False
    typed = parse(envelope.structured_content, use_json)
    assert typed.model_dump(mode='json') == payload
    assert canonical_sha256(typed.model_dump(mode='json')) == canonical_sha256(payload)


@pytest.mark.parametrize('use_json', [False, True], ids=['python-object', 'json-wire'])
def test_unchanged_model_schema_does_not_admit_foreign_installed_projection(
    monkeypatch: pytest.MonkeyPatch, use_json: bool,
) -> None:
    producer_payload = models.build_workbench_capability({}).model_dump(mode='json')
    unchanged_schema = deepcopy(models.RlmWorkbenchCapability.model_json_schema())
    original = models._load_projection
    foreign_digest = canonical_sha256({'test_projection': 'different-consumer-installation'})
    assert foreign_digest != producer_payload['tool_surface_digest']

    def consumer_projection(name: str) -> dict:
        value = original(name)
        if name == 'aar-mcp-tools-v9-combined.json':
            value = deepcopy(value)
            value['tool_surface_digest'] = foreign_digest
        return value

    # Substitute only the consumer's local packaged-projection reader. Runtime
    # validation, the producer body and its JSON schema remain unmodified.
    monkeypatch.setattr(models, '_load_projection', consumer_projection)
    assert models.RlmWorkbenchCapability.model_json_schema() == unchanged_schema
    with pytest.raises(ValidationError, match='capability tool surface digest mismatch'):
        parse(producer_payload, use_json)
    matched_payload = models.build_workbench_capability({}).model_dump(mode='json')
    assert matched_payload['tool_surface_digest'] == foreign_digest
    assert parse(matched_payload, use_json).model_dump(mode='json') == matched_payload


@pytest.mark.parametrize('use_json', [False, True], ids=['python-object', 'json-wire'])
def test_matching_projection_does_not_relax_capability_shape(use_json: bool) -> None:
    payload = models.build_workbench_capability({}).model_dump(mode='json')
    del payload['planner_directive_schema_digest']
    with pytest.raises(ValidationError):
        parse(payload, use_json)
