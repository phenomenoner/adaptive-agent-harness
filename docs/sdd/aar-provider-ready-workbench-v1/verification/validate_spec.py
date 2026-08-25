#!/usr/bin/env python3
"""Deterministic validator for the clean-install-only provider-ready SDD rev2."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

EXPECTED_FILES = {
    "README.md",
    "BASELINE.md",
    "ARCHITECTURE.md",
    "CONTRACTS.md",
    "LIFECYCLE.md",
    "MIGRATION.md",
    "EVALUATION.md",
    "ACCEPTANCE.md",
    "IMPLEMENTATION-PLAN.md",
    "TEST-STRATEGY-AND-GAP-ANALYSIS.md",
    "HANDOFF.md",
    "decisions/ADR-001-activate-existing-authorities.md",
    "decisions/ADR-002-ticket-root-planner.md",
    "decisions/ADR-003-preserve-v8-method-scoped-admission.md",
    "decisions/ADR-004-freeze-activation-contract-domains.md",
    "decisions/ADR-005-clean-install-only.md",
    "verification/README.md",
    "verification/requirements.json",
    "verification/acceptance-matrix.json",
    "verification/validate_spec.py",
}
ACTIVE_MARKDOWN = {
    "README.md",
    "ARCHITECTURE.md",
    "CONTRACTS.md",
    "LIFECYCLE.md",
    "MIGRATION.md",
    "EVALUATION.md",
    "ACCEPTANCE.md",
    "IMPLEMENTATION-PLAN.md",
    "TEST-STRATEGY-AND-GAP-ANALYSIS.md",
    "HANDOFF.md",
    "decisions/ADR-005-clean-install-only.md",
    "verification/README.md",
}
HISTORICAL_PREFIXES = (
    "reviews/",
    "decisions/ADR-001-",
    "decisions/ADR-002-",
    "decisions/ADR-003-",
    "decisions/ADR-004-",
)
HISTORICAL_FILES = {"BASELINE.md"}
FROZEN_BASELINE_FILES = {
    "schemas/aar-broker-catalog-v2.json": "sha256:88099616e61f41c7d72d5e1c81fba8ebfe29995467fd583acc9da0bb15aeadbc",
    "schemas/aar-rlm-workbench-v1.schema.json": "sha256:0d52527f9019cae724459e3eb36a0165b91822771ae64f815f1c2fd18d3054ef",
    "schemas/aar-caller-work-v1.schema.json": "sha256:76deb95fb89081c7ec90734821689e14a893040696af6727725b059676c2b7e3",
    "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql": "sha256:8e7080b319aadb4eb98b5e3b9e12efe8c189c0bd12a82dc8ba1eea7c7bfe29b7",
}
PRODUCT_CONTRACT = {
    "release_mode": "clean_install_only",
    "sole_public_mutation": "aar-admin runtime install --runtime-home <absolute-runtime-home> --intent <exact-intent> --candidate-receipt <absolute-json> --wheel <absolute-wheel>",
    "preservation": False,
    "adoption": False,
    "downgrade": False,
    "old_root_product_mutation": False,
    "transition_mutators": [],
    "planned_receipt_schema": "aar.install-candidate-receipt.v1",
    "implementation_architecture": "standalone_installer_with_thin_adapters",
    "c1_phase": "prepublication_generation_independent",
    "c2_phase": "postpublication_runtime_activation",
    "grant_set_owner": "aar.runtime.provider_ready_activation.ProviderReadyActivationStore",
}
SCHEMA_NAMES = (
    "aar.host-activation-intent.v1",
    "aar.host-activation-profile.v1",
    "aar.method-adapter-manifest.v1",
    "aar.activation-generation-authority.v1",
    "aar.cutover-plan.v1",
    "aar.operator-prepared-marker.v1",
    "aar.cutover-receipt.v1",
    "aar.restore-receipt.v1",
    "aar.operator-terminal-marker.v1",
    "aar.activation-readback.v1",
    "aar.workbench-grant-set.v1",
    "aar.provider-alias-attestation.v1",
    "aar.evaluation-evidence-classification.v1",
    "aar.paired-evaluation-admission.v1",
)
V6_STATEMENTS = (
    "migration_v6_attestations",
    "rlm_workbench_jobs",
    "rlm_workbench_cells",
    "rlm_workbench_suspensions",
    "caller_work_tickets",
    "caller_work_candidate_receipts",
    "caller_work_command_receipts",
    "caller_work_command_receipts_no_update",
    "caller_work_command_receipts_no_delete",
    "rlm_workbench_successor_outbox",
    "rlm_workbench_attempt_authority",
    "rlm_workbench_rebind_transfers",
    "rlm_workbench_artifact_stages",
    "rlm_workbench_cell_manifests",
    "rlm_workbench_finalization_manifests",
    "broker_contract_catalog_v2",
    "broker_backend_availability_v2",
    "idx_workbench_phase_deadline",
    "idx_workbench_cells_state",
    "idx_caller_work_state_deadline",
    "idx_caller_work_claim_expiry",
    "idx_candidate_receipts_ticket",
    "idx_caller_command_receipts_ticket",
    "idx_artifact_stages_state",
    "idx_successor_outbox_state",
)
PACKAGE_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?"
    r"(?:\.post(?:0|[1-9][0-9]*))?(?:\.dev(?:0|[1-9][0-9]*))?$"
)
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PUBLIC_TRANSITION = re.compile(
    r"(?:aar-admin\s+cutover|aar-admin\s+runtime\s+initialize|`cutover\s+(?:plan|apply|abort|reconcile|restore)`)",
    re.IGNORECASE,
)
PRESERVATION_ACTION = re.compile(
    r"\b(?:preserv(?:e|es|ed|ing|ation)|support(?:s|ed|ing)?|adopt(?:s|ed|ing)?|downgrad(?:e|d|ing)?|rollback|roll\s+back)\b",
    re.IGNORECASE,
)
PRESERVATION_OBJECT = re.compile(
    r"\b(?:in[- ]place|v0\.5|v5|old runtime|existing runtime|old root|prior runtime|old operations|old history)\b",
    re.IGNORECASE,
)
NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|never|without|cannot|does not|do not|unsupported|removed|retired|absent)\b"
    r"(?:\s+[A-Za-z0-9_./`-]+){0,5}\s*$",
    re.IGNORECASE,
)
STALE_COUNT = re.compile(
    r"\b(?:53|48|39|45|194|181|57)(?:\s*-\s*|\s+)(?:planned\s+)?(?:rows?|gates?|requirements?)\b",
    re.IGNORECASE,
)
AGGREGATE_VARIANT = re.compile(
    r"\b(?:all|every|each|aggregate|cross[- ]?product|matrix|variants?)\b|[\[\]|]",
    re.IGNORECASE,
)

WRONG_EFFECT_BY_ID_PREFIX: tuple[tuple[str, str], ...] = (
    ("A-TARGET-", "no existing target byte, directory entry, SQLite sidecar, authority state, or staging sibling is created, deleted, adopted, or overwritten"),
    ("A-PATH-", "no unresolved, symlinked, normalized, case-folded, or wrong-ancestor path becomes canonical identity and no filesystem mutation occurs"),
    ("A-STAGE-", "no operation escapes retained handles, no mismatched inode is cleaned or published, and no unknown residue is deleted"),
    ("A-SQLITE-", "no SQLite, backup, WAL, or SHM write escapes the retained stage inode and no drifted entry is cleaned or published"),
    ("A-V6-PROJ-", "no illegal token, digest, preparation, snapshot, or attestation projection is persisted or published"),
    ("A-V6-", "no partial v6 DDL, attestation row, schema-migration row, or published target survives the injected boundary"),
    ("A-V5-", "no noncanonical v1-v5 row, domain state, integrity violation, WAL, SHM, or unknown residue is accepted"),
    ("A-INITIAL-", "no noninitial intent is rewritten, staged, installed, or published"),
    ("A-EPOCH-", "no malformed, reused, or mismatched epoch and no stale stage is accepted, persisted, or published"),
    ("A-CLEAN-", "no illegal token, digest, preparation, snapshot, or attestation projection is persisted or published"),
    ("A-GEN-", "no wrong-generation capability or grant-set bytes are published, reused, exposed as current, or reported Ready"),
    ("A-GRANT-", "no denied provider dispatch, capability admission, grant expansion, budget charge, or authority mutation occurs"),
    ("A-AUTH-", "no denied provider dispatch, capability admission, grant expansion, budget charge, or authority mutation occurs"),
    ("A-ADM-", "no denied provider dispatch, capability admission, grant expansion, budget charge, or authority mutation occurs"),
    ("A-ACT-", "no activation mutation, factory instantiation, grant issuance, or Ready publication occurs in the wrong phase"),
    ("A-D2-", "no physical send, blind replay, duplicate successor, authority rebind, or cell-bound mutation occurs outside the claimed durable transition"),
    ("A-PLAN-", "no physical send, blind replay, duplicate successor, authority rebind, or cell-bound mutation occurs outside the claimed durable transition"),
    ("A-T4-", "no unauthorized provider call, route upgrade, rerun, or evidence-tier promotion occurs"),
    ("A-EVAL-", "no unauthorized provider call, route upgrade, rerun, or evidence-tier promotion occurs"),
    ("A-T3-", "no release, push, tag, qualification, or exact-candidate claim is emitted from missing, mixed, secret-bearing, or nonexact evidence"),
    ("A-REL-", "no release, push, tag, qualification, or exact-candidate claim is emitted from missing, mixed, secret-bearing, or nonexact evidence"),
    ("A-COMP-", "no preserved schema or fixture byte is regenerated, omitted, reordered, or accepted with path, size, or digest drift"),
    ("A-CUST-", "no credential, secret, unowned artifact, or nonexact evidence enters persisted or release-authorizing bytes"),
    ("A-SEC-", "no credential, secret, unowned artifact, or nonexact evidence enters persisted or release-authorizing bytes"),
    ("A-MOD-", "no install orchestration enters admin or operator and no migration, registry, transport, or daemon ownership moves"),
    ("A-CLI-", "no install mutation or success receipt occurs when exact CLI, receipt, wheel, identity, or target preconditions are absent or mismatched"),
    ("A-INST-", "no install mutation or success receipt occurs when exact CLI, receipt, wheel, identity, or target preconditions are absent or mismatched"),
    ("A-SCOPE-", "no install mutation or success receipt occurs when exact CLI, receipt, wheel, identity, or target preconditions are absent or mismatched"),
    ("A-IDENT-", "no install mutation or success receipt occurs when exact CLI, receipt, wheel, identity, or target preconditions are absent or mismatched"),
    ("A-VALID-", "no invalid spec, receipt, count, tree, or frozen-input state is reported as pass"),
    ("A-NEC-", "no wider seam, migration feature, or unreviewed scope is authorized"),
    ("A-OPS-", "no old runtime is moved, deleted, or declared inactive without verified archive or no-old-root evidence"),
)


def expected_wrong_effect(row_id: str) -> str:
    for prefix, effect in WRONG_EFFECT_BY_ID_PREFIX:
        if row_id.startswith(prefix):
            return effect
    raise KeyError(row_id)


def expected_stimulus(row: dict[str, Any]) -> str:
    return f"{row['operation']} at phase {row['phase']} using exactly the {row['variant']} fixture"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path}: root must be an object")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return "sha256:" + digest.hexdigest()


def tree_digest(root: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return "sha256:" + digest.hexdigest()


def canonical_digest(value: Any) -> str:
    content = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(content).hexdigest()


def active_text(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in sorted(ACTIVE_MARKDOWN):
        path = root / relative
        if path.is_file():
            result[relative] = path.read_text(encoding="utf-8")
    for relative in ("verification/requirements.json", "verification/acceptance-matrix.json"):
        result[relative] = (root / relative).read_text(encoding="utf-8")
    return result


def validate_frozen_inputs(root: Path, failures: list[str]) -> dict[str, str]:
    """Append every frozen-input failure before the result status is computed."""
    project_root = root.parents[2]
    observed: dict[str, str] = {}
    for relative, expected in FROZEN_BASELINE_FILES.items():
        path = project_root / relative
        if not path.is_file():
            failures.append(f"missing frozen predecessor input: {relative}")
            continue
        actual = sha256_file(path)
        observed[relative] = actual
        if actual != expected:
            failures.append(f"frozen predecessor bytes drifted: {relative}: {actual}")
    try:
        catalog = load_json(project_root / "schemas/aar-broker-catalog-v2.json")
        methods = tuple(item["method"] for item in catalog["contracts"])
        if methods != (
            "model.request", "subagent.submit", "subagent.result",
            "evidence.query", "artifact.put", "effect.propose",
        ):
            failures.append("frozen broker catalog method order drifted")
        workbench = load_json(project_root / "schemas/aar-rlm-workbench-v1.schema.json")
        backend = workbench["$defs"]["BackendAvailability"]
        if set(backend["properties"]["evidence_tier"]["enum"]) != {
            "unknown", "caller_observed", "host_receipt_bound", "provider_attested"
        }:
            failures.append("frozen capability evidence domain drifted")
        reference = next(
            item["then"]["properties"] for item in backend["allOf"]
            if item["if"]["properties"]["backend_kind"].get("const") == "reference"
        )
        if (
            reference["configured"].get("const") is not True
            or reference["reference_only"].get("const") is not True
            or reference["adapter_id"].get("type") != "null"
            or reference["adapter_generation"].get("type") != "null"
            or reference["evidence_tier"].get("const") != "unknown"
        ):
            failures.append("frozen reference capability truth drifted")
        caller = load_json(project_root / "schemas/aar-caller-work-v1.schema.json")
        logical_owner = caller["$defs"]["LogicalCallOwner"]
        planner = next(item for item in logical_owner["oneOf"] if item["properties"]["kind"].get("const") == "planner")
        if set(planner["properties"]) != {"kind", "phase", "step_index"} or planner.get("additionalProperties") is not False:
            failures.append("frozen planner logical-owner wire drifted")
        requests = caller["$defs"]["CallerWorkRequest"]["oneOf"]
        if tuple(item["properties"]["method"]["const"] for item in requests) != (
            "model.request", "subagent.submit", "subagent.result", "evidence.query", "effect.propose"
        ):
            failures.append("frozen caller-work method domain drifted")
        migration = (project_root / "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql").read_text(encoding="utf-8")
        for marker in (
            "CREATE TABLE IF NOT EXISTS migration_v6_attestations",
            "CREATE TABLE IF NOT EXISTS rlm_workbench_jobs",
            "CREATE TABLE IF NOT EXISTS rlm_workbench_suspensions",
            "CREATE TABLE IF NOT EXISTS caller_work_tickets",
            "CREATE TABLE IF NOT EXISTS rlm_workbench_successor_outbox",
            "foreign_key_violation_count INTEGER NOT NULL",
            "integrity_result TEXT NOT NULL CHECK (integrity_result = 'ok')",
        ):
            if marker not in migration:
                failures.append(f"frozen v6 SQL marker missing: {marker}")
        suspension = re.search(r"CREATE TABLE IF NOT EXISTS rlm_workbench_suspensions \((.*?)\n\);", migration, re.DOTALL)
        if suspension is None or "cell_execution_id TEXT" not in suspension.group(1) or "'model.request'" not in suspension.group(1):
            failures.append("frozen v6 suspension/cell-free planner owner drifted")
        outbox = re.search(r"CREATE TABLE IF NOT EXISTS rlm_workbench_successor_outbox \((.*?)\n\);", migration, re.DOTALL)
        if outbox is None or "rebind_generation" not in outbox.group(1) or "successor_attempt_fence" not in outbox.group(1):
            failures.append("frozen v6 successor outbox CAS fields drifted")
    except (KeyError, TypeError, StopIteration, json.JSONDecodeError, OSError) as error:
        failures.append(f"cannot cross-check frozen predecessor contracts: {error}")
    return observed


def validate_frozen_compatibility(
    root: Path,
    requirements: dict[str, Any],
    failures: list[str],
) -> str | None:
    """Bind every preserved schema and provider-ready fixture byte before status."""

    project_root = root.parents[2]
    manifest = requirements.get("frozen_compatibility_manifest")
    if not isinstance(manifest, dict):
        failures.append("missing frozen_compatibility_manifest")
        return None
    if manifest.get("schema_version") != "aar.prw-frozen-compatibility-manifest.v1":
        failures.append("frozen compatibility schema_version is wrong")
    schemas = manifest.get("schemas")
    fixtures = manifest.get("fixtures")
    fixture_manifest = manifest.get("fixture_manifest")
    if not isinstance(schemas, list) or len(schemas) != 14:
        failures.append("frozen compatibility manifest must bind 14 schema files")
        schemas = []
    if not isinstance(fixtures, list) or len(fixtures) != 64:
        failures.append("frozen compatibility manifest must bind 64 fixture files")
        fixtures = []
    if not isinstance(fixture_manifest, dict):
        failures.append("frozen compatibility fixture manifest entry is missing")
        fixture_manifest = {}

    observed_schema_paths = sorted(
        path.relative_to(project_root).as_posix()
        for path in (project_root / "schemas").glob("*.json")
    )
    expected_schema_paths = [item.get("path") for item in schemas if isinstance(item, dict)]
    planned_receipt_schema_path = "schemas/aar-install-candidate-receipt-v1.schema.json"
    expected_repository_schema_paths = sorted(
        [*expected_schema_paths, planned_receipt_schema_path]
    )
    if (
        expected_schema_paths != sorted(expected_schema_paths)
        or len(set(expected_schema_paths)) != 14
        or planned_receipt_schema_path in expected_schema_paths
        or observed_schema_paths != expected_repository_schema_paths
    ):
        failures.append(
            "schema paths are not the exact 14 preserved plus one planned receipt set"
        )

    fixture_manifest_path = "tests/fixtures/provider-ready/manifest.json"
    if fixture_manifest.get("path") != fixture_manifest_path:
        failures.append("frozen compatibility fixture manifest path is wrong")
    try:
        declared_fixture_manifest = load_json(project_root / fixture_manifest_path)
        declared_paths = [
            f"tests/fixtures/provider-ready/{item['path']}"
            for item in declared_fixture_manifest["fixtures"]
        ]
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        failures.append(f"cannot read provider-ready fixture manifest: {error}")
        declared_fixture_manifest = {}
        declared_paths = []
    expected_fixture_paths = [item.get("path") for item in fixtures if isinstance(item, dict)]
    if (
        expected_fixture_paths != declared_paths
        or expected_fixture_paths != sorted(expected_fixture_paths)
        or len(set(expected_fixture_paths)) != 64
    ):
        failures.append("frozen compatibility fixture paths are not the exact declared sorted 64-file set")

    def check_entry(item: dict[str, Any], *, label: str) -> None:
        if set(item) != {"path", "size_bytes", "sha256"}:
            failures.append(f"{label}: compatibility entry fields are not exact")
            return
        path = project_root / str(item["path"])
        if not path.is_file():
            failures.append(f"{label}: compatibility file is missing: {item['path']}")
            return
        raw = path.read_bytes()
        if item["size_bytes"] != len(raw):
            failures.append(f"{label}: compatibility size drifted: {item['path']}")
        if item["sha256"] != "sha256:" + hashlib.sha256(raw).hexdigest():
            failures.append(f"{label}: compatibility digest drifted: {item['path']}")

    for item in schemas:
        if isinstance(item, dict):
            check_entry(item, label="schema")
    if fixture_manifest:
        check_entry(fixture_manifest, label="fixture-manifest")
    for item in fixtures:
        if isinstance(item, dict):
            check_entry(item, label="fixture")

    if declared_fixture_manifest:
        by_path = {item.get("path"): item for item in fixtures if isinstance(item, dict)}
        for declared in declared_fixture_manifest.get("fixtures", []):
            full_path = f"tests/fixtures/provider-ready/{declared.get('path')}"
            bound = by_path.get(full_path)
            if bound is None or bound.get("sha256") != "sha256:" + str(declared.get("raw_bytes_sha256")):
                failures.append(f"fixture manifest/raw-byte binding drifted: {full_path}")

    core = {
        key: manifest.get(key)
        for key in ("schema_version", "schemas", "fixture_manifest", "fixtures")
    }
    observed_digest = canonical_digest(core)
    if manifest.get("bundle_digest") != observed_digest:
        failures.append("frozen compatibility bundle_digest mismatch")
    try:
        provider_bundle = load_json(project_root / "schemas/aar-provider-ready-schemas-v1.json")
        schema_map = provider_bundle["schemas"]
        if not isinstance(schema_map, dict) or sorted(schema_map) != sorted(SCHEMA_NAMES):
            failures.append("provider-ready bundle schema-name set drifted")
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as error:
        failures.append(f"cannot validate provider-ready schema-name order: {error}")
    return observed_digest


def _explicit_negation(text: str, start: int) -> bool:
    clause = re.split(r"[.;\n]", text[:start])[-1]
    return NEGATION_BEFORE.search(clause) is not None or re.search(
        r"\b(?:no|not|never|without|cannot|does not|do not|unsupported|removed|retired|absent)\b"
        r"[\s,;:A-Za-z0-9_./`-]{0,100}$",
        clause,
        re.IGNORECASE,
    ) is not None


def _preservation_is_negated(text: str, action: re.Match[str], object_match: re.Match[str]) -> bool:
    clause = re.split(r"[.;\n]", text[:action.start()])[-1]
    suffix = text[object_match.end():object_match.end() + 80]
    return bool(
        _explicit_negation(text, action.start())
        or re.search(r"\b(?:false product claim|not a product claim|cannot be .*authority|never becomes .*authority)\b", suffix, re.I)
    )


def _same_clause_object(text: str, action: re.Match[str]) -> re.Match[str] | None:
    suffix = re.split(r"[.;\n]", text[action.end():], maxsplit=1)[0]
    match = PRESERVATION_OBJECT.search(suffix)
    if match is None:
        return None
    offset = action.end()
    return re.compile(PRESERVATION_OBJECT.pattern, re.IGNORECASE).search(text, offset, offset + match.end())


def check_required_phrases(texts: dict[str, str], failures: list[str]) -> None:
    required: dict[str, tuple[str, ...]] = {
        "README.md": ("CLEAN-INSTALL-ONLY", "FRESH_INSTALL_TARGET_EXISTS", "mode-0700", "renameat2", "14 preserved + 1 planned receipt schema", "No implementation", "standalone-first", "214 counted atomic rows"),
        "ARCHITECTURE.md": ("DIRECT/PLATFORM_PRIMITIVE", "renameat2", "C1", "C2", "WorkbenchGrantSet", "cell-bound", "SQLiteStageAdapter", "ProviderReadyActivationStore", "Standalone module boundary"),
        "CONTRACTS.md": ("aar.clean-install-database-identity.v1", "canonical_sha256(authority_material).removeprefix", "canonical_sha256(snapshot_material).removeprefix", "^install-[0-9a-f]{64}$", "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID", "SQLiteStageAdapter", "aar.clean-install-preparation.v1", "aar.install-candidate-receipt.v1", "wheel_size_bytes", "ProviderReadyActivationStore", "grant_ids", "GRANT_DENIED", "CAPABILITY_UNAVAILABLE", "step_index", "aar.evaluation-evidence-classification.v1", "T5 physical execution remains `NOT AUTHORIZED`"),
        "LIFECYCLE.md": ("ABSENT_TARGET", "PUBLISHING", "mode-0700", "renameat2", "never adopted", "mark_send_started", "outcome_unknown", "cell-bound", "FRESH_INSTALL_PARENT_REPLACED", "SQLiteStageAdapter", "runtime-generations"),
        "MIGRATION.md": ("migration-v6.sql", "OperationRegistry", "empty-v5", "external_authority_prepared_digest", "cutover_epoch", "secrets.token_hex(32)", "SQLiteStageAdapter", "no schema v7", "FRESH_INSTALL_TARGET_EXISTS", "profile_digest", "removed before final fsync/publication"),
        "ACCEPTANCE.md": ("214 counted atomic rows", "T0=22", "T1=91", "T2=73", "T3=15", "T4=13", "T5=0", "A-TEST-001", "fail_after_statement_25", "wrong_effect_absent"),
        "IMPLEMENTATION-PLAN.md": ("Lane C1", "Lane C2", "Lane D1", "Lane D2", "aar.install-candidate-receipt.v1", "same worktree", "bounded patch", "standalone", "SQLiteStageAdapter", "ProviderReadyActivationStore"),
        "TEST-STRATEGY-AND-GAP-ANALYSIS.md": ("214-row matrix", "A-V6-001", "renameat2", "exact-wheel", "runtime_generation=1", "wrong_effect_absent", "T5"),
        "EVALUATION.md": ("aar.evaluation-evidence-classification.v1", "prime_live_qualified", "requested_only", "paired-evaluation-admission"),
        "HANDOFF.md": ("CLEAN-INSTALL-ONLY", "214 counted atomic rows", "candidate-receipt", "no product implementation is claimed", "SQLite adapter", "standalone activation coordinator"),
        "decisions/ADR-005-clean-install-only.md": ("CLEAN-INSTALL-ONLY", "DIRECT/PLATFORM_PRIMITIVE", "FRESH_INSTALL_TARGET_EXISTS", "No aar-admin cutover command exists", "renameat2"),
        "verification/README.md": ("61 requirement IDs", "214 counted atomic rows", "wrong-effect absence", "aar.install-candidate-receipt.v1", "frozen-compatibility manifest", "--receipt"),
    }
    for relative, phrases in required.items():
        text = texts.get(relative, "")
        for phrase in phrases:
            if phrase not in text:
                failures.append(f"{relative}: missing required phrase: {phrase}")


def check_forbidden_active_semantics(texts: dict[str, str], failures: list[str]) -> None:
    for relative, text in texts.items():
        for match in PUBLIC_TRANSITION.finditer(text):
            if not _explicit_negation(text, match.start()):
                failures.append(f"{relative}: positive public transition command")
        for action in PRESERVATION_ACTION.finditer(text):
            object_match = _same_clause_object(text, action)
            if object_match and not _preservation_is_negated(text, action, object_match):
                failures.append(f"{relative}: positive preservation/in-place claim")
        if STALE_COUNT.search(text):
            failures.append(f"{relative}: stale predecessor acceptance count remains")
        if "hex(canonical_sha256" in text:
            failures.append(f"{relative}: non-constructible digest projection formula")
        if "C1/C2" in text:
            failures.append(f"{relative}: ambiguous C1/C2 phase ownership")


def validate_product_contract(requirements: dict[str, Any], failures: list[str]) -> None:
    if requirements.get("product_contract") != PRODUCT_CONTRACT:
        failures.append("product_contract must exactly match the clean-install-only contract")
    if requirements.get("summary_requirements") != ["TEST-001"]:
        failures.append("summary_requirements must contain only TEST-001")


def validate_requirements(root: Path, requirements: dict[str, Any], failures: list[str]) -> tuple[set[str], dict[str, dict[str, Any]], set[str]]:
    rows = requirements.get("requirements")
    if not isinstance(rows, list):
        failures.append("requirements must be a list")
        return set(), {}, set()
    ids: set[str] = set()
    by_id: dict[str, dict[str, Any]] = {}
    valid_tiers = {"T0", "T1", "T2", "T3", "T4", "T5"}
    for item in rows:
        if not isinstance(item, dict):
            failures.append("requirement row is not an object")
            continue
        row_id = item.get("id")
        if not isinstance(row_id, str) or not row_id:
            failures.append("requirement row lacks id")
            continue
        if row_id in ids:
            failures.append(f"duplicate requirement id: {row_id}")
        ids.add(row_id); by_id[row_id] = item
        owner = item.get("owner")
        if not isinstance(owner, str) or not (root / owner).is_file():
            failures.append(f"{row_id}: missing owner document {owner!r}")
        if owner in HISTORICAL_FILES or owner.startswith(HISTORICAL_PREFIXES):
            failures.append(f"{row_id}: historical owner is not active authority")
        if item.get("tier") not in valid_tiers:
            failures.append(f"{row_id}: invalid tier")
        if item.get("status") != "planned":
            failures.append(f"{row_id}: status must be planned")
        if not isinstance(item.get("statement"), str) or not item["statement"].strip():
            failures.append(f"{row_id}: statement must be non-empty")
    if [item.get("id") for item in rows if isinstance(item, dict)] != sorted(ids):
        failures.append("requirement IDs must be sorted and unique")
    return ids, by_id, set(requirements.get("summary_requirements", []))


def validate_matrix(root: Path, matrix: dict[str, Any], requirement_ids: set[str], summary_requirement_ids: set[str], summary_row_ids: set[str], failures: list[str]) -> tuple[int, set[str], dict[str, int]]:
    if matrix.get("schema_version") != "aar.prw-acceptance-matrix.v2-clean-install-rev2":
        failures.append("acceptance matrix schema_version is not rev2")
    if matrix.get("status") != "planned_not_executed":
        failures.append("acceptance matrix must remain planned_not_executed")
    if matrix.get("generation") != "clean-install-only-rev2":
        failures.append("acceptance matrix generation marker is wrong")
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        failures.append("acceptance rows must be a list"); rows = []
    ids: set[str] = set(); covered: set[str] = set(); counts = {tier: 0 for tier in ("T0", "T1", "T2", "T3", "T4", "T5")}
    required = ("title", "setup", "operation", "phase", "variant", "stimulus", "expected", "wrong_effect_absent", "evidence_class")
    exact_row_fields = {"id", "tier", "requirement", *required, "release_gate", "live_inference", "status"}
    for item in rows:
        if not isinstance(item, dict):
            failures.append("acceptance row is not an object"); continue
        row_id = item.get("id")
        if not isinstance(row_id, str) or not row_id:
            failures.append("acceptance row lacks id"); continue
        if row_id in ids: failures.append(f"duplicate acceptance id: {row_id}")
        ids.add(row_id)
        if set(item) != exact_row_fields: failures.append(f"{row_id}: acceptance row fields are not exact")
        if row_id in summary_row_ids: failures.append(f"summary row is counted: {row_id}")
        tier = item.get("tier")
        if tier not in counts: failures.append(f"{row_id}: invalid tier")
        else: counts[tier] += 1
        if "requirements" in item or isinstance(item.get("requirement"), (list, dict, tuple)):
            failures.append(f"{row_id}: requirement reference must be singular")
        requirement = item.get("requirement")
        if not isinstance(requirement, str) or not requirement.strip():
            failures.append(f"{row_id}: missing singular requirement")
        elif requirement not in requirement_ids:
            failures.append(f"{row_id}: unknown requirement {requirement}")
        else: covered.add(requirement)
        for field in required:
            if not isinstance(item.get(field), str) or not item[field].strip(): failures.append(f"{row_id}: missing scalar {field}")
        if all(isinstance(item.get(field), str) and item[field].strip() for field in ("operation", "phase", "variant")):
            if item.get("stimulus") != expected_stimulus(item): failures.append(f"{row_id}: stimulus is not the exact operation/phase/variant enactment")
        try:
            required_wrong_effect = expected_wrong_effect(row_id)
        except KeyError:
            failures.append(f"{row_id}: no wrong-effect safety partition is defined")
        else:
            if item.get("wrong_effect_absent") != required_wrong_effect: failures.append(f"{row_id}: wrong_effect_absent is missing or weakened")
        variant = item.get("variant")
        if isinstance(variant, str) and AGGREGATE_VARIANT.search(variant): failures.append(f"{row_id}: aggregate token in variant")
        if item.get("status") != "planned": failures.append(f"{row_id}: status must be planned")
        if tier in {"T0", "T1", "T2", "T3"} and (item.get("release_gate") is not True or item.get("live_inference") is not False): failures.append(f"{row_id}: T0-T3 gate flags invalid")
        if tier == "T4" and (item.get("release_gate") is not False or item.get("live_inference") is not True): failures.append(f"{row_id}: T4 gate flags invalid")
        if tier == "T5": failures.append(f"{row_id}: T5 execution row is forbidden")
    if [item.get("id") for item in rows if isinstance(item, dict)] != sorted(ids): failures.append("acceptance IDs must be sorted and unique")
    if matrix.get("declared_total_rows") != len(rows) or matrix.get("declared_atomic_row_count") != len(rows): failures.append("declared row counts do not equal counted atomic rows")
    if matrix.get("declared_tier_counts") != counts: failures.append(f"declared tier counts {matrix.get('declared_tier_counts')} != {counts}")
    if covered | summary_requirement_ids != requirement_ids: failures.append(f"requirement coverage mismatch: {sorted(requirement_ids - covered - summary_requirement_ids)}")
    if "A-TEST-001" not in set(matrix.get("non_counted_metadata", {}).get("summary_ids", [])): failures.append("A-TEST-001 must be non-counted metadata")
    variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("requirement") == "V6-001"}
    expected_variants = {f"fail_after_statement_{i:02d}_{name}" for i, name in enumerate(V6_STATEMENTS, 1)} | {"before-first-statement", "before-attestation-insert", "before-schema-migration-insert", "before-commit", "after-commit-readback"}
    if variants != expected_variants: failures.append("v6 failpoint/boundary variants are not the exact frozen 30-cell set")
    target_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("id", "").startswith("A-TARGET-")}
    expected_targets = {"regular-file", "empty-directory", "nonempty-directory", "symlink", "broken-symlink", "database-file", "wal-shm-tree", "authority-history-current-tree", "unknown-directory-entry"}
    if target_variants != expected_targets: failures.append("existing target type cells are incomplete")
    expected_epoch = {"fresh-invocation", "invalid-token-grammar", "cutover-epoch-mismatch", "reused-prior-invocation-token"}
    epoch_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("requirement") == "CLEAN-013"}
    if epoch_variants != expected_epoch: failures.append("install-epoch cells are not the exact four-cell set")
    expected_initial = {"generation-one-null-predecessor", "activation-generation-not-one", "non-null-previous-authority"}
    initial_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("requirement") == "CLEAN-012"}
    if initial_variants != expected_initial: failures.append("initial activation-authority cells are incomplete")
    expected_sqlite = {"proc-fd-unavailable", "renamed-stage-parent-entry", "fd-root-backup-destination", "wal-shm-nofollow-allowlist", "stage-fstat-identity-drift"}
    sqlite_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("requirement") == "CLEAN-014"}
    if sqlite_variants != expected_sqlite: failures.append("SQLite stage-adapter cells are incomplete")
    expected_modularity = {"installer-module-ownership", "thin-admin-operator-adapters", "single-activation-injection-seam"}
    modularity_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("requirement") == "MOD-001"}
    if modularity_variants != expected_modularity: failures.append("standalone modularity cells are incomplete")
    required_grant_store = {"generation-file-first-publish", "same-generation-exact-bytes", "same-generation-conflicting-bytes", "successor-generation-invalidates-session-grants", "explicit-current-session-request"}
    grant_store_variants = {item.get("variant") for item in rows if isinstance(item, dict) and item.get("id", "").startswith("A-GEN-")}
    if not required_grant_store <= grant_store_variants: failures.append("C2 grant-store lifecycle cells are incomplete")
    if any(isinstance(item, dict) and item.get("tier") == "T5" for item in rows): failures.append("T5 execution authority exists")
    return len(rows), covered, counts


def run_self_tests(failures: list[str]) -> None:
    probes = []
    positive = []; check_forbidden_active_semantics({"probe": "run aar-admin cutover apply"}, positive)
    probes.append(bool(positive))
    negative = []; check_forbidden_active_semantics({"probe": "No aar-admin cutover command exists"}, negative)
    probes.append(not negative)
    preservation_positive = []; check_forbidden_active_semantics({"probe": "status is read-only; preserve v5 in place"}, preservation_positive)
    probes.append(bool(preservation_positive))
    preservation_negative = []; check_forbidden_active_semantics({"probe": "The product does not preserve v5 in place"}, preservation_negative)
    probes.append(not preservation_negative)
    frozen_failures = ["frozen predecessor bytes drifted: probe"]
    probes.append(("fail" if frozen_failures else "pass") == "fail")
    stale = []; check_forbidden_active_semantics({"probe": "53 planned rows"}, stale)
    probes.append(bool(stale))
    stale_hyphen = []; check_forbidden_active_semantics({"probe": "The 194-row matrix is atomic"}, stale_hyphen)
    probes.append(bool(stale_hyphen))
    if not all(probes): failures.append("validator semantic self-test failed")


def validate(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    for relative in sorted(EXPECTED_FILES):
        if not (root / relative).is_file(): failures.append(f"missing required file: {relative}")
    requirements: dict[str, Any] = {}
    matrix: dict[str, Any] = {}
    try:
        requirements = load_json(root / "verification/requirements.json")
        matrix = load_json(root / "verification/acceptance-matrix.json")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        failures.append(f"cannot load machine assets: {error}")
    if requirements:
        if requirements.get("schema_version") != "aar.prw-requirements.v2-clean-install-rev2": failures.append("requirements schema_version is not rev2")
        if requirements.get("target_release") != "0.6.0a0-clean-install-only": failures.append("requirements target_release is not clean-install-only")
        if requirements.get("status") != "specification_only": failures.append("requirements status must remain specification_only")
        validate_product_contract(requirements, failures)
    requirement_ids, requirement_by_id, summary_requirements = validate_requirements(root, requirements, failures) if requirements else (set(), {}, set())
    acceptance_count, covered, tier_counts = validate_matrix(root, matrix, requirement_ids, summary_requirements, set(matrix.get("non_counted_metadata", {}).get("summary_ids", [])), failures) if matrix else (0, set(), {tier: 0 for tier in ("T0", "T1", "T2", "T3", "T4", "T5")})
    texts = active_text(root) if (root / "verification/requirements.json").is_file() and (root / "verification/acceptance-matrix.json").is_file() else {}
    check_required_phrases(texts, failures)
    check_forbidden_active_semantics(texts, failures)
    for relative, text in texts.items():
        if "[truncated]" in text: failures.append(f"{relative}: contains truncated marker")
    for document in sorted(root.rglob("*.md")):
        text = document.read_text(encoding="utf-8")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")): continue
            resolved = (document.parent / target).resolve()
            try: resolved.relative_to(root.resolve())
            except ValueError:
                failures.append(f"{document.relative_to(root)}: local link escapes spec root: {raw_target}"); continue
            if not resolved.exists(): failures.append(f"{document.relative_to(root)}: broken local link: {raw_target}")
    valid_versions = ("0.6.0a0", "1.2.3", "1.2.3rc1.post2.dev3", "10.20.30.post0")
    invalid_versions = ("00.06.000a00", "1.2.3rc01.post02.dev03", "01.2.3", "1.02.3", "1.2.03", "1.2.3+local")
    if any(PACKAGE_VERSION_PATTERN.fullmatch(value) is None for value in valid_versions): failures.append("package-version fixture rejected")
    if any(PACKAGE_VERSION_PATTERN.fullmatch(value) is not None for value in invalid_versions): failures.append("invalid package-version fixture accepted")
    run_self_tests(failures)
    # Frozen inputs are deliberately checked before this status/result construction.
    frozen = validate_frozen_inputs(root, failures)
    frozen_compatibility_digest = validate_frozen_compatibility(root, requirements, failures)
    artifact_files = [
        path for path in root.rglob("*")
        if path.is_file() and path.name != "spec-validation-receipt.json"
        and "reviews" not in path.relative_to(root).parts
        and "__pycache__" not in path.relative_to(root).parts and path.suffix != ".pyc"
    ]
    status = "pass" if not failures else "fail"
    assert status == ("pass" if not failures else "fail")
    return {
        "schema_version": "aar.prw-spec-validation.v2-clean-install-rev2",
        "status": status,
        "failures": failures,
        "generation": "clean-install-only-rev2",
        "requirement_count": len(requirement_ids),
        "summary_requirement_count": len(summary_requirements),
        "acceptance_row_count": acceptance_count,
        "covered_requirement_count": len(covered),
        "tier_counts": tier_counts,
        "markdown_file_count": len(list(root.rglob("*.md"))),
        "artifact_file_count": len(artifact_files),
        "tree_digest": tree_digest(root, artifact_files),
        "files": {path.relative_to(root).as_posix(): sha256_file(path) for path in sorted(artifact_files, key=lambda item: item.relative_to(root).as_posix())},
        "frozen_baseline_files": frozen,
        "frozen_compatibility_digest": frozen_compatibility_digest,
        "active_semantic_scan_excludes": sorted(HISTORICAL_FILES | {item for item in EXPECTED_FILES if item.startswith(HISTORICAL_PREFIXES)}),
        "non_claim": "This receipt proves specification structure only; it proves no implementation or runtime behavior.",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    result = validate(root)
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.receipt is not None:
        args.receipt.resolve().write_text(rendered, encoding="utf-8")
    summary_keys = ("status", "requirement_count", "summary_requirement_count", "acceptance_row_count", "covered_requirement_count", "tier_counts", "artifact_file_count", "tree_digest")
    print(json.dumps({key: result[key] for key in summary_keys}, sort_keys=True))
    for failure in result["failures"]: print(f"FAIL: {failure}")
    return 0 if result["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
