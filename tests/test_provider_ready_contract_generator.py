from __future__ import annotations

import hashlib
import importlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_contract import (
    PROVIDER_READY_SCHEMA_MODELS,
    ProviderReadyContractError,
    load_provider_ready_json_bytes,
    validate_provider_ready_document_bytes,
)
from aar.provider_ready_contract_generator import (
    generate_provider_ready_contract,
    provider_ready_asset_bytes,
    provider_ready_fixture_documents,
    provider_ready_schema_bundle,
    validate_provider_ready_contract,
    verify_provider_ready_assets,
)

# Exact fixture paths, digests, and normalized messages are intentionally long.
# ruff: noqa: E501

REPO_ROOT = Path(__file__).parents[1]
SPEC_PATH = REPO_ROOT / "docs/implementation/aar-provider-ready-workbench-v1/work-packages/A1c-GF-fixture-spec.json"
SPEC_RAW_SHA256 = "a49428b890f0c8e612fe677b24ca4fd3decc83ad71674a699eddc8f7403605a8"
SPEC_DIGEST = "sha256:1b45466190c7b54d493ef3d68857633f5b8f2643cd827a602faa976aafc85379"
LEGACY_HASHES = {
    "schemas/aar-mcp-tools-v7.json": "6ebf848eee74795771af23dfaaeb70fd5030cb88878327f76ffc10d1c4639ab8",
    "schemas/aar-mcp-tools-v8.json": "41e97f76f8f371ad2c12d1c4bcba0131491a903cfdf6760430bdeffe4ab2e82f",
    "schemas/aar-mcp-tools-v8-combined.json": "36b41abbcab2e5b3a60dff6a1de85e7a3168cc01135d42c5a88b416abfbca424",
    "schemas/aar-schemas-v1.json": "02b894c6a1396c2a3f3d3238e44a3f26a5f1e3126bc3e3e4bac5946cd13c33ca",
    "tests/fixtures/manifest.json": "69214e21f14be8800e386c7a8f4800311c3fabcc12015a843031b8b3cd3324b0",
}
FROZEN_INSTALLER_HASHES = {
    "schemas/aar-broker-catalog-v2.json": "88099616e61f41c7d72d5e1c81fba8ebfe29995467fd583acc9da0bb15aeadbc",
    "schemas/aar-rlm-workbench-v1.schema.json": "0d52527f9019cae724459e3eb36a0165b91822771ae64f815f1c2fd18d3054ef",
    "schemas/aar-caller-work-v1.schema.json": "76deb95fb89081c7ec90734821689e14a893040696af6727725b059676c2b7e3",
    "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql": "8e7080b319aadb4eb98b5e3b9e12efe8c189c0bd12a82dc8ba1eea7c7bfe29b7",
}


def _spec() -> dict[str, Any]:
    raw = SPEC_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SPEC_RAW_SHA256
    parsed = json.loads(raw)
    assert parsed["specDigest"] == SPEC_DIGEST
    assert parsed["specDigest"] == canonical_sha256(
        {key: value for key, value in parsed.items() if key != "specDigest"}
    )
    return parsed


def _pointer_get(document: Any, pointer: str) -> Any:
    current = document
    if pointer:
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _expected_entry(record: dict[str, Any], *, kind: str) -> dict[str, Any]:
    error = record.get("normalizedError")
    return {
        "path": record["path"],
        "kind": kind,
        "schema_id": record.get("schemaId"),
        "project_expected": record["projectExpected"],
        "independent_expected": record["independentExpected"],
        "graph_expected": record["graphExpected"],
        "composite_expected": record["compositeExpected"],
        "graph_scenario": record.get("graphScenario"),
        "graph_dependencies": record.get("graphDependencies", []),
        "graph_replacements": record.get("graphReplacements", {}),
        "expected_error_engine": error["engine"] if error else None,
        "expected_error_code": error["code"] if error else None,
        "expected_error_location": error["location"] if error else None,
        "expected_error_fragment": error["fragment"] if error else None,
        "document_digest": record.get("documentDigest"),
        "raw_bytes_sha256": record["rawBytesSha256"],
    }


def _project_result(raw: bytes) -> tuple[str, dict[str, Any] | None]:
    try:
        validate_provider_ready_document_bytes(raw)
    except ProviderReadyContractError as error:
        return "fail", {
            "engine": "loader", "code": error.code, "location": None,
            "fragment": str(error), "targetIndex": None,
        }
    except ValidationError as error:
        target = error.errors()[0]
        return "fail", {
            "engine": "project", "code": target["type"], "location": list(target["loc"]),
            "fragment": target["msg"], "targetIndex": 0,
        }
    return "pass", None


def _independent_result(record: dict[str, Any], payload: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    schema_id = record.get("schemaId")
    if schema_id not in PROVIDER_READY_SCHEMA_MODELS:
        schema_id = record["basePath"] and next(
            item["schemaId"] for item in _spec()["validFixtures"] if item["path"] == record["basePath"]
        )
    schema = PROVIDER_READY_SCHEMA_MODELS[schema_id].model_json_schema(mode="validation")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(payload),
        key=lambda item: (list(item.absolute_path), item.validator or "", item.message),
    )
    if not errors:
        return "pass", None
    error = errors[0]
    return "fail", {
        "engine": "independent", "code": error.validator,
        "location": list(error.absolute_path), "fragment": error.message, "targetIndex": 0,
    }


def _graph_errors(
    scenario: dict[str, Any], documents: dict[str, dict[str, Any]], replacements: dict[str, str] | None = None,
    join_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    roles = dict(scenario["roles"])
    for role, path in (replacements or {}).items():
        roles[role] = path

    def role_document(role: str) -> Any:
        spec = roles[role]
        if isinstance(spec, str):
            return documents[spec]
        return _pointer_get(documents[spec["path"]], spec.get("pointer", ""))

    errors = []
    for join in scenario["joins"]:
        if join_names is not None and join["name"] not in join_names:
            continue
        left = _pointer_get(role_document(join["leftRole"]), join["leftPointer"])
        right = _pointer_get(role_document(join["rightRole"]), join["rightPointer"])
        if left != right:
            errors.append({
                "engine": "graph", "code": join["code"],
                "location": ["$graph", scenario["name"], join["name"]],
                "fragment": join["fragment"], "targetIndex": 0,
            })
    return errors


def test_module_surface_and_exact_spec_identity() -> None:
    module = importlib.import_module("aar.provider_ready_contract_generator")
    assert {
        "provider_ready_schema_bundle", "provider_ready_fixture_documents", "provider_ready_asset_bytes",
        "generate_provider_ready_contract", "validate_provider_ready_contract", "verify_provider_ready_assets",
        "validate_provider_ready_fixture_graph",
    } <= set(vars(module))
    assert _spec()["constants"]["schemaIds"] == sorted(PROVIDER_READY_SCHEMA_MODELS)
    source = inspect.getsource(module)
    assert "build_a1c_gf_fixture_spec" not in source
    assert "A1c-GF-fixture-spec.json" not in source


def test_exact_schema_bundle_self_digests_and_independent_schemas() -> None:
    spec = _spec()
    bundle = provider_ready_schema_bundle()
    assert bundle["schema_version"] == "aar.provider-ready-schema-bundle.v1"
    assert list(bundle["schemas"]) == spec["constants"]["schemaIds"]
    assert list(bundle["schema_digests"]) == spec["constants"]["schemaIds"]
    assert len(bundle["schemas"]) == 14
    assert "IssuedWorkbenchGrant" not in bundle["schemas"]
    for schema_id, schema in bundle["schemas"].items():
        Draft202012Validator.check_schema(schema)
        assert bundle["schema_digests"][schema_id] == canonical_sha256(schema)
        assert schema == PROVIDER_READY_SCHEMA_MODELS[schema_id].model_json_schema(mode="validation")
    core = {key: bundle[key] for key in ("schema_version", "schemas", "schema_digests")}
    assert bundle["bundle_digest"] == canonical_sha256(core)

    intent = bundle["schemas"]["aar.host-activation-intent.v1"]
    assert intent["properties"]["adapters"]["minItems"] == 1
    assert intent["properties"]["adapters"]["maxItems"] == 6
    assert intent["properties"]["adapters"]["items"]["$ref"].startswith("#/$defs/")
    plan = bundle["schemas"]["aar.cutover-plan.v1"]
    actions = plan["properties"]["allowed_recovery_actions"]
    assert actions["minItems"] == 1 and actions["maxItems"] == 4
    assert set(actions["items"]["enum"]) == {"abort", "apply", "reconcile", "status"}
    grant_set = bundle["schemas"]["aar.workbench-grant-set.v1"]
    principals = grant_set["properties"]["principal_ids"]
    assert principals["minItems"] == 1 and principals["maxItems"] == 64
    assert "items" in principals
    def refs(value: Any) -> set[str]:
        if isinstance(value, dict):
            result = {value["$ref"]} if "$ref" in value else set()
            for child in value.values():
                result |= refs(child)
            return result
        if isinstance(value, list):
            result: set[str] = set()
            for child in value:
                result |= refs(child)
            return result
        return set()
    assert any(ref.startswith("#/$defs/") for ref in refs(grant_set))


def test_fixture_documents_are_exact_normative_projection_and_counts() -> None:
    spec = _spec()
    documents = provider_ready_fixture_documents()
    expected = {record["path"]: record["document"] for record in spec["validFixtures"] + spec["invalidFixtures"]}
    assert len(documents) == 56
    assert documents == expected
    assert {
        document["schema_version"]
        for document in documents.values()
        if document.get("schema_version") in spec["constants"]["schemaIds"]
    } == set(spec["constants"]["schemaIds"])


def test_asset_inventory_manifest_records_and_raw_hex_are_exact() -> None:
    spec = _spec()
    assets = provider_ready_asset_bytes()
    manifest = json.loads(assets["tests/fixtures/provider-ready/manifest.json"])
    assert len(spec["validFixtures"]) == 25
    assert len(spec["invalidFixtures"]) == 31
    assert len(spec["loaderFixtures"]) == 8
    assert len(manifest["fixtures"]) == 64
    assert len(assets) == 66
    assert [entry["path"] for entry in manifest["fixtures"]] == sorted(entry["path"] for entry in manifest["fixtures"])
    required_fields = {
        "path", "kind", "schema_id", "project_expected", "independent_expected", "graph_expected",
        "composite_expected", "graph_scenario", "graph_dependencies", "graph_replacements",
        "expected_error_engine", "expected_error_code", "expected_error_location", "expected_error_fragment",
        "document_digest", "raw_bytes_sha256",
    }
    assert all(set(entry) == required_fields for entry in manifest["fixtures"])
    for record in spec["validFixtures"]:
        actual = next(entry for entry in manifest["fixtures"] if entry["path"] == record["path"])
        assert actual == _expected_entry(record, kind="valid")
    for record in spec["invalidFixtures"]:
        kind = "digest_invalid" if record["kind"] == "stale-root" else "semantic_invalid"
        actual = next(entry for entry in manifest["fixtures"] if entry["path"] == record["path"])
        assert actual == _expected_entry(record, kind=kind)
    for record in spec["loaderFixtures"]:
        actual = next(entry for entry in manifest["fixtures"] if entry["path"] == record["path"])
        expected = {
            "path": record["path"], "kind": "loader_invalid", "schema_id": None,
            "project_expected": "fail", "independent_expected": "not_applicable",
            "graph_expected": "not_applicable", "composite_expected": "fail", "graph_scenario": None,
            "graph_dependencies": [], "graph_replacements": {}, "expected_error_engine": "loader",
            "expected_error_code": record["normalizedError"]["code"], "expected_error_location": None,
            "expected_error_fragment": record["normalizedError"]["fragment"], "document_digest": None,
            "raw_bytes_sha256": record["rawBytesSha256"],
        }
        assert actual == expected
        raw = assets[f"tests/fixtures/provider-ready/{record['path']}"]
        assert raw.hex() == record["rawHex"]
        assert hashlib.sha256(raw).hexdigest() == record["rawBytesSha256"]
    manifest_core = {key: manifest[key] for key in ("schema_version", "schema_bundle_digest", "fixtures")}
    assert manifest["fixture_set_digest"] == canonical_sha256(manifest_core)


def test_dual_validator_matrix_and_exact_normalized_errors() -> None:
    spec = _spec()
    assets = provider_ready_asset_bytes()
    schema_bundle = provider_ready_schema_bundle()
    documents = provider_ready_fixture_documents()
    for record in spec["validFixtures"] + spec["invalidFixtures"]:
        raw = assets[f"tests/fixtures/provider-ready/{record['path']}"]
        payload = load_provider_ready_json_bytes(raw)
        assert canonical_sha256(payload) == record["documentDigest"]
        project, project_error = _project_result(raw)
        assert project == record["projectExpected"]
        expected_error = record.get("normalizedError")
        if record["projectExpected"] == "fail" and record["graphExpected"] != "fail":
            assert project_error is not None and expected_error is not None
            assert project_error["engine"] == expected_error["engine"]
            assert project_error["code"] == expected_error["code"]
            assert project_error["location"] == expected_error["location"]
            assert expected_error["fragment"] in project_error["fragment"]
        model_schema_id = record["schemaId"]
        model_type = PROVIDER_READY_SCHEMA_MODELS.get(model_schema_id)
        if model_type is None:
            base = next(item for item in spec["validFixtures"] if item["path"] == record["basePath"])
            model_type = PROVIDER_READY_SCHEMA_MODELS[base["schemaId"]]
            model_schema_id = base["schemaId"]
        independent_errors = sorted(
            Draft202012Validator(model_type.model_json_schema(mode="validation")).iter_errors(payload),
            key=lambda item: (list(item.absolute_path), item.validator or "", item.message),
        )
        independent = "fail" if independent_errors else "pass"
        assert independent == record["independentExpected"]
        if independent_errors:
            assert model_schema_id in schema_bundle["schemas"]
    for record in spec["loaderFixtures"]:
        raw = assets[f"tests/fixtures/provider-ready/{record['path']}"]
        with pytest.raises(ProviderReadyContractError) as caught:
            load_provider_ready_json_bytes(raw)
        assert caught.value.code == record["normalizedError"]["code"]

    for scenario in spec["graphScenarios"]:
        assert _graph_errors(scenario, {path: documents[path] for path in documents if path.startswith("valid/")}) == []
    for record in spec["invalidFixtures"]:
        if record.get("graphScenario") is None:
            continue
        all_documents = dict(documents)
        all_documents[record["path"]] = record["document"]
        scenario = next(item for item in spec["graphScenarios"] if item["name"] == record["graphScenario"])
        errors = _graph_errors(scenario, all_documents, record["graphReplacements"], set(record["graphJoinNames"]))
        assert record["graphExpected"] == ("fail" if errors else "pass")
        assert len(errors) == 1
        assert errors[0] == record["normalizedError"]


def test_generation_is_byte_identical_and_verifiers_are_zero_write(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    generate_provider_ready_contract(first)
    generate_provider_ready_contract(second)
    first_bytes = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    second_bytes = {path.relative_to(second).as_posix(): path.read_bytes() for path in second.rglob("*") if path.is_file()}
    assert first_bytes == second_bytes
    before = dict(first_bytes)
    assert verify_provider_ready_assets(first) is True
    assert validate_provider_ready_contract(first) is True
    after = {path.relative_to(first).as_posix(): path.read_bytes() for path in first.rglob("*") if path.is_file()}
    assert after == before


def test_preflight_rejects_existing_partial_case_alias_and_symlink_without_writes(tmp_path: Path) -> None:
    existing = tmp_path / "existing"
    existing.mkdir()
    schema = existing / "schemas/aar-provider-ready-schemas-v1.json"
    schema.parent.mkdir(parents=True)
    schema.write_bytes(b"preexisting")
    with pytest.raises(ValueError):
        generate_provider_ready_contract(existing)
    assert schema.read_bytes() == b"preexisting"

    partial = tmp_path / "partial"
    (partial / "tests/fixtures/provider-ready/valid").mkdir(parents=True)
    (partial / "tests/fixtures/provider-ready/valid/extra.json").write_bytes(b"keep")
    with pytest.raises(ValueError):
        generate_provider_ready_contract(partial)
    assert (partial / "tests/fixtures/provider-ready/valid/extra.json").read_bytes() == b"keep"

    alias = tmp_path / "alias"
    (alias / "Schemas").mkdir(parents=True)
    with pytest.raises(ValueError):
        generate_provider_ready_contract(alias)
    assert list(alias.iterdir()) == [alias / "Schemas"]

    symlink = tmp_path / "symlink"
    target = tmp_path / "outside"
    target.mkdir()
    symlink.mkdir()
    (symlink / "schemas").symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        generate_provider_ready_contract(symlink)
    assert not (target / "aar-provider-ready-schemas-v1.json").exists()


def test_verify_rejects_extra_and_symlink_entries_without_mutation(tmp_path: Path) -> None:
    generate_provider_ready_contract(tmp_path)
    extra = tmp_path / "tests/fixtures/provider-ready/valid/extra.json"
    extra.write_bytes(b"extra")
    with pytest.raises(ValueError):
        verify_provider_ready_assets(tmp_path)
    extra.unlink()
    link = tmp_path / "tests/fixtures/provider-ready/invalid/link.json"
    link.symlink_to(tmp_path / "schemas/aar-provider-ready-schemas-v1.json")
    with pytest.raises(ValueError):
        verify_provider_ready_assets(tmp_path)
    link.unlink()
    assert verify_provider_ready_assets(tmp_path)


def test_invocation_owned_partial_install_is_cleaned(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from pathlib import Path as ConcretePath

    original_rename = ConcretePath.rename

    def fail_fixture_install(self: ConcretePath, target: str | Path) -> ConcretePath:
        if ConcretePath(target).name == "provider-ready":
            raise OSError("injected fixture install failure")
        return original_rename(self, target)

    monkeypatch.setattr(ConcretePath, "rename", fail_fixture_install)
    with pytest.raises(OSError, match="injected fixture install failure"):
        generate_provider_ready_contract(tmp_path)
    assert not (tmp_path / "schemas/aar-provider-ready-schemas-v1.json").exists()
    assert not (tmp_path / "tests/fixtures/provider-ready").exists()
    assert not list(tmp_path.glob(".provider-ready-stage-*"))


def test_secret_injection_and_inert_operator_canaries() -> None:
    documents = provider_ready_fixture_documents()
    alias = documents["valid/provider-alias-attestation.json"]
    assert "credential_resolver_executable_digest" in alias
    plan = documents["valid/cutover-plan.json"]
    assert plan["snapshot"]["destination"] == "/runtime/snapshots/snapshot-001.db"
    assert plan["final_profile_output"] == "/runtime/profiles/final.json"
    assets = provider_ready_asset_bytes()
    assert all(b"A1C_GF_SECRET" not in raw and b"super-secret" not in raw for raw in assets.values())

    prohibited = dict(documents["valid/method-adapter.json"])
    prohibited["secret_value"] = "super-secret"
    prohibited["manifest_digest"] = canonical_sha256({key: value for key, value in prohibited.items() if key != "manifest_digest"})
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        validate_provider_ready_document_bytes(canonical_json_bytes(prohibited))


def test_static_imports_are_pure_and_legacy_hashes_remain_unchanged() -> None:
    source = inspect.getsource(importlib.import_module("aar.provider_ready_contract_generator"))
    for forbidden in ("import subprocess", "import socket", "import requests", "import urllib", "import sqlite3", "os.environ", "exec(", "eval(", "resolve("):
        assert forbidden not in source
    for relative, expected in LEGACY_HASHES.items():
        assert hashlib.sha256((REPO_ROOT / relative).read_bytes()).hexdigest() == expected


def test_frozen_v5_v6_d1_d2_bytes_use_one_atomic_owner_manifest() -> None:
    manifest = {**LEGACY_HASHES, **FROZEN_INSTALLER_HASHES}
    assert len(manifest) == len(LEGACY_HASHES) + len(FROZEN_INSTALLER_HASHES)
    for relative, expected in manifest.items():
        path = REPO_ROOT / relative
        assert path.is_file(), relative
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, relative
