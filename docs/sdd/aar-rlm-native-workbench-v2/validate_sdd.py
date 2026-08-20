#!/usr/bin/env python3
# ruff: noqa: E501
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tarfile
from copy import deepcopy
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry, Resource

from portable_regex import validate_portable_linear_pattern

ROOT = Path(__file__).resolve().parent
DEFECT_PATH = ROOT / "defect-register.json"
ACCEPTANCE_PATH = ROOT / "acceptance-matrix.json"
FAULT_PATH = ROOT / "fault-matrix.json"
README_PATH = ROOT / "README.md"
CONTRACTS_PATH = ROOT / "CONTRACTS.md"
LIFECYCLE_PATH = ROOT / "LIFECYCLE.md"
MIGRATION_PATH = ROOT / "MIGRATION.md"
MIGRATION_SQL_PATH = ROOT / "migration-v6.sql"
HANDOFF_PATH = ROOT / "implementation-handoff.md"
CONTRACT_DIR = ROOT / "contracts"
FIXTURE_DIR = ROOT / "fixtures"
CONTRACT_MANIFEST_PATH = CONTRACT_DIR / "contract-manifest.json"
EVIDENCE_SCHEMA_PATH = CONTRACT_DIR / "aar-acceptance-evidence-v1.schema.json"
PACKAGE_MANIFEST_PATH = ROOT / "sdd-package-manifest.json"
EXCLUDED_PACKAGE_DIRECTORIES = {".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}

VALID_CLASSIFICATIONS = {
    "observed_defect",
    "source_supported_risk",
    "product_gap",
    "declared_boundary",
}
VALID_SEVERITIES = {"critical", "high", "medium", "low"}
VALID_ALTITUDES = {"T0", "T1", "T2", "T3", "T4"}
SENSITIVE_PATH_PATTERNS = (
    re.compile(r"/home/[A-Za-z0-9_.-]+"),
    re.compile(r"/mnt/[a-z]/", re.IGNORECASE),
    re.compile(r"[A-Za-z]:\\"),
)
REQUIRED_GENERATED = {
    "contracts/aar-acceptance-evidence-v1.schema.json",
    "contracts/aar-artifact-publication-v1.schema.json",
    "contracts/aar-caller-work-v1.schema.json",
    "contracts/aar-migration-cutover-v1.schema.json",
    "contracts/aar-mcp-tools-v8.json",
    "contracts/aar-mcp-tools-v8-combined.json",
    "contracts/aar-mcp-tools-v7-binding.json",
    "contracts/aar-rlm-workbench-v1.schema.json",
    "contracts/aar-workspace-broker-frame-v1.schema.json",
    "fault-matrix.json",
    "fixtures/fixture-index.json",
    "fixtures/registry-v5.sql",
    "fixtures/registry-v5-binding.json",
    "fixtures/registry-v5-migration-verification.json",
    "fixtures/valid-route-catalog.json",
    "fixtures/valid-broker-catalog.json",
    "fixtures/valid-workbench-capabilities.json",
    "fixtures/invalid-capability-schema-digest.json",
    "fixtures/invalid-capability-planner-digest.json",
    "fixtures/invalid-capability-reference-only.json",
    "fixtures/negative-contract-index.json",
    "fixtures/valid-ticket-send-started.json",
    "fixtures/invalid-ticket-send-started-missing-request-digest.json",
    "fixtures/valid-ticket-cancelled-before-send-pending.json",
    "fixtures/valid-ticket-cancelled-before-send-reserved.json",
    "fixtures/invalid-ticket-cancelled-before-send-has-send-mark.json",
    "fixtures/valid-ticket-cancelled-certain.json",
    "fixtures/invalid-ticket-cancelled-certain-missing-settlement.json",
    "fixtures/valid-migration-attestation.json",
    "fixtures/valid-cutover-forward-ledger.json",
    "fixtures/invalid-cutover-post-write-rollback.json",
    "fixtures/invalid-cutover-semantic-rollback.json",
    "fixtures/invalid-ticket-send-reserved-unclaimed.json",
    "fixtures/invalid-phase-projection-mismatch.json",
    "fixtures/invalid-model-observation-output-on-failure.json",
    "fixtures/invalid-failure-unknown-code.json",
    "fixtures/invalid-failure-details-overflow.json",
    "fixtures/invalid-worker-empty-broker-intent.json",
    "fixtures/valid-workbench-execute.json",
    "migration-v6.sql",
}
EXPECTED_NEW_TOOLS = {
    "aar_rlm_workbench_execute",
    "aar_rlm_workbench_capabilities",
    "aar_rlm_workbench_status",
    "aar_broker_work_claim",
    "aar_broker_work_mark_send_started",
    "aar_broker_work_cancel_before_send",
    "aar_broker_work_commit",
    "aar_broker_work_reconcile",
}
ALLOWED_SCHEMA_KEYWORDS = {
    "$schema",
    "$defs",
    "$ref",
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "minItems",
    "maxItems",
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "pattern",
    "enum",
    "const",
    "oneOf",
    "allOf",
    "if",
    "then",
    "else",
    "not",
    "minProperties",
    "maxProperties",
    "propertyNames",
    "title",
    "description",
}


def _no_duplicate_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _check_json_tree(value: Any, *, max_depth: int, max_nodes: int) -> None:
    node_count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > max_nodes:
            raise ValueError(f"JSON node limit exceeded: {max_nodes}")
        if depth > max_depth:
            raise ValueError(f"JSON depth limit exceeded: {max_depth}")
        if isinstance(item, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise ValueError("JSON string contains a lone surrogate")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)

    visit(value, 0)


def load_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) > 16_777_216:
        raise ValueError(f"{path.name}: JSON file exceeds 16 MiB package limit")
    text = raw.decode("utf-8", errors="strict")
    value = json.loads(text, object_pairs_hook=_no_duplicate_object)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: root must be an object")
    _check_json_tree(value, max_depth=256, max_nodes=1_000_000)
    return value


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def digest_value(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()


def digest_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_text(record: dict[str, Any], key: str, owner: str) -> None:
    value = record.get(key)
    require(isinstance(value, str) and bool(value.strip()), f"{owner}: missing non-empty {key}")


def validate_defects(document: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], set[str]]:
    require(
        document.get("schema_version") == "aar.sdd.defect-register.v1", "unexpected defect schema"
    )
    defects = document.get("defects")
    if not isinstance(defects, list) or not defects:
        raise ValueError("defects must be a non-empty list")
    by_id: dict[str, dict[str, Any]] = {}
    acceptance_refs: set[str] = set()
    for defect in defects:
        if not isinstance(defect, dict):
            raise ValueError("each defect must be an object")
        defect_id = defect.get("id")
        if not isinstance(defect_id, str) or re.fullmatch(r"AAR-PD-\d{3}", defect_id) is None:
            raise ValueError(f"invalid defect id: {defect_id!r}")
        require(defect_id not in by_id, f"duplicate defect id: {defect_id}")
        by_id[defect_id] = defect
        for key in (
            "title",
            "native_trigger",
            "observed_result",
            "violated_invariant",
            "repair_boundary",
        ):
            require_text(defect, key, defect_id)
        require(
            defect.get("classification") in VALID_CLASSIFICATIONS,
            f"{defect_id}: invalid classification",
        )
        require(defect.get("severity") in VALID_SEVERITIES, f"{defect_id}: invalid severity")
        require(
            isinstance(defect.get("core_journey_required"), bool), f"{defect_id}: invalid core flag"
        )
        evidence = defect.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise ValueError(f"{defect_id}: evidence must be non-empty")
        for item in evidence:
            if not isinstance(item, dict):
                raise ValueError(f"{defect_id}: evidence item must be object")
            require_text(item, "tier", defect_id)
            require_text(item, "summary", defect_id)
        refs = defect.get("acceptance_ids")
        if not isinstance(refs, list):
            raise ValueError(f"{defect_id}: acceptance_ids must be a list")
        if defect["core_journey_required"]:
            require(bool(refs), f"{defect_id}: core defect/boundary requires acceptance coverage")
        for ref in refs:
            require(
                isinstance(ref, str) and re.fullmatch(r"AC-[A-Z0-9-]+", ref) is not None,
                f"{defect_id}: invalid acceptance ref {ref!r}",
            )
            acceptance_refs.add(ref)
    return by_id, acceptance_refs


def validate_acceptance(
    document: dict[str, Any], defects: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    require(
        document.get("schema_version") == "aar.sdd.acceptance-matrix.v1",
        "unexpected acceptance schema",
    )
    require_text(document, "claim_rule", "acceptance matrix")
    rows = document.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("acceptance rows must be non-empty")
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("each acceptance row must be an object")
        row_id = row.get("id")
        if not isinstance(row_id, str) or re.fullmatch(r"AC-[A-Z0-9-]+", row_id) is None:
            raise ValueError(f"invalid acceptance id: {row_id!r}")
        require(row_id not in by_id, f"duplicate acceptance id: {row_id}")
        by_id[row_id] = row
        for key in ("area", "requirement", "setup", "action", "expected"):
            require_text(row, key, row_id)
        require(row.get("altitude") in VALID_ALTITUDES, f"{row_id}: invalid altitude")
        require("status" not in row, f"{row_id}: normative matrix cannot contain mutable status")
        implementation_required = row.get("required_for_implementation_verified")
        live_required = row.get("required_for_live_qualified")
        if not isinstance(implementation_required, bool):
            raise ValueError(f"{row_id}: invalid implementation flag")
        if not isinstance(live_required, bool):
            raise ValueError(f"{row_id}: invalid live flag")
        require(
            not implementation_required or live_required,
            f"{row_id}: implementation-required row must be live-required",
        )
        owners = row.get("source_owners")
        if (
            not isinstance(owners, list)
            or not owners
            or not all(isinstance(item, str) and item for item in owners)
        ):
            raise ValueError(f"{row_id}: source_owners must be non-empty strings")
        defect_refs = row.get("defect_ids")
        if not isinstance(defect_refs, list):
            raise ValueError(f"{row_id}: defect_ids must be a list")
        for defect_id in defect_refs:
            require(defect_id in defects, f"{row_id}: unknown defect id {defect_id}")
        if row["altitude"] == "T4":
            require(
                not implementation_required and live_required, f"{row_id}: T4 claim flags invalid"
            )
            require(
                row.get("authorization") == "separate_explicit_authorization_required",
                f"{row_id}: missing authorization rule",
            )
        else:
            require(
                implementation_required and live_required,
                f"{row_id}: T0-T3 must be required for both claims",
            )
    return by_id


def validate_cross_links(
    defects: dict[str, dict[str, Any]], acceptance_refs: set[str], rows: dict[str, dict[str, Any]]
) -> None:
    missing = sorted(acceptance_refs - rows.keys())
    require(not missing, f"defect register references unknown acceptance rows: {missing}")
    reverse: dict[str, int] = {key: 0 for key in defects}
    for row in rows.values():
        for defect_id in row["defect_ids"]:
            reverse[defect_id] += 1
    uncovered = sorted(
        defect_id
        for defect_id, defect in defects.items()
        if defect["core_journey_required"] and reverse[defect_id] == 0
    )
    require(not uncovered, f"core defects lack reverse acceptance coverage: {uncovered}")
    non_implementation = sorted(
        row_id
        for row_id in acceptance_refs
        if not rows[row_id]["required_for_implementation_verified"]
    )
    require(
        not non_implementation, f"core defect repair cannot depend only on T4: {non_implementation}"
    )


def validate_fault_matrix(
    document: dict[str, Any], rows: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    require(document.get("schema_version") == "aar.sdd-fault-matrix.v1", "unexpected fault schema")
    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("fault cases must be non-empty")
    by_id: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("fault case must be object")
        case_id = case.get("id")
        if not isinstance(case_id, str) or re.fullmatch(r"FC-[A-Z0-9-]+", case_id) is None:
            raise ValueError(f"invalid fault id: {case_id!r}")
        require(case_id not in by_id, f"duplicate fault id: {case_id}")
        by_id[case_id] = case
        parent = case.get("acceptance_id")
        if not isinstance(parent, str):
            raise ValueError(f"{case_id}: invalid acceptance row {parent!r}")
        require(parent in rows, f"{case_id}: unknown acceptance row {parent!r}")
        require(case.get("required") is True, f"{case_id}: all listed fault cases are required")
        for key in ("barrier", "action", "expected"):
            require_text(case, key, case_id)
    required_fault_parents = {
        "AC-C04",
        "AC-A02",
        "AC-A03",
        "AC-A04",
        "AC-B04",
        "AC-J05",
        "AC-L01",
        "AC-L03",
        "AC-L04",
        "AC-L05",
        "AC-L06",
        "AC-L07",
        "AC-L08",
        "AC-S01",
        "AC-S03",
        "AC-S04",
    }
    covered = {
        parent
        for case in cases
        if isinstance(case, dict) and isinstance((parent := case.get("acceptance_id")), str)
    }
    require(
        not (required_fault_parents - covered),
        f"fault matrix missing required parents: {sorted(required_fault_parents - covered)}",
    )
    return by_id


def validate_contract_manifest() -> dict[str, Any]:
    manifest = load_json(CONTRACT_MANIFEST_PATH)
    require(
        manifest.get("schema_version") == "aar.sdd-contract-manifest.v1",
        "unexpected contract manifest schema",
    )
    require(
        manifest.get("generator_sha256") == digest_file(ROOT / "generate_contracts.py"),
        "generator digest drift",
    )
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ValueError("contract manifest files missing")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("contract manifest entry must be object")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in seen:
            raise ValueError(f"invalid/duplicate manifest path: {relative!r}")
        seen.add(relative)
        path = ROOT / relative
        require(path.is_file(), f"manifest file missing: {relative}")
        require(entry.get("size_bytes") == path.stat().st_size, f"manifest size drift: {relative}")
        require(entry.get("sha256") == digest_file(path), f"manifest digest drift: {relative}")
    required_contract_generated = REQUIRED_GENERATED - {
        "fixtures/registry-v5-migration-verification.json"
    }
    require(
        seen >= required_contract_generated,
        f"manifest missing required generated files: {sorted(required_contract_generated - seen)}",
    )
    payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    require(
        manifest.get("manifest_digest") == digest_value(payload),
        "contract manifest self digest drift",
    )
    return manifest


def validate_package_manifest(contract_manifest_digest: str) -> dict[str, Any]:
    manifest = load_json(PACKAGE_MANIFEST_PATH)
    require(
        manifest.get("schema_version") == "aar.sdd-package-manifest.v1",
        "package manifest schema mismatch",
    )
    require(
        manifest.get("contract_manifest_digest") == contract_manifest_digest,
        "package/contract manifest mismatch",
    )
    baseline = manifest.get("baseline")
    if not isinstance(baseline, dict):
        raise ValueError("package baseline missing")
    require(
        baseline.get("source_commit") == "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "package source baseline mismatch",
    )
    require(baseline.get("runtime_schema") == "aar.runtime.v1", "runtime contract version mismatch")
    require(
        baseline.get("operation_continuity_schema") == "aar.operation-continuity.v1",
        "continuity contract version mismatch",
    )
    require(baseline.get("registry_schema_version") == 5, "registry storage baseline mismatch")
    files = manifest.get("files")
    if not isinstance(files, list):
        raise ValueError("package manifest files missing")
    seen: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise ValueError("package manifest entry must be object")
        relative = entry.get("path")
        if not isinstance(relative, str) or relative in seen:
            raise ValueError(f"invalid/duplicate package path: {relative!r}")
        seen.add(relative)
        path = ROOT / relative
        require(path.is_file(), f"package file missing: {relative}")
        require(entry.get("size_bytes") == path.stat().st_size, f"package size drift: {relative}")
        require(entry.get("sha256") == digest_file(path), f"package digest drift: {relative}")
    expected = {
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.name not in {PACKAGE_MANIFEST_PATH.name, "review-receipt.json"}
        and not EXCLUDED_PACKAGE_DIRECTORIES.intersection(path.parts)
        and path.suffix != ".pyc"
        and path.name != "test-results.json"
    }
    require(
        seen == expected,
        f"package file set drift: missing={sorted(expected - seen)} extra={sorted(seen - expected)}",
    )
    normative = manifest.get("normative_files")
    require(
        isinstance(normative, list) and set(normative) <= seen, "package normative file set invalid"
    )
    payload = {key: value for key, value in manifest.items() if key != "manifest_digest"}
    require(
        manifest.get("manifest_digest") == digest_value(payload),
        "package manifest self-digest mismatch",
    )
    return manifest


def validate_schema_profile() -> None:
    contracts: dict[str, dict[str, Any]] = {}
    for path in sorted(CONTRACT_DIR.glob("*.schema.json")):
        document = load_json(path)
        Draft202012Validator.check_schema(document)
        contracts[path.name] = document
    for owner, document in contracts.items():
        for external_ref in iter_external_refs(document):
            filename, fragment = external_ref.split("#", 1)
            require(filename in contracts, f"{owner}: external schema missing: {filename}")
            require(
                fragment.startswith("/$defs/"),
                f"{owner}: external fragment forbidden: {external_ref}",
            )
            def_name = fragment.removeprefix("/$defs/")
            require(
                def_name in contracts[filename].get("$defs", {}),
                f"{owner}: external def missing: {external_ref}",
            )
    workbench = contracts["aar-rlm-workbench-v1.schema.json"]
    execute_schema = workbench["$defs"]["RlmWorkbenchExecuteInput"]
    execute_validator = Draft202012Validator(execute_schema)
    index = load_json(FIXTURE_DIR / "fixture-index.json")
    for item in index.get("fixtures", []):
        path = FIXTURE_DIR / item["path"]
        value = load_json(path)
        if item["path"] == "valid-route-catalog.json":
            continue
        structural_valid = not list(execute_validator.iter_errors(value))
        require(
            structural_valid is item["schema_valid"], f"fixture structural mismatch: {item['path']}"
        )
        if item["profile_valid"]:
            validate_embedded_contracts(value)
        elif structural_valid:
            try:
                validate_embedded_contracts(value)
            except (KeyError, TypeError, ValueError):
                pass
            else:
                raise ValueError(f"fixture profile mutant unexpectedly passed: {item['path']}")


def iter_external_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, list):
        for item in value:
            refs.extend(iter_external_refs(item))
    elif isinstance(value, dict):
        ref_value = value.get("$ref")
        if isinstance(ref_value, str) and not ref_value.startswith("#"):
            refs.append(ref_value)
        for item in value.values():
            refs.extend(iter_external_refs(item))
    return refs


def validate_embedded_contracts(execute_request: dict[str, Any]) -> None:
    spec = execute_request["spec"]
    route = spec["model"]["route_binding"]
    profile = {
        key: route[key]
        for key in (
            "schema_version",
            "profile_id",
            "provider_driver",
            "provider",
            "model",
            "reasoning_effort",
            "max_output_tokens",
            "fallback_policy",
            "cache_policy",
        )
    }
    require(route["profile_digest"] == digest_value(profile), "route profile digest mismatch")
    require(route["fallback_policy"] == "none", "workbench route fallback must be none")
    catalog = load_json(FIXTURE_DIR / "valid-route-catalog.json")
    require(route["catalog_digest"] == digest_value(catalog), "route catalog digest mismatch")
    require(
        any(
            item.get("profile_id") == route["profile_id"]
            and digest_value(item) == route["profile_digest"]
            for item in catalog.get("profiles", [])
            if isinstance(item, dict)
        ),
        "route profile not present in catalog",
    )
    runtime_planner_schema = load_json(CONTRACT_DIR / "aar-rlm-workbench-v1.schema.json")["$defs"][
        "RlmDirective"
    ]
    require(
        len(canonical_bytes(runtime_planner_schema)) <= 65_536,
        "runtime planner schema exceeds 64 KiB",
    )
    Draft202012Validator.check_schema(runtime_planner_schema)
    for contract in (spec["completion"]["output_contract"],):
        require(
            contract["dialect"] == "https://json-schema.org/draft/2020-12/schema",
            "schema dialect mismatch",
        )
        require(contract["profile"] == "aar.json-schema-profile.v1", "schema profile mismatch")
        schema = contract["schema"]
        require(len(canonical_bytes(schema)) <= 65_536, "embedded schema exceeds 64 KiB")
        require(1 <= contract["max_instance_bytes"] <= 1_048_576, "embedded instance limit invalid")
        require(
            contract["schema_digest"] == digest_value(schema), "embedded schema digest mismatch"
        )
        validate_schema_profile_contract(schema)


def validate_schema_profile_contract(schema: dict[str, Any]) -> None:
    _check_json_tree(schema, max_depth=32, max_nodes=4096)
    root_defs = schema.get("$defs", {})
    if root_defs is None:
        root_defs = {}
    elif not isinstance(root_defs, dict):
        raise ValueError("$defs must be an object")

    def validate_pattern(pattern: Any) -> None:
        validate_portable_linear_pattern(pattern)

    def validate_node(node: Any) -> None:
        if not isinstance(node, dict):
            raise ValueError("schema node must be an object")
        unknown = set(node) - ALLOWED_SCHEMA_KEYWORDS
        require(not unknown, f"schema keywords forbidden: {sorted(unknown)}")
        ref_value = node.get("$ref")
        if ref_value is not None:
            require(
                isinstance(ref_value, str) and ref_value.startswith("#/$defs/"),
                f"schema ref forbidden: {ref_value!r}",
            )
            target_name = ref_value.removeprefix("#/$defs/")
            require(
                "/" not in target_name and target_name in root_defs,
                f"schema local ref unresolved: {ref_value}",
            )
        type_value = node.get("type")
        if type_value is not None:
            require(
                type_value in {"object", "array", "string", "integer", "number", "boolean", "null"},
                "schema type invalid",
            )
        properties = node.get("properties")
        if properties is not None:
            require(
                isinstance(properties, dict) and len(properties) <= 256, "schema properties invalid"
            )
            for property_name, child in properties.items():
                require(
                    isinstance(property_name, str) and 0 < len(property_name) <= 128,
                    "schema property name invalid",
                )
                validate_node(child)
        definitions = node.get("$defs")
        if definitions is not None:
            require(
                isinstance(definitions, dict) and len(definitions) <= 256, "schema defs invalid"
            )
            for definition_name, child in definitions.items():
                require(
                    isinstance(definition_name, str)
                    and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", definition_name)
                    is not None,
                    "schema def name invalid",
                )
                validate_node(child)
        required = node.get("required")
        if required is not None:
            require(
                isinstance(required, list)
                and all(isinstance(item, str) for item in required)
                and required == sorted(set(required))
                and len(required) <= 256,
                "schema required list must be sorted and unique",
            )
        additional = node.get("additionalProperties")
        if additional is not None and not isinstance(additional, bool):
            validate_node(additional)
        items = node.get("items")
        if items is not None:
            validate_node(items)
        for keyword in ("oneOf", "allOf"):
            branches = node.get(keyword)
            if branches is not None:
                require(
                    isinstance(branches, list) and 1 <= len(branches) <= 32,
                    f"schema {keyword} invalid",
                )
                for branch in branches:
                    validate_node(branch)
        for keyword in ("if", "then", "else", "not", "propertyNames"):
            child = node.get(keyword)
            if child is not None:
                validate_node(child)
        pattern = node.get("pattern")
        if pattern is not None:
            validate_pattern(pattern)
        enum_values = node.get("enum")
        if enum_values is not None:
            require(
                isinstance(enum_values, list) and 1 <= len(enum_values) <= 128,
                "schema enum invalid",
            )
            _check_json_tree(enum_values, max_depth=4, max_nodes=512)
        for key in (
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "minProperties",
            "maxProperties",
        ):
            if key in node:
                require(
                    isinstance(node[key], int) and 0 <= node[key] <= 1_048_576,
                    f"schema {key} invalid",
                )
        for key in ("minimum", "maximum"):
            if key in node:
                require(
                    isinstance(node[key], (int, float)) and not isinstance(node[key], bool),
                    f"schema {key} invalid",
                )

    # Candidate-selected patterns must pass the portable profile before the
    # Draft meta-validator is allowed to invoke its host regex engine.
    validate_node(schema)
    Draft202012Validator.check_schema(schema)


def validate_negative_contract_fixtures() -> None:
    contract_files = tuple(path.name for path in sorted(CONTRACT_DIR.glob("*.schema.json")))
    require(bool(contract_files), "no contract schemas found")
    registry = Registry()
    for filename in contract_files:
        path = (CONTRACT_DIR / filename).resolve()
        registry = registry.with_resource(path.as_uri(), Resource.from_contents(load_json(path)))
    index = load_json(FIXTURE_DIR / "negative-contract-index.json")

    def pointer_parent(value: Any, pointer: str) -> tuple[dict[str, Any] | list[Any], str]:
        require(pointer.startswith("/") and pointer != "/", f"invalid mutation pointer: {pointer}")
        tokens = [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]
        parent: dict[str, Any] | list[Any] = value
        for token in tokens[:-1]:
            if isinstance(parent, dict):
                child = parent.get(token)
            elif token.isdigit() and int(token) < len(parent):
                child = parent[int(token)]
            else:
                child = None
            if not isinstance(child, (dict, list)):
                raise ValueError(f"mutation pointer parent missing: {pointer}")
            parent = child
        return parent, tokens[-1]

    def pointer_has(parent: dict[str, Any] | list[Any], token: str) -> bool:
        return (
            token in parent
            if isinstance(parent, dict)
            else token.isdigit() and int(token) < len(parent)
        )

    def pointer_get(parent: dict[str, Any] | list[Any], token: str) -> Any:
        return parent[token] if isinstance(parent, dict) else parent[int(token)]

    def pointer_set(parent: dict[str, Any] | list[Any], token: str, value: Any) -> None:
        if isinstance(parent, dict):
            parent[token] = value
        else:
            parent[int(token)] = value

    def pointer_delete(parent: dict[str, Any] | list[Any], token: str) -> None:
        if isinstance(parent, dict):
            del parent[token]
        else:
            del parent[int(token)]

    for item in index["fixtures"]:
        contract_path = (CONTRACT_DIR / item["contract_file"]).resolve()
        root_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$ref": f"{contract_path.as_uri()}#/$defs/{item['definition']}",
        }
        validator = Draft202012Validator(root_schema, registry=registry)
        valid_value = load_json(FIXTURE_DIR / item["valid_path"])
        valid_errors = list(validator.iter_errors(valid_value))
        require(
            not valid_errors,
            f"positive contract fixture was rejected: {item['valid_path']}: "
            f"{valid_errors[0].message if valid_errors else ''}",
        )
        invalid_value = load_json(FIXTURE_DIR / item["path"])
        invalid_errors = list(validator.iter_errors(invalid_value))
        require(bool(invalid_errors), f"negative contract fixture was accepted: {item['path']}")
        atomic_mutant = deepcopy(valid_value)
        mutation_pointer = item["mutation_pointer"]
        mutation_op = item.get("mutation_op", "replace")
        valid_parent, valid_key = pointer_parent(atomic_mutant, mutation_pointer)
        invalid_parent, invalid_key = pointer_parent(invalid_value, mutation_pointer)
        if mutation_op == "replace":
            require(
                pointer_has(valid_parent, valid_key) and pointer_has(invalid_parent, invalid_key),
                f"replace mutation target missing: {mutation_pointer}",
            )
            pointer_set(
                valid_parent,
                valid_key,
                deepcopy(pointer_get(invalid_parent, invalid_key)),
            )
        elif mutation_op == "remove":
            require(
                pointer_has(valid_parent, valid_key)
                and not pointer_has(invalid_parent, invalid_key),
                f"remove mutation target invalid: {mutation_pointer}",
            )
            pointer_delete(valid_parent, valid_key)
        elif mutation_op == "add":
            require(
                not pointer_has(valid_parent, valid_key)
                and pointer_has(invalid_parent, invalid_key),
                f"add mutation target invalid: {mutation_pointer}",
            )
            pointer_set(
                valid_parent,
                valid_key,
                deepcopy(pointer_get(invalid_parent, invalid_key)),
            )
        else:
            raise ValueError(f"unsupported mutation op: {mutation_op!r}")
        require(
            atomic_mutant == invalid_value,
            f"negative contract fixture is not atomic: {item['path']}",
        )


def validate_broker_capabilities() -> None:
    catalog = load_json(FIXTURE_DIR / "valid-broker-catalog.json")
    catalog_core = {"schema_version": catalog["schema_version"], "contracts": catalog["contracts"]}
    require(
        catalog["catalog_digest"] == digest_value(catalog_core), "broker catalog digest mismatch"
    )
    contracts = catalog["contracts"]
    require(isinstance(contracts, list), "broker catalog contracts missing")
    methods = [row.get("method") if isinstance(row, dict) else None for row in contracts]
    contract_ids = [row.get("contract_id") if isinstance(row, dict) else None for row in contracts]
    require(all(isinstance(value, str) for value in methods), "broker method missing")
    require(
        all(isinstance(value, str) for value in contract_ids),
        "broker contract id missing",
    )
    require(len(methods) == len(set(methods)), "broker catalog contains duplicate methods")
    require(
        len(contract_ids) == len(set(contract_ids)),
        "broker catalog contains duplicate contract ids",
    )
    expected_contracts = {row["method"]: row for row in contracts}
    require(len(expected_contracts) == 6, "broker catalog must contain six unique methods")
    definition_map = {
        "model.request": (
            "aar-caller-work-v1.schema.json",
            "ModelRequestV2",
            "aar-caller-work-v1.schema.json",
            "ModelObservation",
        ),
        "subagent.submit": (
            "aar-caller-work-v1.schema.json",
            "SubagentSubmitV2",
            "aar-caller-work-v1.schema.json",
            "ChildObservation",
        ),
        "subagent.result": (
            "aar-caller-work-v1.schema.json",
            "SubagentResultV2",
            "aar-caller-work-v1.schema.json",
            "ChildObservation",
        ),
        "evidence.query": (
            "aar-caller-work-v1.schema.json",
            "EvidenceQueryV2",
            "aar-caller-work-v1.schema.json",
            "EvidenceObservation",
        ),
        "artifact.put": (
            "aar-artifact-publication-v1.schema.json",
            "ArtifactStage",
            "aar-artifact-publication-v1.schema.json",
            "ArtifactBinding",
        ),
        "effect.propose": (
            "aar-caller-work-v1.schema.json",
            "EffectProposeV2",
            "aar-caller-work-v1.schema.json",
            "EffectObservation",
        ),
    }
    for method, (request_file, request_def, response_file, response_def) in definition_map.items():
        request_schema = load_json(CONTRACT_DIR / request_file)["$defs"][request_def]
        response_schema = load_json(CONTRACT_DIR / response_file)["$defs"][response_def]
        request_properties = request_schema.get("properties", {})
        identity_property = (
            request_properties.get("contract_id") or request_properties.get("schema_version") or {}
        )
        require(
            identity_property.get("const") == expected_contracts[method]["contract_id"],
            f"{method}: catalog contract id mismatch",
        )
        if "method" in request_properties:
            require(
                request_properties["method"].get("const") == method,
                f"{method}: request method const mismatch",
            )
        require(
            expected_contracts[method]["request_schema_digest"] == digest_value(request_schema),
            f"{method}: request schema digest mismatch",
        )
        require(
            expected_contracts[method]["response_schema_digest"] == digest_value(response_schema),
            f"{method}: response schema digest mismatch",
        )

    workbench_contract = load_json(CONTRACT_DIR / "aar-rlm-workbench-v1.schema.json")
    capability_schema = workbench_contract["$defs"]["RlmWorkbenchCapability"]
    planner_directive_schema = workbench_contract["$defs"]["RlmDirective"]
    planner_directive_digest = digest_value(planner_directive_schema)
    capability_validator = Draft202012Validator(capability_schema)
    combined = load_json(CONTRACT_DIR / "aar-mcp-tools-v8-combined.json")

    def admitted(value: dict[str, Any]) -> bool:
        capability_validator.validate(value)
        if value["tool_surface_digest"] != combined["tool_surface_digest"]:
            return False
        if value["broker_catalog_digest"] != catalog["catalog_digest"]:
            return False
        if value["planner_directive_schema_version"] != "aar.rlm-directive.v1":
            return False
        if value["planner_directive_schema_digest"] != planner_directive_digest:
            return False
        method_names = [row["method"] for row in value["methods"]]
        capability_contract_ids = [row["contract_id"] for row in value["methods"]]
        if len(method_names) != len(set(method_names)):
            return False
        if len(capability_contract_ids) != len(set(capability_contract_ids)):
            return False
        rows = {row["method"]: row for row in value["methods"]}
        if set(rows) != set(expected_contracts):
            return False
        for method, expected in expected_contracts.items():
            row = rows[method]
            for key in ("contract_id", "request_schema_digest", "response_schema_digest"):
                if row[key] != expected[key]:
                    return False
            if (
                not row["configured"]
                or row["reference_only"]
                or row["backend_kind"] in {"reference", "unconfigured"}
            ):
                return False
        return True

    require(
        admitted(load_json(FIXTURE_DIR / "valid-workbench-capabilities.json")),
        "valid workbench capability not admitted",
    )
    require(
        not admitted(load_json(FIXTURE_DIR / "invalid-capability-schema-digest.json")),
        "schema-digest capability mutant admitted",
    )
    require(
        not admitted(load_json(FIXTURE_DIR / "invalid-capability-planner-digest.json")),
        "planner-digest capability mutant admitted",
    )
    require(
        not admitted(load_json(FIXTURE_DIR / "invalid-capability-reference-only.json")),
        "reference-only capability mutant admitted",
    )


def validate_tool_surface() -> None:
    surface = load_json(CONTRACT_DIR / "aar-mcp-tools-v8.json")
    require(surface.get("surface_version") == "aar.mcp-tools.v8", "tool surface version mismatch")
    tools = surface.get("tools")
    if not isinstance(tools, list):
        raise ValueError("tool surface tools missing")
    names = [
        name
        for tool in tools
        if isinstance(tool, dict) and isinstance((name := tool.get("name")), str)
    ]
    require(len(names) == len(tools), "tool surface contains an unnamed descriptor")
    require(len(names) == len(set(names)), "tool surface contains duplicate tool names")
    require(set(names) == EXPECTED_NEW_TOOLS, f"tool surface mismatch: {sorted(names)}")
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValueError("tool manifest entry must be object")
        for key in ("name", "title", "description", "capability", "input_schema", "output_schema"):
            require_text(tool, key, f"tool {tool.get('name')!r}")
        annotations = tool.get("annotations")
        if not isinstance(annotations, dict) or set(annotations) != {
            "readOnlyHint",
            "idempotentHint",
            "destructiveHint",
            "openWorldHint",
        }:
            raise ValueError(f"tool {tool['name']}: invalid annotations")
        require(
            all(isinstance(value, bool) for value in annotations.values()),
            f"tool {tool['name']}: annotation values must be boolean",
        )
        for schema_ref in (tool["input_schema"], tool["output_schema"]):
            filename, fragment = schema_ref.split("#", 1)
            schema = load_json(CONTRACT_DIR / filename)
            def_name = fragment.removeprefix("/$defs/")
            require(
                fragment.startswith("/$defs/") and def_name in schema.get("$defs", {}),
                f"tool {tool['name']}: unresolved schema ref {schema_ref}",
            )

    binding = load_json(CONTRACT_DIR / "aar-mcp-tools-v7-binding.json")
    require(
        binding["source_commit"] == "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "baseline tool binding commit mismatch",
    )
    require(
        binding["sha256"]
        == "sha256:6ebf848eee74795771af23dfaaeb70fd5030cb88878327f76ffc10d1c4639ab8",
        "baseline tool manifest byte mismatch",
    )
    require(
        binding["tool_count"] == 30 and len(binding["tool_names"]) == 30,
        "baseline tool count mismatch",
    )
    combined = load_json(CONTRACT_DIR / "aar-mcp-tools-v8-combined.json")
    require(
        combined["tool_surface_version"] == "aar.mcp-tools.v8", "combined surface version mismatch"
    )
    require(combined["server_version"] == "0.5.0a0", "combined target package version mismatch")
    combined_tools = combined["tools"]
    expected_combined_count = 30 + len(tools)
    require(
        isinstance(combined_tools, list) and len(combined_tools) == expected_combined_count,
        f"combined surface must contain {expected_combined_count} tools",
    )
    combined_names = [tool["name"] for tool in combined_tools]
    require(
        len(combined_names) == len(set(combined_names)),
        "combined surface contains duplicate tool names",
    )
    require(
        combined_names[:30] == binding["tool_names"], "combined surface changed v7 prefix order"
    )
    require(combined_names[30:] == names, "combined surface additive tool order mismatch")

    schema_prefix = {
        "aar-rlm-workbench-v1.schema.json": "workbench",
        "aar-caller-work-v1.schema.json": "caller",
        "aar-artifact-publication-v1.schema.json": "artifact",
        "aar-workspace-broker-frame-v1.schema.json": "worker",
        "aar-acceptance-evidence-v1.schema.json": "evidence",
    }
    published_contracts = {
        path.name: load_json(path) for path in CONTRACT_DIR.glob("*.schema.json")
    }

    def bundled_schema(schema_ref: str) -> dict[str, Any]:
        filename, fragment = schema_ref.split("#", 1)
        definition = fragment.removeprefix("/$defs/")
        require(
            fragment.startswith("/$defs/")
            and filename in published_contracts
            and definition in published_contracts[filename].get("$defs", {}),
            f"unresolved additive descriptor schema ref {schema_ref}",
        )
        collected: dict[tuple[str, str], dict[str, Any]] = {}
        in_progress: set[tuple[str, str]] = set()

        def alias(key: tuple[str, str]) -> str:
            require(key[0] in schema_prefix, f"schema prefix missing for {key[0]}")
            return f"{schema_prefix[key[0]]}__{key[1]}"

        def split_ref(current_file: str, ref_value: str) -> tuple[str, str]:
            if ref_value.startswith("#/$defs/"):
                return current_file, ref_value.removeprefix("#/$defs/")
            target_file, target_fragment = ref_value.split("#", 1)
            return target_file, target_fragment.removeprefix("/$defs/")

        def collect(key: tuple[str, str]) -> None:
            if key in collected or key in in_progress:
                return
            require(
                key[0] in published_contracts
                and key[1] in published_contracts[key[0]].get("$defs", {}),
                f"unresolved bundled contract ref: {key}",
            )
            in_progress.add(key)
            collected[key] = rewrite(deepcopy(published_contracts[key[0]]["$defs"][key[1]]), key[0])
            in_progress.remove(key)

        def rewrite(value: Any, current_file: str) -> Any:
            if isinstance(value, list):
                return [rewrite(item, current_file) for item in value]
            if not isinstance(value, dict):
                return value
            ref_value = value.get("$ref")
            if isinstance(ref_value, str):
                target = split_ref(current_file, ref_value)
                collect(target)
                replacement = {"$ref": f"#/$defs/{alias(target)}"}
                replacement.update(
                    {
                        key: rewrite(item, current_file)
                        for key, item in value.items()
                        if key != "$ref"
                    }
                )
                return replacement
            return {key: rewrite(item, current_file) for key, item in value.items()}

        root_schema = rewrite(
            deepcopy(published_contracts[filename]["$defs"][definition]), filename
        )
        if collected:
            root_schema["$defs"] = {
                alias(key): value
                for key, value in sorted(collected.items(), key=lambda item: alias(item[0]))
            }
        root_schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        Draft202012Validator.check_schema(root_schema)
        return root_schema

    expected_additive_descriptors = []
    for tool in tools:
        annotations = dict(tool["annotations"])
        annotations["title"] = tool["title"]
        expected_additive_descriptors.append(
            {
                "_meta": None,
                "annotations": annotations,
                "description": tool["description"],
                "execution": None,
                "icons": None,
                "inputSchema": bundled_schema(tool["input_schema"]),
                "name": tool["name"],
                "outputSchema": {
                    "type": "object",
                    **bundled_schema(tool["output_schema"]),
                },
                "title": None,
            }
        )
    require(
        combined_tools[30:] == expected_additive_descriptors,
        "combined surface additive descriptors diverge from the additive authority",
    )
    core = {
        "server_name": combined["server_name"],
        "server_version": combined["server_version"],
        "sdk": combined["sdk"],
        "protocol_versions": combined["protocol_versions"],
        "instructions": combined["instructions"],
        "tool_surface_version": combined["tool_surface_version"],
        "tools": combined_tools,
    }
    require(
        combined["tool_surface_digest"] == digest_value(core),
        "combined tool surface digest mismatch",
    )
    descriptor_keys = {
        "_meta",
        "annotations",
        "description",
        "execution",
        "icons",
        "inputSchema",
        "name",
        "outputSchema",
        "title",
    }
    for descriptor in combined_tools:
        require(
            isinstance(descriptor, dict) and set(descriptor) == descriptor_keys,
            f"invalid combined descriptor: {descriptor.get('name') if isinstance(descriptor, dict) else descriptor!r}",
        )
        for key in ("inputSchema", "outputSchema"):
            schema = descriptor[key]
            require(
                isinstance(schema, dict), f"combined descriptor {descriptor['name']} lacks {key}"
            )
            Draft202012Validator.check_schema(schema)


def validate_migration_sql() -> None:
    sql = MIGRATION_SQL_PATH.read_text(encoding="utf-8")
    require("ALTER TABLE operations" not in sql, "migration may not alter frozen operations table")
    fixture_path = FIXTURE_DIR / "registry-v5.sql"
    binding = load_json(FIXTURE_DIR / "registry-v5-binding.json")
    require(binding["package_version"] == "0.4.0a6", "v5 fixture package mismatch")
    require(
        binding["source_commit"] == "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90",
        "v5 fixture source mismatch",
    )
    require(binding["schema_versions"] == [1, 2, 3, 4, 5], "v5 fixture migration history mismatch")
    require(binding["sql_dump_sha256"] == digest_file(fixture_path), "v5 fixture digest mismatch")
    require(
        binding["sql_dump_size_bytes"] == fixture_path.stat().st_size, "v5 fixture size mismatch"
    )
    require(binding.get("foreign_key_violation_count") == 0, "v5 fixture FK violations")
    populated_tables = set(binding.get("populated_tables", []))
    required_populated_tables = {
        "operations",
        "operation_attempts",
        "operation_leases",
        "rlm_jobs",
        "rlm_steps",
        "workspaces",
        "workspace_receipts",
        "asset_bodies",
        "asset_manifests",
        "asset_events",
        "broker_artifacts",
    }
    require(
        required_populated_tables <= populated_tables,
        f"v5 fixture lacks populated tables: {sorted(required_populated_tables - populated_tables)}",
    )
    require(
        isinstance(binding.get("canonical_data_row_set_digest"), str),
        "v5 fixture canonical data row-set digest missing",
    )
    verifier_source = (ROOT / "verify_registry_v5_migration.py").read_text(encoding="utf-8")
    require(
        "source.backup(destination)" in verifier_source
        and "shutil.copyfile" not in verifier_source,
        "migration verifier must use SQLite backup API, not raw file copy",
    )
    require(
        'connection.execute("BEGIN IMMEDIATE")' in verifier_source
        and "connection.executescript(migration_sql)" not in verifier_source,
        "migration verifier must commit DDL and attestation atomically",
    )
    require(
        "except UnsupportedRegistrySchema as error:" in verifier_source
        and "registry schema version 6 is newer than supported version 5" in verifier_source
        and "failed to open v6 for a non-schema reason" in verifier_source,
        "migration verifier must discriminate newer-schema rejection",
    )
    require(
        "installed_distribution_binding()" in verifier_source
        and "post_migration_data_digest != source_data_digest" in verifier_source
        and 'table != "runtime_meta"' in verifier_source
        and "host_runtime_generation != initial_runtime_generation + 1" in verifier_source,
        "migration verifier provenance or frozen-row binding missing",
    )
    verification = load_json(FIXTURE_DIR / "registry-v5-migration-verification.json")
    require(
        verification.get("schema_version") == "aar.sdd-registry-v5-migration-verification.v1",
        "migration verification receipt schema mismatch",
    )
    require(verification.get("package_version") == "0.4.0a6", "migration receipt package mismatch")
    require(
        verification.get("source_commit") == "a585ac52bef6f95f9dcfb40dc69f83d6d92b3b90"
        and verification.get("requested_revision") == "v0.4.0a6",
        "migration receipt installed source mismatch",
    )
    require(
        re.fullmatch(r"sha256:[0-9a-f]{64}", str(verification.get("installed_distribution_digest")))
        is not None
        and isinstance(verification.get("installed_distribution_file_count"), int)
        and verification["installed_distribution_file_count"] > 0,
        "migration receipt installed distribution binding invalid",
    )
    require(
        verification.get("baseline_sql_digest") == digest_file(fixture_path),
        "migration receipt baseline SQL mismatch",
    )
    require(
        verification.get("baseline_binding_file_digest")
        == digest_file(FIXTURE_DIR / "registry-v5-binding.json"),
        "migration receipt baseline binding mismatch",
    )
    require(
        verification.get("contract_manifest_digest")
        == load_json(CONTRACT_MANIFEST_PATH)["manifest_digest"],
        "migration receipt contract manifest mismatch",
    )
    require(
        verification.get("verifier_sha256")
        == digest_file(ROOT / "verify_registry_v5_migration.py"),
        "migration receipt verifier digest mismatch",
    )
    require(
        verification.get("baseline_fixture_data_row_set_digest")
        == binding["canonical_data_row_set_digest"],
        "migration receipt baseline row-set mismatch",
    )
    require(
        verification.get("canonical_v5_data_row_set_digest")
        == verification.get("pre_migration_v5_data_row_set_digest")
        == verification.get("post_migration_v5_data_row_set_digest"),
        "migration receipt frozen v5 row-set mismatch",
    )
    require(
        verification["migration_digest"] == digest_file(MIGRATION_SQL_PATH),
        "migration verification receipt digest mismatch",
    )
    for key in (
        "mid_transaction_rollback_reopened_by_v5",
        "committed_v6_rejected_by_v5",
        "v5_newer_schema_rejection_reason_verified",
        "migrated_integrity_and_fk_checked_before_v5_rejection",
        "restored_backup_reopened_by_v5",
        "caller_ticket_state_constraints_enforced",
        "caller_ticket_pre_send_cancellation_enforced",
        "caller_ticket_suspension_binding_enforced",
        "caller_command_storage_constraints_enforced",
        "rebind_prepare_abort_constraints_enforced",
        "rebind_commit_atomic_authority_verified",
        "rebind_post_commit_replay_classification_verified",
        "sqlite_backup_api_verified",
        "wal_committed_rows_included_in_snapshot",
        "populated_v5_data_rows_preserved",
        "v5_host_reopen_stable_rows_preserved",
        "v5_host_reopen_runtime_generation_increment_verified",
        "migration_attestation_row_verified",
        "migration_and_attestation_atomic_transaction_verified",
        "pre_write_snapshot_rollback_path_verified",
        "forward_retirement_path_verified",
        "rollback_after_post_snapshot_write_rejected",
    ):
        require(verification.get(key) is True, f"migration verification receipt failed: {key}")
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(fixture_path.read_text(encoding="utf-8"))
        connection.executescript(sql)
        connection.executescript(sql)
        require(
            not connection.execute("PRAGMA foreign_key_check").fetchall(),
            "migration FK check failed",
        )
        require(
            connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
            "migration integrity check failed",
        )
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        expected = {
            "migration_v6_attestations",
            "rlm_workbench_jobs",
            "rlm_workbench_cells",
            "rlm_workbench_suspensions",
            "caller_work_tickets",
            "caller_work_candidate_receipts",
            "caller_work_command_receipts",
            "rlm_workbench_successor_outbox",
            "rlm_workbench_attempt_authority",
            "rlm_workbench_rebind_transfers",
            "rlm_workbench_artifact_stages",
            "rlm_workbench_cell_manifests",
            "rlm_workbench_finalization_manifests",
            "broker_contract_catalog_v2",
            "broker_backend_availability_v2",
        }
        require(tables >= expected, f"migration missing tables: {sorted(expected - tables)}")
        ticket_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(caller_work_tickets)")
        }
        required_ticket_columns = {
            "send_started_at_unix_ms",
            "sent_request_digest",
            "sent_at_unix_ms",
            "provider_or_child_request_id",
            "settled_receipt_digest",
            "settled_at_unix_ms",
        }
        require(
            ticket_columns >= required_ticket_columns,
            f"caller ticket columns missing: {sorted(required_ticket_columns - ticket_columns)}",
        )
        foreign_key_groups: dict[int, set[tuple[str, str]]] = {}
        for row in connection.execute("PRAGMA foreign_key_list(caller_work_tickets)"):
            if str(row[2]) != "rlm_workbench_suspensions":
                continue
            foreign_key_groups.setdefault(int(row[0]), set()).add((str(row[3]), str(row[4])))
        semantic_binding = {
            ("operation_id", "operation_id"),
            ("suspension_revision", "suspension_revision"),
            ("ticket_id", "ticket_id"),
            ("method", "broker_method"),
            ("contract_id", "contract_id"),
            ("request_digest", "request_digest"),
            ("logical_owner_json", "logical_owner_json"),
        }
        require(
            any(pairs == semantic_binding for pairs in foreign_key_groups.values()),
            "caller ticket suspension semantic-binding FK missing",
        )
        ticket_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='caller_work_tickets'"
        ).fetchone()
        ticket_sql = str(ticket_sql_row[0]) if ticket_sql_row else ""
        require(
            "state NOT IN ('pending', 'cancelled_before_send')" in ticket_sql
            and "external_idempotency_key IS NOT NULL" in ticket_sql
            and "sent_request_digest IS NOT NULL" in ticket_sql
            and "'cancelled_certain', 'quarantined'" in ticket_sql,
            "caller ticket sent-state constraint missing",
        )
        require(
            "state = 'cancelled_before_send'" in ticket_sql
            and "'cancelled_before_send', 'cancelled_certain'" in ticket_sql
            and "send_started_at_unix_ms IS NULL" in ticket_sql
            and "settled_receipt_digest IS NOT NULL" in ticket_sql,
            "pre-send cancellation settlement constraint missing",
        )
        require(
            re.search(
                r"CHECK\s*\(\(state IN\s*\('settled_success',\s*'settled_failure',\s*"
                r"'cancelled_before_send',\s*'cancelled_certain'\)\)\s*<=\s*\(\s*"
                r"settled_receipt_digest IS NOT NULL\s+AND\s+settled_at_unix_ms IS NOT NULL",
                ticket_sql,
            )
            is not None,
            "cancelled_certain settlement constraint missing",
        )
        job_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(rlm_workbench_jobs)")
        }
        require(
            {"control_revision", "cancellation_revision", "cancellation_requested"} <= job_columns,
            "workbench control/cancellation projection missing",
        )
        outbox_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='rlm_workbench_successor_outbox'"
        ).fetchone()
        transfer_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='rlm_workbench_rebind_transfers'"
        ).fetchone()
        authority_sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name='rlm_workbench_attempt_authority'"
        ).fetchone()
        outbox_sql = str(outbox_sql_row[0]) if outbox_sql_row else ""
        transfer_sql = str(transfer_sql_row[0]) if transfer_sql_row else ""
        authority_sql = str(authority_sql_row[0]) if authority_sql_row else ""
        require(
            "state IN ('pending', 'prepared', 'consumed')" in outbox_sql
            and "successor_attempt_fence" in outbox_sql
            and "outbox_digest" in outbox_sql,
            "successor outbox durable preparation contract missing",
        )
        for required_term in (
            "token_digest TEXT PRIMARY KEY",
            "prior_attempt_fence",
            "successor_attempt_fence",
            "worker_process_identity_digest",
            "workspace_generation",
            "workspace_revision",
            "state IN ('prepared', 'committed', 'aborted', 'consumed')",
            "recovery_fenced_loss",
            "successor_authority_generation > prior_authority_generation",
        ):
            require(
                required_term in transfer_sql,
                f"rebind transfer authority missing term: {required_term}",
            )
        require(
            "attempt_fence" in authority_sql
            and "authority_generation" in authority_sql
            and "rebind_token_digest" in authority_sql
            and "worker_process_identity_digest" in authority_sql,
            "current attempt authority contract missing",
        )
        transfer_fks = {
            str(row[2])
            for row in connection.execute("PRAGMA foreign_key_list(rlm_workbench_rebind_transfers)")
        }
        authority_fks = {
            str(row[2])
            for row in connection.execute(
                "PRAGMA foreign_key_list(rlm_workbench_attempt_authority)"
            )
        }
        require(
            "rlm_workbench_successor_outbox" in transfer_fks
            and "rlm_workbench_rebind_transfers" in authority_fks,
            "rebind transfer/outbox/current-authority FK closure missing",
        )
    finally:
        connection.close()


def validate_cutover_ledger_semantics(ledger: dict[str, Any]) -> None:
    events = ledger["events"]
    states: list[str] = []
    previous_digest: str | None = None
    previous_time = -1
    for sequence, document in enumerate(events):
        event = document["event"]
        require(
            document["event_digest"] == digest_value(event),
            "cutover event digest mismatch",
        )
        require(
            event["cutover_epoch"] == ledger["cutover_epoch"],
            "cutover epoch mismatch",
        )
        require(event["sequence"] == sequence, "cutover sequence mismatch")
        require(
            event["previous_event_digest"] == previous_digest,
            "cutover previous-event digest mismatch",
        )
        require(
            event["occurred_at_unix_ms"] >= previous_time,
            "cutover event timestamp regression",
        )
        previous_time = event["occurred_at_unix_ms"]
        states.append(event["state"])
        previous_digest = document["event_digest"]
    allowed_paths = {
        ("prepared", "candidate_active", "rolled_back_before_write"),
        (
            "prepared",
            "candidate_active",
            "post_snapshot_write",
            "retired_forward",
        ),
    }
    require(tuple(states) in allowed_paths, f"illegal cutover state path: {states}")
    require(
        ledger["head_event_digest"] == previous_digest,
        "cutover head-event digest mismatch",
    )


def validate_cutover_authority_fixtures() -> None:
    schema = load_json(CONTRACT_DIR / "aar-migration-cutover-v1.schema.json")
    attestation = load_json(FIXTURE_DIR / "valid-migration-attestation.json")
    ledger = load_json(FIXTURE_DIR / "valid-cutover-forward-ledger.json")
    semantic_invalid = load_json(FIXTURE_DIR / "invalid-cutover-semantic-rollback.json")
    for definition, value in (
        ("MigrationAttestationDocument", attestation),
        ("CutoverAuthorityLedger", ledger),
        ("CutoverAuthorityLedger", semantic_invalid),
    ):
        errors = list(Draft202012Validator(schema["$defs"][definition]).iter_errors(value))
        require(
            not errors,
            f"cutover fixture schema invalid: {definition}: {errors[0].message if errors else ''}",
        )
    require(
        attestation["attestation_digest"] == digest_value(attestation["attestation"]),
        "migration attestation digest mismatch",
    )
    require(
        attestation["attestation"]["completed_at_unix_ms"]
        >= attestation["attestation"]["started_at_unix_ms"],
        "migration attestation timestamp regression",
    )
    validate_cutover_ledger_semantics(ledger)
    rejected = False
    try:
        validate_cutover_ledger_semantics(semantic_invalid)
    except ValueError:
        rejected = True
    require(rejected, "semantic post-write rollback fixture was accepted")


def validate_documents() -> None:
    documents = (README_PATH, CONTRACTS_PATH, LIFECYCLE_PATH, MIGRATION_PATH, HANDOFF_PATH)
    texts: list[str] = []
    for path in documents:
        text = path.read_text(encoding="utf-8")
        texts.append(text)
        require(text.count("```") % 2 == 0, f"unbalanced code fences: {path.name}")
        trailing = [
            index
            for index, line in enumerate(text.splitlines(), start=1)
            if line.endswith((" ", "\t"))
        ]
        require(not trailing, f"trailing whitespace in {path.name}: {trailing[:8]}")
    joined = "\n".join(texts)
    for token in (
        "aar_rlm_workbench_execute",
        "aar_broker_work_mark_send_started",
        "waiting_external",
        "aar.workspace-broker-frame.v1",
        "aar.caller-work-ticket.v1",
        "aar.artifact-binding.v1",
        "recovery planner",
        "final delivery",
        "candidate receipt",
        "sent_request_digest",
        "EnvironmentManifest",
        "SourceManifest",
        "WheelBuildManifest",
        "portable_regex.py",
        "aar.envelope.v1",
        "migration-v6.sql",
        "migration_v6_attestations",
        "aar-migration-cutover-v1.schema.json",
        "post_snapshot_write",
        "rolled_back_before_write",
        "SQLite backup API",
        "RuntimeResourceOwner",
    ):
        require(token.lower() in joined.lower(), f"SDD missing normative token: {token}")
    require("no partial" in joined.lower(), "SDD must forbid partial product claims")
    require(
        "installed" in texts[-1].lower() and "read-only" in texts[-1].lower(),
        "handoff must protect installed source",
    )
    public_text = "\n".join(
        [
            joined,
            DEFECT_PATH.read_text(encoding="utf-8"),
            ACCEPTANCE_PATH.read_text(encoding="utf-8"),
            FAULT_PATH.read_text(encoding="utf-8"),
        ]
    )
    for pattern in SENSITIVE_PATH_PATTERNS:
        match = pattern.search(public_text)
        offending = match.group(0) if match else ""
        require(match is None, f"public SDD contains local absolute path: {offending}")


def validate_results(
    results_path: Path,
    review_trust_path: Path | None,
    rows: dict[str, dict[str, Any]],
    faults: dict[str, dict[str, Any]],
    contract_manifest: dict[str, Any],
) -> str:
    results = load_json(results_path)
    evidence_schema = load_json(EVIDENCE_SCHEMA_PATH)
    result_errors = list(Draft202012Validator(evidence_schema).iter_errors(results))
    require(
        not result_errors,
        f"acceptance results schema invalid: {result_errors[0].message if result_errors else ''}",
    )
    if review_trust_path is None:
        raise ValueError("acceptance results require --review-trust")
    review_trust = load_json(review_trust_path)
    trust_errors = list(
        Draft202012Validator(evidence_schema["$defs"]["ReviewTrustStore"]).iter_errors(review_trust)
    )
    require(
        not trust_errors,
        f"review trust store invalid: {trust_errors[0].message if trust_errors else ''}",
    )
    trusted_key_digests: dict[tuple[str, str], str] = {}
    for key in review_trust["keys"]:
        identity = (key["reviewer_principal_id"], key["reviewer_key_id"])
        require(identity not in trusted_key_digests, f"duplicate review trust key: {identity}")
        trusted_key_digests[identity] = key["public_key_digest"]
    candidate = results["candidate"]
    require(candidate["package_version"] == "0.5.0a0", "candidate package version mismatch")
    require(
        candidate["manifest_digest"]
        == digest_value(
            {key: value for key, value in candidate.items() if key != "manifest_digest"}
        ),
        "candidate manifest digest mismatch",
    )
    require(
        candidate["contract_manifest_digest"] == contract_manifest["manifest_digest"],
        "candidate contract manifest digest mismatch",
    )
    combined_surface = load_json(CONTRACT_DIR / "aar-mcp-tools-v8-combined.json")
    require(
        candidate["tool_surface_digest"] == combined_surface["tool_surface_digest"],
        "candidate tool surface digest mismatch",
    )
    version_lines = {".".join(version.split(".")[:2]) for version in candidate["python_versions"]}
    require(version_lines == {"3.11", "3.12", "3.13", "3.14"}, "candidate Python matrix incomplete")
    require(
        "linux-x86_64" in candidate["platforms"], "candidate lacks Linux x86_64 reference platform"
    )
    require(
        results["matrix_digest"] == digest_file(ACCEPTANCE_PATH), "results matrix digest mismatch"
    )
    require(
        results["fault_matrix_digest"] == digest_file(FAULT_PATH), "results fault digest mismatch"
    )

    results_root = results_path.resolve().parent
    validated_files: dict[tuple[str, int, str], Path] = {}
    semantic_documents: dict[tuple[str, str], dict[str, Any]] = {}

    def validate_file(binding: dict[str, Any], owner: str) -> Path:
        cache_key = (
            binding["relative_path"],
            binding["size_bytes"],
            binding["sha256"],
        )
        cached = validated_files.get(cache_key)
        if cached is not None:
            return cached
        relative_path = Path(binding["relative_path"])
        require(
            not relative_path.is_absolute() and ".." not in relative_path.parts,
            f"{owner}: unsafe evidence path",
        )
        path = (results_root / relative_path).resolve()
        require(
            path == results_root or results_root in path.parents,
            f"{owner}: evidence path escapes root",
        )
        require(path.is_file(), f"{owner}: evidence file missing: {relative_path.as_posix()}")
        require(binding["size_bytes"] == path.stat().st_size, f"{owner}: evidence size mismatch")
        require(binding["sha256"] == digest_file(path), f"{owner}: evidence digest mismatch")
        validated_files[cache_key] = path
        return path

    def validate_semantic_document(
        binding: dict[str, Any],
        definition: str,
        owner: str,
    ) -> tuple[Path, dict[str, Any]]:
        path = validate_file(binding, owner)
        cache_key = (binding["sha256"], definition)
        cached = semantic_documents.get(cache_key)
        if cached is not None:
            return path, cached
        document = load_json(path)
        schema = evidence_schema["$defs"][definition]
        errors = list(Draft202012Validator(schema).iter_errors(document))
        require(
            not errors,
            f"{owner}: {definition} invalid: {errors[0].message if errors else ''}",
        )
        semantic_documents[cache_key] = document
        return path, document

    review_public_keys: dict[tuple[str, str], Ed25519PublicKey] = {}
    for key in results["review_keys"]:
        identity = (key["reviewer_principal_id"], key["reviewer_key_id"])
        require(identity not in review_public_keys, f"duplicate result review key: {identity}")
        require(
            key["public_key"]["kind"] == "review_public_key",
            f"review key file kind mismatch: {identity}",
        )
        public_key_path = validate_file(key["public_key"], f"review key {identity}")
        public_key_bytes = public_key_path.read_bytes()
        require(
            len(public_key_bytes) == 32
            and key["public_key_digest"] == digest_file(public_key_path),
            f"review public key bytes/digest invalid: {identity}",
        )
        require(
            trusted_key_digests.get(identity) == key["public_key_digest"],
            f"review key is not trusted: {identity}",
        )
        try:
            review_public_keys[identity] = Ed25519PublicKey.from_public_bytes(public_key_bytes)
        except ValueError as error:
            raise ValueError(f"review public key invalid: {identity}") from error

    artifacts_by_kind: dict[str, list[dict[str, Any]]] = {}
    for binding in candidate["artifacts"]:
        validate_file(binding, "candidate")
        artifacts_by_kind.setdefault(binding["kind"], []).append(binding)
    single_artifact_kinds = (
        "source_archive",
        "source_manifest",
        "wheel",
        "wheel_build_manifest",
        "tool_surface",
        "test_bundle",
    )
    for required_kind in single_artifact_kinds:
        require(
            len(artifacts_by_kind.get(required_kind, [])) == 1,
            f"candidate requires exactly one {required_kind} artifact",
        )
    for required_kind in ("profile", "skill"):
        require(
            bool(artifacts_by_kind.get(required_kind)),
            f"candidate artifact kind missing: {required_kind}",
        )
    source_archive_binding = artifacts_by_kind["source_archive"][0]
    source_manifest_binding = artifacts_by_kind["source_manifest"][0]
    wheel_binding = artifacts_by_kind["wheel"][0]
    wheel_build_binding = artifacts_by_kind["wheel_build_manifest"][0]
    source_archive_path = validate_file(source_archive_binding, "candidate source archive")
    _, source_document = validate_semantic_document(
        source_manifest_binding,
        "SourceManifest",
        "candidate source manifest",
    )
    require(
        source_document["manifest_digest"]
        == digest_value(
            {key: value for key, value in source_document.items() if key != "manifest_digest"}
        ),
        "source manifest digest mismatch",
    )
    require(
        source_document["candidate_id"] == candidate["candidate_id"],
        "source manifest candidate mismatch",
    )
    require(
        source_document["source_commit"] == candidate["source_commit"],
        "candidate source commit is not source-manifest-bound",
    )
    require(
        source_document["source_tree_digest"] == candidate["source_tree_digest"],
        "candidate source tree is not source-manifest-bound",
    )
    require(
        source_document["source_archive_digest"] == source_archive_binding["sha256"]
        and source_document["source_archive_size_bytes"] == source_archive_binding["size_bytes"],
        "source manifest archive binding mismatch",
    )
    source_entries = source_document["entries"]
    source_paths = [entry["relative_path"] for entry in source_entries]
    require(source_paths == sorted(source_paths), "source manifest entries are not canonical")
    require(len(source_paths) == len(set(source_paths)), "source manifest contains duplicate paths")
    archive_entries: list[dict[str, Any]] = []
    with tarfile.open(source_archive_path, mode="r:*") as source_archive:
        for member in source_archive.getmembers():
            member_path = Path(member.name)
            require(
                not member_path.is_absolute() and ".." not in member_path.parts,
                "source archive contains unsafe path",
            )
            if member.isdir():
                continue
            require(member.isreg(), "source archive contains non-regular entry")
            extracted = source_archive.extractfile(member)
            require(extracted is not None, "source archive member is unreadable")
            member_hash = hashlib.sha256()
            observed_size = 0
            while chunk := extracted.read(1024 * 1024):
                observed_size += len(chunk)
                member_hash.update(chunk)
            require(observed_size == member.size, "source archive member size mismatch")
            archive_entries.append(
                {
                    "relative_path": member_path.as_posix(),
                    "sha256": f"sha256:{member_hash.hexdigest()}",
                    "size_bytes": observed_size,
                    "executable": bool(member.mode & 0o111),
                }
            )
    archive_entries.sort(key=lambda entry: entry["relative_path"])
    require(archive_entries == source_entries, "source archive inventory mismatch")
    require(
        source_document["source_tree_digest"]
        == digest_value({"schema_version": "aar.source-tree.v1", "entries": source_entries}),
        "source tree digest mismatch",
    )
    _, wheel_build_document = validate_semantic_document(
        wheel_build_binding,
        "WheelBuildManifest",
        "candidate wheel build manifest",
    )
    require(
        wheel_build_document["manifest_digest"]
        == digest_value(
            {key: value for key, value in wheel_build_document.items() if key != "manifest_digest"}
        ),
        "wheel build manifest digest mismatch",
    )
    require(
        wheel_build_document["candidate_id"] == candidate["candidate_id"],
        "wheel build candidate mismatch",
    )
    require(
        wheel_build_document["source_manifest_file_digest"] == source_manifest_binding["sha256"]
        and wheel_build_document["source_manifest_digest"] == source_document["manifest_digest"],
        "wheel build source manifest binding mismatch",
    )
    for field in ("source_commit", "source_tree_digest", "source_archive_digest"):
        require(
            wheel_build_document[field] == source_document[field],
            f"wheel build source provenance mismatch: {field}",
        )
    require(
        wheel_build_document["wheel_digest"]
        == wheel_binding["sha256"]
        == candidate["wheel_digest"],
        "wheel digest not provenance-bound",
    )
    require(
        wheel_build_document["build_command"] == candidate["build_command"],
        "wheel build command mismatch",
    )
    require(
        load_json(
            (results_root / artifacts_by_kind["tool_surface"][0]["relative_path"]).resolve()
        ).get("tool_surface_digest")
        == candidate["tool_surface_digest"],
        "tool surface digest not bound to candidate file",
    )
    require(
        set(candidate["profile_digests"])
        <= {item["sha256"] for item in artifacts_by_kind["profile"]},
        "profile digests not file-bound",
    )
    require(
        set(candidate["skill_digests"]) <= {item["sha256"] for item in artifacts_by_kind["skill"]},
        "skill digests not file-bound",
    )

    evidence = results["evidence"]
    evidence_ids = [item["evidence_id"] for item in evidence]
    require(len(evidence_ids) == len(set(evidence_ids)), "duplicate evidence id")
    obligation_identities = [
        (item["acceptance_id"], item.get("fault_case_id")) for item in evidence
    ]
    require(
        len(obligation_identities) == len(set(obligation_identities)),
        "conflicting or duplicate evidence for one obligation",
    )
    altitude_rank = {"T0": 0, "T1": 1, "T2": 2, "T3": 3, "T4": 4}
    semantic_authorizations: set[str] = set()
    for item in evidence:
        acceptance_id = item["acceptance_id"]
        require(
            acceptance_id in rows, f"evidence references unknown acceptance row: {acceptance_id}"
        )
        require(
            item["candidate_id"] == candidate["candidate_id"],
            f"evidence candidate mismatch: {item['evidence_id']}",
        )
        require(
            altitude_rank[item["tier"]] >= altitude_rank[rows[acceptance_id]["altitude"]],
            f"evidence tier too low: {item['evidence_id']}",
        )
        fault_case_id = item.get("fault_case_id")
        if fault_case_id is not None:
            require(
                fault_case_id in faults, f"evidence references unknown fault case: {fault_case_id}"
            )
            require(
                faults[fault_case_id]["acceptance_id"] == acceptance_id,
                f"fault/acceptance mismatch: {item['evidence_id']}",
            )
        if item["observed_outcome"] == "pass":
            require(
                item["exit_code"] == 0, f"passing evidence has non-zero exit: {item['evidence_id']}"
            )
        files_by_kind: dict[str, list[dict[str, Any]]] = {}
        for binding in item["files"]:
            validate_file(binding, item["evidence_id"])
            files_by_kind.setdefault(binding["kind"], []).append(binding)
        for required_kind in ("environment", "test_bundle", "fixture_bundle", "stdout"):
            require(
                len(files_by_kind.get(required_kind, [])) == 1,
                f"{item['evidence_id']}: expected one {required_kind} file",
            )
        environment_binding = files_by_kind["environment"][0]
        test_binding = files_by_kind["test_bundle"][0]
        fixture_binding = files_by_kind["fixture_bundle"][0]
        stdout_binding = files_by_kind["stdout"][0]
        require(
            item["environment_digest"] == environment_binding["sha256"],
            f"{item['evidence_id']}: environment digest not file-bound",
        )
        require(
            item["test_or_probe_digest"] == test_binding["sha256"],
            f"{item['evidence_id']}: test/probe digest not file-bound",
        )
        require(
            item["fixture_digest"] == fixture_binding["sha256"],
            f"{item['evidence_id']}: fixture digest not file-bound",
        )
        require(
            item["stdout_digest"] == stdout_binding["sha256"],
            f"{item['evidence_id']}: stdout digest not file-bound",
        )
        _, environment = validate_semantic_document(
            environment_binding,
            "EnvironmentManifest",
            item["evidence_id"],
        )
        require(
            environment["candidate_id"] == candidate["candidate_id"]
            and environment["source_commit"] == candidate["source_commit"]
            and environment["package_version"] == candidate["package_version"]
            and environment["tool_surface_digest"] == candidate["tool_surface_digest"],
            f"{item['evidence_id']}: environment/candidate binding mismatch",
        )
        require(
            environment["python_version"].split(".")[:2]
            in [version.split(".")[:2] for version in candidate["python_versions"]],
            f"{item['evidence_id']}: environment Python version outside candidate matrix",
        )
        require(
            environment["platform"] in candidate["platforms"],
            f"{item['evidence_id']}: environment platform outside candidate matrix",
        )
        _, fixture_bundle = validate_semantic_document(
            fixture_binding,
            "FixtureBundleManifest",
            item["evidence_id"],
        )
        require(
            fixture_bundle["candidate_id"] == candidate["candidate_id"],
            f"{item['evidence_id']}: fixture/candidate binding mismatch",
        )
        require(
            fixture_bundle["bundle_digest"]
            == digest_value(
                {key: value for key, value in fixture_bundle.items() if key != "bundle_digest"}
            ),
            f"{item['evidence_id']}: fixture bundle digest mismatch",
        )
        for fixture in fixture_bundle["entries"]:
            fixture_path = (results_root / fixture["relative_path"]).resolve()
            require(
                results_root in fixture_path.parents and fixture_path.is_file(),
                f"{item['evidence_id']}: fixture entry missing or unsafe",
            )
            require(
                fixture["size_bytes"] == fixture_path.stat().st_size
                and fixture["sha256"] == digest_file(fixture_path),
                f"{item['evidence_id']}: fixture entry digest mismatch",
            )
        receipt_kinds: set[str] = set()
        semantic_receipt_digests: list[str] = []
        for receipt in item["semantic_receipts"]:
            require(
                receipt["file"]["kind"] == "raw_receipt",
                f"semantic receipt file kind mismatch: {item['evidence_id']}",
            )
            receipt_path, receipt_document = validate_semantic_document(
                receipt["file"],
                "SemanticReceiptDocument",
                item["evidence_id"],
            )
            require(
                receipt["receipt_digest"] == digest_file(receipt_path),
                f"semantic receipt digest mismatch: {item['evidence_id']}",
            )
            require(
                receipt_document["kind"] == receipt["kind"]
                and receipt_document["candidate_id"] == candidate["candidate_id"],
                f"semantic receipt content mismatch: {item['evidence_id']}",
            )
            receipt_kinds.add(receipt["kind"])
            semantic_receipt_digests.append(receipt["receipt_digest"])
            if receipt["kind"] == "authorization":
                semantic_authorizations.add(receipt["receipt_digest"])
        require(
            len(semantic_receipt_digests) == len(set(semantic_receipt_digests))
            and sorted(item["receipt_digests"]) == sorted(semantic_receipt_digests),
            f"semantic receipt list mismatch: {item['evidence_id']}",
        )
        if (
            item["observed_outcome"] == "pass"
            and altitude_rank[item["tier"]] >= altitude_rank["T3"]
        ):
            require(
                bool(receipt_kinds & {"host", "reconciler"}),
                f"T3+ evidence lacks host/reconciler receipt: {item['evidence_id']}",
            )
        if item["observed_outcome"] == "pass" and item["tier"] == "T4":
            require(
                {"host", "model_route", "subagent", "authorization"} <= receipt_kinds,
                f"T4 evidence lacks route/subagent/authorization receipts: {item['evidence_id']}",
            )
        signature_path = validate_file(item["review_signature"], item["evidence_id"])
        require(
            item["review_signature"]["kind"] == "review_signature",
            "review signature kind mismatch",
        )
        signature_bytes = signature_path.read_bytes()
        require(
            len(signature_bytes) == 64
            and item["review_signature_digest"] == digest_file(signature_path),
            "review signature bytes/digest mismatch",
        )
        reviewer_identity = (
            item["reviewer_principal_id"],
            item["reviewer_key_id"],
        )
        review_public_key = review_public_keys.get(reviewer_identity)
        if review_public_key is None:
            raise ValueError(f"review signer is not trusted: {reviewer_identity}")
        signed_item = {
            key: value
            for key, value in item.items()
            if key not in {"review_signature", "review_signature_digest"}
        }
        signature_payload = canonical_bytes(
            {
                "schema_version": "aar.acceptance-review-signature-payload.v1",
                "candidate_manifest_digest": candidate["manifest_digest"],
                "evidence_item": signed_item,
            }
        )
        try:
            review_public_key.verify(signature_bytes, signature_payload)
        except InvalidSignature as error:
            raise ValueError(f"review signature invalid: {item['evidence_id']}") from error

    accepted_rows = {
        item["acceptance_id"]
        for item in evidence
        if item.get("fault_case_id") is None
        and item["observed_outcome"] == "pass"
        and item["reviewer_disposition"] == "accepted"
    }
    accepted_faults = {
        item["fault_case_id"]
        for item in evidence
        if item.get("fault_case_id") is not None
        and item["observed_outcome"] == "pass"
        and item["reviewer_disposition"] == "accepted"
    }
    implementation_rows = {
        row_id for row_id, row in rows.items() if row["required_for_implementation_verified"]
    }
    live_rows = {row_id for row_id, row in rows.items() if row["required_for_live_qualified"]}
    implementation_faults = {
        case_id
        for case_id, case in faults.items()
        if rows[case["acceptance_id"]]["required_for_implementation_verified"]
    }
    live_faults = {
        case_id
        for case_id, case in faults.items()
        if rows[case["acceptance_id"]]["required_for_live_qualified"]
    }
    implementation_ok = (
        implementation_rows <= accepted_rows and implementation_faults <= accepted_faults
    )
    live_ok = implementation_ok and live_rows <= accepted_rows and live_faults <= accepted_faults
    if results["claim_status"] in {"implementation_verified", "live_qualified"}:
        missing_rows = sorted(implementation_rows - accepted_rows)
        missing_faults = sorted(implementation_faults - accepted_faults)
        require(
            not missing_rows,
            f"implementation claim lacks direct row evidence: {missing_rows}",
        )
        require(
            not missing_faults,
            f"implementation claim lacks required fault evidence: {missing_faults}",
        )
    if live_ok:
        require(
            bool(results["authorization_receipt_digests"]),
            "live qualification lacks authorization receipt",
        )
        require(
            set(results["authorization_receipt_digests"]) <= semantic_authorizations,
            "authorization digests lack file-bound semantic receipts",
        )
    expected = (
        "live_qualified"
        if live_ok
        else "implementation_verified"
        if implementation_ok
        else "not_verified"
    )
    require(
        results["claim_status"] == expected,
        f"claim status {results['claim_status']} must be {expected}",
    )
    require(
        results["results_digest"]
        == digest_value({key: value for key, value in results.items() if key != "results_digest"}),
        "results digest mismatch",
    )
    return expected.upper()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results", type=Path)
    parser.add_argument("--review-trust", type=Path)
    args = parser.parse_args()
    defects, refs = validate_defects(load_json(DEFECT_PATH))
    rows = validate_acceptance(load_json(ACCEPTANCE_PATH), defects)
    validate_cross_links(defects, refs, rows)
    faults = validate_fault_matrix(load_json(FAULT_PATH), rows)
    manifest = validate_contract_manifest()
    package_manifest = validate_package_manifest(manifest["manifest_digest"])
    validate_schema_profile()
    validate_negative_contract_fixtures()
    validate_broker_capabilities()
    validate_tool_surface()
    validate_migration_sql()
    validate_cutover_authority_fixtures()
    validate_documents()
    implementation_rows = sum(
        1 for row in rows.values() if row["required_for_implementation_verified"]
    )
    live_rows = sum(1 for row in rows.values() if row["required_for_live_qualified"])
    status = (
        validate_results(args.results, args.review_trust, rows, faults, manifest)
        if args.results
        else "STRUCTURALLY_VALID"
    )
    print(
        json.dumps(
            {
                "status": status,
                "defect_count": len(defects),
                "acceptance_row_count": len(rows),
                "implementation_required_rows": implementation_rows,
                "live_required_rows": live_rows,
                "required_fault_cases": len(faults),
                "contract_manifest_digest": manifest["manifest_digest"],
                "sdd_package_manifest_digest": package_manifest["manifest_digest"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError, SchemaError) as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1) from None
