from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aar.asset_models import AdaptiveAssetBundle
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.contract import (
    ahc_continuity_fixture,
    ahc_integration_fixture,
    forbidden_imports,
    generate_contract,
    schema_bundle,
    validate_fixtures,
    verify_contract,
)
from aar.schemas import CapabilitySet, RequestEnvelope
from aar.versions import FROZEN_COMPATIBILITY_PACKAGE_VERSION, PACKAGE_VERSION

ROOT = Path(__file__).resolve().parents[1]


def test_checked_in_contract_verifies() -> None:
    assert verify_contract(ROOT) == []


def test_generation_is_byte_deterministic(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = generate_contract(first_root)
    second = generate_contract(second_root)
    assert [path.relative_to(first_root) for path in first] == [
        path.relative_to(second_root) for path in second
    ]
    for first_path, second_path in zip(first, second, strict=True):
        assert first_path.read_bytes() == second_path.read_bytes()


def test_manifest_covers_invalid_contract_dimensions() -> None:
    manifest = json.loads((ROOT / "tests" / "fixtures" / "manifest.json").read_text())
    invalid_paths = {entry["path"] for entry in manifest["fixtures"] if not entry["valid"]}
    expected_fragments = {
        "identity",
        "version",
        "digest",
        "generation",
        "revision",
        "grant",
        "budget",
        "extra-field",
    }
    assert all(any(fragment in path for path in invalid_paths) for fragment in expected_fragments)
    assert validate_fixtures(ROOT) == []


def test_capability_digest_is_content_bound() -> None:
    document = json.loads(
        (ROOT / "tests" / "fixtures" / "valid" / "capability-set.json").read_text()
    )
    parsed = CapabilitySet.model_validate_json(canonical_json_bytes(document), strict=True)
    assert parsed.digest == canonical_sha256(
        {
            "schema_version": parsed.schema_version,
            "capabilities": [item.model_dump(mode="json") for item in parsed.capabilities],
        }
    )
    document["capabilities"][0]["name"] = "artifact.write"
    with pytest.raises(ValidationError, match="capability digest"):
        CapabilitySet.model_validate_json(canonical_json_bytes(document), strict=True)


def test_request_envelope_rejects_workspace_context_without_workspace() -> None:
    document = json.loads(
        (ROOT / "tests" / "fixtures" / "valid" / "request-envelope.json").read_text()
    )
    document["workspace"] = None
    with pytest.raises(ValidationError, match="require a workspace reference"):
        RequestEnvelope.model_validate_json(canonical_json_bytes(document), strict=True)


def test_canonical_json_rejects_float_and_sorts_keys() -> None:
    assert canonical_json_bytes({"z": 1, "a": [True, None]}) == b'{"a":[true,null],"z":1}'
    with pytest.raises(TypeError, match="floating-point"):
        canonical_json_bytes({"value": 1.5})


def test_schema_bundle_digest_is_self_consistent() -> None:
    bundle = schema_bundle()
    digest = bundle.pop("bundle_digest")
    assert PACKAGE_VERSION == "0.6.0a1"
    assert bundle["package_version"] == FROZEN_COMPATIBILITY_PACKAGE_VERSION == "0.5.0a0"
    assert digest == canonical_sha256(bundle)


def test_ahc_fixture_reuses_transport_neutral_contracts() -> None:
    document = json.loads((ROOT / "integration" / "ahc" / "rlm-contract-v1.json").read_text())
    assert document == ahc_integration_fixture()
    request = RequestEnvelope.model_validate_json(
        canonical_json_bytes(document["request_envelope"]), strict=True
    )
    assert request.capability_digest == document["capabilities"]["digest"]
    assert document["authority"] == {
        "effect_execution_available": False,
        "final_delivery_available": False,
        "outer_operation_is_authoritative": True,
        "provider_credentials_available": False,
        "retained_child_requires_explicit_result": True,
    }


def test_ahc_continuity_fixture_is_optional_and_preserves_authority() -> None:
    document = json.loads(
        (ROOT / "integration" / "ahc" / "continuity-contract-v1.json").read_text()
    )
    assert document == ahc_continuity_fixture()
    assert document["profile"] == "operation.continuity.v1"
    assert document["authority"] == {
        "aar_owns_ahc_task_state": False,
        "aar_owns_delivery": False,
        "operation_id_is_bearer_authority": False,
        "shared_ig_gate_added": False,
    }


def test_non_ahc_asset_fixture_has_no_ahc_only_required_fields() -> None:
    path = ROOT / "tests" / "fixtures" / "valid" / "adaptive-asset-bundle.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    bundle = AdaptiveAssetBundle.model_validate_json(canonical_json_bytes(document), strict=True)
    assert len(bundle.documents) == 2
    assert "ahc" not in canonical_json_bytes(document).decode().lower()


def test_core_has_no_host_specific_imports() -> None:
    assert forbidden_imports(ROOT / "src" / "aar") == []


def test_every_mutating_mcp_handler_reaches_current_authority_admission() -> None:
    source = (ROOT / "src" / "aar" / "mcp" / "server.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    mutating: dict[str, set[str]] = {}
    for node in functions.values():
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or ast.unparse(decorator.func) != "server.tool":
                continue
            keywords = {item.arg: item.value for item in decorator.keywords}
            annotation = keywords.get("annotations")
            if annotation is None or not ast.unparse(annotation).startswith("MUTATING"):
                continue
            name = keywords["name"]
            assert isinstance(name, ast.Constant) and isinstance(name.value, str)
            mutating[name.value] = {
                symbol.id for symbol in ast.walk(node) if isinstance(symbol, ast.Name)
            }

    caller_tools = {
        "aar_broker_work_claim",
        "aar_broker_work_mark_send_started",
        "aar_broker_work_cancel_before_send",
        "aar_broker_work_commit",
        "aar_broker_work_reconcile",
    }
    caller_methods = {
        "aar_broker_work_cancel_before_send": "cancel_before_send",
        "aar_broker_work_claim": "claim",
        "aar_broker_work_commit": "commit",
        "aar_broker_work_mark_send_started": "mark_send_started",
        "aar_broker_work_reconcile": "reconcile",
    }
    caller_capabilities = {
        "cancel_before_send": "broker.caller.cancel",
        "claim": "broker.caller.claim",
        "commit": "broker.caller.commit",
        "mark_send_started": "broker.caller.send",
        "reconcile": "broker.caller.reconcile",
    }
    authority_assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name)
            and target.id == "CALLER_WORK_METHOD_CAPABILITIES"
            for target in node.targets
        )
    )
    assert ast.literal_eval(authority_assignment.value) == caller_capabilities
    for tool_name, method_name in caller_methods.items():
        calls = [
            call
            for call in ast.walk(functions[tool_name])
            if isinstance(call, ast.Call)
            and call.args
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == "_run_caller_work_command"
        ]
        assert len(calls) == 1
        assert ast.literal_eval(calls[0].args[3]) == method_name
    expected = {
        "aar_workspace_create",
        "aar_workspace_execute",
        "aar_program_workspace_create",
        "aar_program_workspace_execute",
        "aar_program_workspace_interrupt",
        "aar_program_workspace_checkpoint",
        "aar_program_workspace_restore",
        "aar_program_workspace_close",
        "aar_asset_import",
        "aar_rlm_execute",
        "aar_operation_cancel",
        "aar_operation_reconcile",
        "aar_rlm_workbench_execute",
        *caller_tools,
    }
    assert set(mutating) == expected
    direct_admission = {"_envelope", "_rlm_envelope", "_validate_mutation_context"}
    for name, calls in mutating.items():
        if name in caller_tools:
            assert "_run_caller_work_command" in calls
        else:
            assert direct_admission & calls, name

    caller_helper_calls = {
        ast.unparse(call.func)
        for call in ast.walk(functions["_run_caller_work_command"])
        if isinstance(call, ast.Call)
    }
    assert "_assert_workbench_operation_binding" in caller_helper_calls
    assert "_assert_current_claim_adapter" in caller_helper_calls
    caller_helper_source = ast.unparse(functions["_run_caller_work_command"])
    assert caller_helper_source.index("_assert_current_claim_adapter") < caller_helper_source.index(
        "getattr"
    )
    claim_adapter_source = ast.unparse(functions["_assert_current_claim_adapter"])
    for required_guard in (
        "row['contract_id'] != request['contract_id']",
        "not row['configured']",
        "row['reference_only']",
        "row['backend_kind'] != 'caller_driver'",
        "row['adapter_id'] != document['adapter_id']",
        "row['adapter_generation'] != document['adapter_generation']",
        "row['adapter_generation'] != host.runtime_generation",
    ):
        assert required_guard in claim_adapter_source
    binding_source = ast.unparse(functions["_assert_workbench_operation_binding"])
    assert "host.provider_ready_startup.resolve_session_grants" in binding_source
