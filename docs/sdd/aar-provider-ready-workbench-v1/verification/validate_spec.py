#!/usr/bin/env python3
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
    "HANDOFF.md",
    "decisions/ADR-001-activate-existing-authorities.md",
    "decisions/ADR-002-ticket-root-planner.md",
    "decisions/ADR-003-preserve-v8-method-scoped-admission.md",
    "decisions/ADR-004-freeze-activation-contract-domains.md",
    "verification/README.md",
    "verification/requirements.json",
    "verification/acceptance-matrix.json",
    "verification/validate_spec.py"
}
LINK_PATTERN = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
PACKAGE_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:(?:a|b|rc)(?:0|[1-9][0-9]*))?"
    r"(?:\.post(?:0|[1-9][0-9]*))?(?:\.dev(?:0|[1-9][0-9]*))?$"
)
REQUIRED_S0_ACCEPTANCE_IDS = {
    "A-CUST-001",
    "A-NEC-001",
    "A-MIG-001", "A-MIG-002", "A-MIG-003", "A-MIG-004", "A-MIG-005",
    "A-MIG-006", "A-MIG-007", "A-MIG-008", "A-MIG-009", "A-MIG-010",
    "A-ACT-001", "A-ACT-002", "A-ACT-003", "A-ACT-004", "A-ACT-005",
    "A-AUTH-001", "A-AUTH-002", "A-AUTH-003",
    "A-PLAN-001", "A-PLAN-002", "A-PLAN-003", "A-PLAN-004", "A-PLAN-005", "A-PLAN-006",
    "A-ADM-001", "A-ADM-002", "A-ADM-003", "A-ADM-004", "A-ADM-005",
    "A-ROUTE-004",
    "A-COMP-001", "A-COMP-002",
    "A-OPS-001", "A-SEC-001", "A-TEST-001", "A-REL-001",
}
EXPECTED_ACCEPTANCE_MATRIX_DIGEST = "sha256:0bf0d9e7f341282f2db0e22240a43df850e16f302faff97e441a5a01716ecdc1"

FROZEN_BASELINE_FILES = (
    "schemas/aar-broker-catalog-v2.json",
    "schemas/aar-rlm-workbench-v1.schema.json",
    "schemas/aar-caller-work-v1.schema.json",
    "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql",
)

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
    return digest.hexdigest()


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


def validate_frozen_baseline(root: Path, failures: list[str]) -> dict[str, str]:
    """Bind and cross-check the exact predecessor bytes the successor reuses."""
    project_root = root.parents[2]
    paths = {relative: project_root / relative for relative in FROZEN_BASELINE_FILES}
    for relative, path in paths.items():
        if not path.is_file():
            failures.append(f"missing frozen baseline input: {relative}")
    hashes = {
        relative: "sha256:" + sha256_file(path)
        for relative, path in paths.items()
        if path.is_file()
    }
    if len(hashes) != len(FROZEN_BASELINE_FILES):
        return hashes

    try:
        catalog = load_json(paths["schemas/aar-broker-catalog-v2.json"])
        catalog_methods = tuple(row["method"] for row in catalog["contracts"])
        expected_catalog = (
            "model.request", "subagent.submit", "subagent.result",
            "evidence.query", "artifact.put", "effect.propose",
        )
        if catalog_methods != expected_catalog:
            failures.append("frozen broker catalog no longer matches the exact six-method order")

        workbench = load_json(paths["schemas/aar-rlm-workbench-v1.schema.json"])
        backend = workbench["$defs"]["BackendAvailability"]
        evidence = set(backend["properties"]["evidence_tier"]["enum"])
        if evidence != {"unknown", "caller_observed", "host_receipt_bound", "provider_attested"}:
            failures.append("frozen workbench capability evidence domain drifted")
        reference = next(
            item["then"]["properties"]
            for item in backend["allOf"]
            if item["if"]["properties"]["backend_kind"].get("const") == "reference"
        )
        reference_tuple = (
            reference["configured"].get("const"),
            reference["reference_only"].get("const"),
            reference["adapter_id"].get("type"),
            reference["adapter_generation"].get("type"),
            reference["evidence_tier"].get("const"),
        )
        if reference_tuple != (True, True, "null", "null", "unknown"):
            failures.append("frozen reference capability truth drifted")

        caller = load_json(paths["schemas/aar-caller-work-v1.schema.json"])
        logical_owner = caller["$defs"]["LogicalCallOwner"]
        planner = next(
            item for item in logical_owner["oneOf"]
            if item["properties"]["kind"].get("const") == "planner"
        )
        if (
            planner.get("additionalProperties") is not False
            or set(planner["properties"]) != {"kind", "phase", "step_index"}
            or set(planner["required"]) != {"kind", "phase", "step_index"}
        ):
            failures.append("frozen planner logical-owner wire is not exactly kind/phase/step_index")
        caller_requests = caller["$defs"]["CallerWorkRequest"]["oneOf"]
        caller_methods = tuple(item["properties"]["method"]["const"] for item in caller_requests)
        expected_caller_methods = (
            "model.request", "subagent.submit", "subagent.result",
            "evidence.query", "effect.propose",
        )
        if caller_methods != expected_caller_methods or "artifact.put" in caller_methods:
            failures.append("frozen caller-work method domain drifted")
        model_request = caller_requests[0]["properties"]
        if "route_binding" not in model_request or "response_contract" not in model_request:
            failures.append("frozen model request lacks route/response-contract owners")

        migration = paths[
            "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql"
        ].read_text(encoding="utf-8")
        match = re.search(
            r"CREATE TABLE IF NOT EXISTS rlm_workbench_suspensions \((.*?)\n\);",
            migration,
            flags=re.DOTALL,
        )
        if match is None:
            failures.append("frozen v6 suspension table was not found")
        else:
            suspension = match.group(1)
            cell_line = next(
                (line for line in suspension.splitlines() if "cell_execution_id TEXT" in line),
                "",
            )
            if "REFERENCES rlm_workbench_cells" not in cell_line or "NOT NULL" in cell_line:
                failures.append("frozen v6 suspension no longer permits planner-owned null cell identity")
            if "'model.request'" not in suspension or "'artifact.put'" in suspension:
                failures.append("frozen v6 suspension method ownership drifted")

        def table_body(name: str) -> str:
            found = re.search(
                rf"CREATE TABLE IF NOT EXISTS {re.escape(name)} \((.*?)\n\);",
                migration,
                flags=re.DOTALL,
            )
            if found is None:
                failures.append(f"frozen v6 table was not found: {name}")
                return ""
            return found.group(1)

        outbox = table_body("rlm_workbench_successor_outbox")
        required_outbox_columns = {
            "operation_id", "suspension_revision", "settlement_digest", "outbox_digest",
            "state", "rebind_generation", "successor_attempt_id", "successor_attempt_fence",
            "created_at_unix_ms", "prepared_at_unix_ms", "consumed_at_unix_ms",
        }
        missing_outbox = {
            name for name in required_outbox_columns
            if re.search(rf"^\s*{re.escape(name)}\s", outbox, flags=re.MULTILINE) is None
        }
        if missing_outbox:
            failures.append(f"frozen successor outbox lacks columns: {sorted(missing_outbox)}")
        if (
            "'pending', 'prepared', 'consumed'" not in outbox
            or "successor_attempt_id IS NULL" not in outbox
            or "rebind_generation > 0" not in outbox
        ):
            failures.append("frozen successor outbox state/CAS constraints drifted")
        if re.search(r"^\s*cell_execution_id\s", outbox, flags=re.MULTILINE):
            failures.append("frozen successor outbox unexpectedly became cell-bound")
        if re.search(
            r"CREATE\s+TRIGGER[^;]*\bON\s+rlm_workbench_successor_outbox\b",
            migration,
            flags=re.IGNORECASE,
        ):
            failures.append("frozen successor outbox unexpectedly forbids the reviewed state-preserving takeover CAS")

        registry_source = (project_root / "src/aar/runtime/registry.py").read_text(encoding="utf-8")
        required_existing_authority = (
            '("attempt_no", "INTEGER")',
            '("event_kind", "TEXT")',
            '("payload_json", "TEXT")',
            '("payload_digest", "TEXT")',
            "CREATE TABLE IF NOT EXISTS operation_attempts",
            "dispatcher_generation INTEGER NOT NULL",
            "CREATE TABLE IF NOT EXISTS operation_leases",
            "released_at_unix_ms INTEGER",
            "CREATE TABLE IF NOT EXISTS operation_dispatch",
            "current_attempt_no INTEGER",
            '"suspension_revision": suspension_revision',
            'event_kind="workbench_waiting_external"',
        )
        for marker in required_existing_authority:
            if marker not in registry_source:
                failures.append(f"existing predecessor/takeover authority drifted: {marker}")

        authority = table_body("rlm_workbench_attempt_authority")
        transfer = table_body("rlm_workbench_rebind_transfers")
        for name, body in (("attempt authority", authority), ("rebind transfer", transfer)):
            line = next((item for item in body.splitlines() if "cell_execution_id TEXT" in item), "")
            if "NOT NULL" not in line or "REFERENCES rlm_workbench_cells" not in line:
                failures.append(f"frozen v6 {name} no longer has cell-only NOT NULL authority")
    except (KeyError, TypeError, StopIteration, json.JSONDecodeError) as error:
        failures.append(f"cannot cross-check frozen baseline contracts: {error}")
    return hashes

def validate(root: Path) -> dict[str, Any]:
    failures: list[str] = []
    for relative in sorted(EXPECTED_FILES):
        if not (root / relative).is_file():
            failures.append(f"missing required file: {relative}")

    requirements_path = root / "verification" / "requirements.json"
    matrix_path = root / "verification" / "acceptance-matrix.json"
    requirements = load_json(requirements_path)
    matrix = load_json(matrix_path)
    observed_matrix_digest = "sha256:" + sha256_file(matrix_path)
    if observed_matrix_digest != EXPECTED_ACCEPTANCE_MATRIX_DIGEST:
        failures.append(
            "acceptance matrix bytes drifted from the reviewed mandatory row authority: "
            f"{observed_matrix_digest}"
        )

    if requirements.get("schema_version") != "aar.prw-requirements.v1":
        failures.append("unexpected requirements schema_version")
    if requirements.get("status") != "specification_only":
        failures.append("requirements status must remain specification_only")
    if matrix.get("schema_version") != "aar.prw-acceptance-matrix.v1":
        failures.append("unexpected acceptance matrix schema_version")
    if matrix.get("status") != "planned_not_executed":
        failures.append("acceptance matrix must remain planned_not_executed")

    requirement_rows = requirements.get("requirements")
    acceptance_rows = matrix.get("rows")
    if not isinstance(requirement_rows, list):
        failures.append("requirements must be a list")
        requirement_rows = []
    if not isinstance(acceptance_rows, list):
        failures.append("acceptance rows must be a list")
        acceptance_rows = []

    requirement_ids: set[str] = set()
    for row in requirement_rows:
        if not isinstance(row, dict):
            failures.append("requirement row is not an object")
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            failures.append("requirement row lacks id")
            continue
        if row_id in requirement_ids:
            failures.append(f"duplicate requirement id: {row_id}")
        requirement_ids.add(row_id)
        owner = row.get("owner")
        if not isinstance(owner, str) or not (root / owner).is_file():
            failures.append(f"{row_id}: missing owner document {owner!r}")
        if row.get("status") != "planned":
            failures.append(f"{row_id}: status must be planned in spec phase")
        if row.get("tier") not in {"T0", "T1", "T2", "T3", "T4", "T5"}:
            failures.append(f"{row_id}: invalid tier")

    acceptance_ids: set[str] = set()
    covered: set[str] = set()
    for row in acceptance_rows:
        if not isinstance(row, dict):
            failures.append("acceptance row is not an object")
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            failures.append("acceptance row lacks id")
            continue
        if row_id in acceptance_ids:
            failures.append(f"duplicate acceptance id: {row_id}")
        acceptance_ids.add(row_id)
        tier = row.get("tier")
        if tier not in {"T0", "T1", "T2", "T3", "T4", "T5"}:
            failures.append(f"{row_id}: invalid tier")
        refs = row.get("requirements")
        if not isinstance(refs, list) or not refs:
            failures.append(f"{row_id}: requirements must be a non-empty list")
            refs = []
        for reference in refs:
            if reference not in requirement_ids:
                failures.append(f"{row_id}: unknown requirement {reference}")
            else:
                covered.add(reference)
        if row.get("status") != "planned":
            failures.append(f"{row_id}: status must be planned")
        for field in ("title", "setup", "stimulus", "expected", "wrong_effect_absent", "evidence_class"):
            if not isinstance(row.get(field), str) or not row[field].strip():
                failures.append(f"{row_id}: missing non-empty {field}")
        live = row.get("live_inference")
        release_gate = row.get("release_gate")
        if tier in {"T0", "T1", "T2", "T3"} and live is not False:
            failures.append(f"{row_id}: T0-T3 cannot use live inference")
        if tier in {"T0", "T1", "T2", "T3"} and release_gate is not True:
            failures.append(f"{row_id}: every necessary T0-T3 row must be a release gate")
        if tier in {"T4", "T5"} and live is not True:
            failures.append(f"{row_id}: T4-T5 must declare live inference")
        if tier in {"T4", "T5"} and release_gate is not False:
            failures.append(f"{row_id}: T4-T5 cannot be package release gates")

    for missing in sorted(requirement_ids - covered):
        failures.append(f"requirement has no acceptance row: {missing}")

    forbidden_t5_ids = {f"A-EVAL-{index:03d}" for index in range(1, 7)}
    for forbidden in sorted(forbidden_t5_ids & acceptance_ids):
        failures.append(f"superseded executable paired-evaluation row remains: {forbidden}")
    for row in acceptance_rows:
        if isinstance(row, dict) and row.get("tier") == "T5":
            failures.append(f"{row.get('id', '<unknown>')}: v1 has no T5 execution authority")

    for missing in sorted(REQUIRED_S0_ACCEPTANCE_IDS - acceptance_ids):
        failures.append(f"missing mandatory S0 acceptance row: {missing}")

    requirement_by_id = {
        row["id"]: row
        for row in requirement_rows
        if isinstance(row, dict) and isinstance(row.get("id"), str)
    }
    semantic_requirement_phrases = {
        "ACT-001": ("All fourteen strict", "marker construction is acyclic", "complete required-property"),
        "ACT-002": ("complete unbranched activation history", "before host construction", "runtime-generation allocation"),
        "ACT-003": ("configured/reference-only/unknown", "never usable"),
        "ACT-004": ("one separate strict owner", "cannot be upgraded"),
        "COMP-001": ("registry-v6 nullable suspension", "operation-event/attempt/lease/dispatch owners", "existing cell-free owners"),
        "MIG-001": ("emits one canonical plan on stdout only", "no WAL checkpoint", "read-only/query-only"),
        "MIG-002": ("creates no separate epoch", "lock exclusively"),
        "MIG-005": ("UNINITIALIZED_RUNTIME_RESIDUE", "ordinary markers", "never silently applies or aborts"),
        "MIG-006": ("snapshot/prepared/DB/profile", "without blind replay"),
        "MIG-008": ("plan→prepared→transaction/receipt→terminal", "snapshot-before-prepared adoption", "cycle-free"),
        "AUTH-001": ("frozen grant_ids array", "client context is never authority"),
        "AUTH-003": ("mixed-set", "duplicate-capability", "authority-before-availability"),
        "OPS-001": ("stdout-only", "apply/abort/reconcile/restore/runtime-initialize"),
        "PLAN-001": ("step_index", "SQL-null cell authority", "unique predecessor operation event"),
        "PLAN-003": ("fenced prepared-to-prepared takeover", "valid directive/correction/certain terminal projection", "never accesses cell-bound"),
        "PLAN-004": ("settled_success", "cancelled_before_send", "outcome_unknown", "Absent usage dimensions remain null"),
        "PLAN-006": ("Only settled_success, settled_failure and cancelled_certain", "no blind resend", "synthetic settlement digest"),
        "ADM-001": ("normalized job/profile planner mode", "effective capability union", "mandatory T2 release evidence"),
        "ADM-002": ("frozen-v8 GRANT_DENIED", "CAPABILITY_UNAVAILABLE"),
        "ROUTE-001": ("provider, model, reasoning, fallback and cache", "Prime alias normalization"),
        "ROUTE-004": ("never becomes a certain deadline terminal",),
        "TEST-001": ("all fourteen schema fixtures", "crash-convergent cell-free planner outcomes", "all admission cross-products"),
        "EVAL-002": ("strict paired-evaluation-admission", "planning evidence only", "cannot authorize T5 spend"),
        "EVAL-001": ("No paired instrumentation block", "Future paired protocol categories", "launcher-authority SDD"),
    }
    for row_id, phrases in semantic_requirement_phrases.items():
        statement = str(requirement_by_id.get(row_id, {}).get("statement", ""))
        for phrase in phrases:
            if phrase not in statement:
                failures.append(f"{row_id}: missing semantic requirement phrase: {phrase}")

    valid_versions = ("0.6.0a0", "1.2.3", "1.2.3rc1.post2.dev3", "10.20.30.post0")
    invalid_versions = (
        "00.06.000a00",
        "1.2.3rc01.post02.dev03",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.3+local",
    )
    if any(PACKAGE_VERSION_PATTERN.fullmatch(value) is None for value in valid_versions):
        failures.append("canonical package-version validator rejects a required valid fixture")
    if any(PACKAGE_VERSION_PATTERN.fullmatch(value) is not None for value in invalid_versions):
        failures.append("canonical package-version validator accepts a non-canonical fixture")

    markdown_files = sorted(root.rglob("*.md"))
    for document in markdown_files:
        text = document.read_text(encoding="utf-8")
        if "[truncated]" in text:
            failures.append(f"{document.relative_to(root)}: contains a truncated-payload marker")
        for raw_target in LINK_PATTERN.findall(text):
            target = raw_target.split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (document.parent / target).resolve()
            try:
                resolved.relative_to(root.resolve())
            except ValueError:
                failures.append(
                    f"{document.relative_to(root)}: local link escapes spec root: {raw_target}"
                )
                continue
            if not resolved.exists():
                failures.append(
                    f"{document.relative_to(root)}: broken local link: {raw_target}"
                )

    readme = (root / "README.md").read_text(encoding="utf-8")
    required_phrases = (
        "product implementation is authorized but held pending successor freeze",
        "0.6.0a0",
        "v0.5.0a0",
        "Product-source writers remain blocked until this successor specification validates deterministically",
    )
    for phrase in required_phrases:
        if phrase not in readme:
            failures.append(f"README missing claim boundary phrase: {phrase}")

    contracts = (root / "CONTRACTS.md").read_text(encoding="utf-8")
    required_contract_phrases = (
        "v1 lexical, collection, and validation domains",
        "v1 semantics are exact principal-ID equality only",
        "`adapters` contains `1..6` manifests",
        "Generic `artifact.read` remains available to lower-layer broker code",
        "unknown | caller_observed | host_receipt_bound | provider_attested",
        "each contain `1..64` values",
        'backend_kind="reference"` requires `reference_only=true` and `evidence_tier="unknown"',
        "That publication is the activation-generation commit linearization point",
        "every numeric component is exactly `0` or has no leading zero",
        "aar-admin runtime initialize",
        "accepts no output-path option and emits exactly one canonical",
        "existing `Registry` initialization transaction",
        "aar-admin cutover reconcile",
        "It MUST NOT execute any WAL checkpoint pragma",
        "All properties below are required",
        "literal integer `5`; an already-v6 root returns existing status/receipt",
        "State/nullability rules are exhaustive",
        "does not claim Byzantine rollback detection",
        "one strict mode-tagged object",
        "plan → prepared marker → transaction/receipt → terminal marker",
        "same still-current generation is `CAPABILITY_UNAVAILABLE`",
        "sorted unique `grant_ids` array",
        "integer `1000..900000`",
        "does **not** place a non-recomputable internal-record-shape digest",
        "cutover_terminal_marker_digest",
        "terminally resolves the original cutover without rewriting either marker",
        "T5 physical execution remains `NOT AUTHORIZED`",
        "cancelled_before_send`, `cancel_requested`, `outcome_unknown`, and `quarantined` have no successor outbox",
        "Only a pending outbox created by frozen `settled_success`, `settled_failure`, or `cancelled_certain`",
        "Exhaustive planner settlement and successor outcomes",
        "aar.paired-evaluation-admission.v1",
        "unique append-only `operation_events` row",
        "CAS the exact old prepared tuple to another `prepared` tuple",
        "In **one registry transaction**",
        "`provider_tier`, `model_tier`, `reasoning_tier`, `fallback_tier`, `cache_tier`",
        "effective_reasoning=null",
        "The cell-bound `rlm_workbench_attempt_authority` and `rlm_workbench_rebind_transfers` tables are neither read nor written",
        "client-supplied context is only a lookup/challenge; it never establishes those values",
        "an `ordinal` field MUST NOT be emitted",
        'method="artifact.put"` plus `backend_kind="caller_driver"` is invalid',
        "`BackendAvailability.backend_kind` is the readback-only domain",
        "| `unconfigured` | `false` | `false` | both null | `unknown` |",
        "`previous_activation_authority_digest` remains nullable in every state",
        "`candidate` is an independently installed observation",
        "`issued_at_unix_ms: UnixMs`",
        "requires `issued_at_unix_ms < expires_at_unix_ms`",
        "Python-mode tests use tuples; JSON-mode arrays are validated through `model_validate_json`",
        "| `reference` | `true` | `true` | both null | `unknown` |",
        "only native/caller-driver executable rows carry the current runtime generation",
        "all profile-derived nullable values remain source-dependent",
        "State alone does not make an independently observed operator tuple non-null",
        "Broker/tool/route observations and validated profile/history/operator evidence remain source-dependent",
        "Independently validated broker-catalog, tool-surface and route observations remain source-dependent",
        "every profile/history/runtime/grant/capability binding is null; all six methods are truthful unconfigured/reference rows",
        "`migration_required` cannot carry `capability_digest` or a configured native/caller-driver method row",
    )
    for phrase in required_contract_phrases:
        if phrase not in contracts:
            failures.append(f"CONTRACTS missing S0 phrase: {phrase}")
    forbidden_contract_claims = (
        "| `issued_grant_record_shape_digest` |",
        "| `attempts` | exact two-item tuple",
        "literal `benchmark_ready_authorized`",
        "only pre-spend authority for one two-arm paired benchmark block",
    )
    for claim in forbidden_contract_claims:
        if claim in contracts:
            failures.append(f"CONTRACTS retains forbidden superseded claim: {claim}")

    architecture = (root / "ARCHITECTURE.md").read_text(encoding="utf-8")
    required_architecture_phrases = (
        "configured=true AND reference_only=false",
        "configured=true`, `reference_only=true`, null adapter identity/generation and `evidence_tier=unknown`",
        "no `ordinal` wire field is introduced",
        "v1 activation rejects an `artifact.put` caller driver",
        "side-effect-free read-only preflight",
        "frozen-v6 cell-free planner CAS",
        "generation-advancing `prepared→prepared` takeover",
        "atomically commits `prepared→consumed`",
        "package-owned immutable method registry",
        "Prime may retain requested-only reasoning with null effective effort",
        "only when an activation-profile path is explicitly supplied",
        "When no activation-profile path is supplied",
        "`AUTHORITY_DENIED` is never a v8 wire literal",
        "complete immutable activation-history",
        "The client context is only compared against issuer state and never creates authority",
        "Do not create a parallel",
    )
    for phrase in required_architecture_phrases:
        if phrase not in architecture:
            failures.append(f"ARCHITECTURE missing S0 phrase: {phrase}")

    schema_names = (
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
    for name in schema_names:
        if name not in contracts:
            failures.append(f"CONTRACTS missing normative schema owner: {name}")
    if "fourteen frozen strict schemas" not in readme:
        failures.append("README schema complexity budget is not fourteen")

    complete_wire_markers = (
        "Shared operator-document wire domains",
        "A valid plan is emitted only after all read-only checks pass. All properties below are required",
        "This is the single prepared authority for cutover or restore. Every property is required",
        "The two receipt schemas use the shared records above. Every listed property is required",
        "This is the epoch terminal file and the only outer marker. Every property is required",
        "This is the strict output of read-only `aar-admin activation status`; it has a self `readback_digest`",
        "This evaluator-owned metadata contract is not a runtime adapter or provider authority. Every property is required",
        "After activation preflight and factory derivation, the supervisor constructs this strict server-side policy record",
        "This evaluator-owned strict document is the sole owner of route-component, usage and visibility classification",
        "planning/evidence document, **not** runtime/provider, physical-launch, replay, or single-use-attempt authority",
        "readback_digest",
        "classification_digest",
        "grant_set_digest",
        "OperatorPath",
        "SidecarObservation",
    )
    for marker in complete_wire_markers:
        if marker not in contracts:
            failures.append(f"CONTRACTS lacks complete wire marker: {marker}")

    migration = (root / "MIGRATION.md").read_text(encoding="utf-8")
    lifecycle = (root / "LIFECYCLE.md").read_text(encoding="utf-8")
    evaluation = (root / "EVALUATION.md").read_text(encoding="utf-8")
    acceptance = (root / "ACCEPTANCE.md").read_text(encoding="utf-8")
    required_cross_document_phrases = {
        "MIGRATION.md": (
            "without executing a checkpoint or any filesystem/DB mutation",
            "emit exactly one canonical `aar.cutover-plan.v1` JSON document",
            "existing `Registry` initialization transaction",
            "operator must next run the ordinary read-only `cutover plan",
            "history/<activation-generation>-<authority-digest>.json",
            "history publication is the generation commit linearization point",
            "aar-admin cutover apply|abort|reconcile|restore",
            "creates no initialization directory, cutover plan, snapshot, marker, epoch, receipt",
            "aar.operator-prepared-marker.v1",
            "aar.operator-terminal-marker.v1",
            "cutover_recovery_required` → `restore_committed",
            "Abrupt process loss is not claimed crash-convergent in v1",
            "UNINITIALIZED_RUNTIME_RESIDUE",
        ),
        "LIFECYCLE.md": (
            "pending→prepared",
            "prepared→prepared",
            "selects exactly one `CONTRACTS.md §7` outcome",
            "Outcome unknown does not prepare or consume",
            "Planner flow never touches cell-bound",
            "`start_only=false` is rejected before operation creation without exception",
            "`cancel_requested`, `outcome_unknown`, or `quarantined`",
            "no successor outbox",
            "ACTIVATION_HISTORY_CONFLICT",
        ),
        "EVALUATION.md": (
            "aar.evaluation-evidence-classification.v1",
            "classified independently",
            "five route-component tiers, derived route qualification, usage tier and attempt visibility have no other owner",
            "never upgrades the evaluator record",
            "aar.paired-evaluation-admission.v1",
            "No v1 status authorizes T5 physical execution",
            "No paired instrumentation block is admitted",
        ),
        "ACCEPTANCE.md": (
            "all fourteen normative documents",
            "accepts no output path",
            "`A-EVAL-007` is the sole machine row",
            "executes no WAL checkpoint",
            "sorted-unique `grant_ids` array",
            "mandatory release-gate method-scoped admission rows",
            "valid unexpired strict `aar.paired-evaluation-admission.v1`",
        ),
    }
    documents = {
        "MIGRATION.md": migration,
        "LIFECYCLE.md": lifecycle,
        "EVALUATION.md": evaluation,
        "ACCEPTANCE.md": acceptance,
    }
    for name, phrases in required_cross_document_phrases.items():
        for phrase in phrases:
            if phrase not in documents[name]:
                failures.append(f"{name} missing blocker-closure phrase: {phrase}")

    forbidden_obsolete_phrases = (
        "checkpoint/read WAL safely",
        "atomic current.json replacement is the linearization point",
        "atomic `current.json` replacement is the commit linearization point",
        "provider_reported | host_receipt_bound | caller_observed | requested_only",
        "aar.fresh-v6-initialization-receipt.v1",
        "route_tier",
        "contradiction_reason",
        "directly at schema v6",
        "cutover plan       --runtime-home ... --intent ... --profile-output ... --output",
        "first commits canonical empty v5, then emits",
        "pre/post-v5 crashes converge",
        "A crash before the v5 transaction commit leaves absent/uninitialized input that may be retried",
        "A crash after commit leaves canonical empty v5",
    )
    combined_semantics = "\n".join(documents.values()) + "\n" + contracts + "\n" + architecture
    for phrase in forbidden_obsolete_phrases:
        if phrase in combined_semantics:
            failures.append(f"obsolete S0 authority phrase remains: {phrase}")

    acceptance_by_id = {
        row.get("id"): row for row in acceptance_rows if isinstance(row, dict)
    }
    for row_id in ("A-ADM-003", "A-ADM-004", "A-ADM-005"):
        row = acceptance_by_id.get(row_id)
        if not isinstance(row, dict) or row.get("release_gate") is not True:
            failures.append(f"{row_id}: mandatory admission discriminator is not a release gate")

    adm004 = acceptance_by_id.get("A-ADM-004")
    if isinstance(adm004, dict):
        adm004_semantics = "\n".join(
            str(adm004.get(field, ""))
            for field in ("title", "setup", "stimulus", "expected", "wrong_effect_absent", "evidence_class")
        )
        for marker in (
            "Both separately digested caller_delegated_ticketed/caller_driver and service_managed/native model.request profiles",
            "max_artifact_count",
            "max_artifact_bytes",
            "require_named_artifacts",
            "artifact.put",
            "max_subagent_calls",
            "either subagent capability",
            "both subagent.submit and subagent.result",
            "evidence.query/effect.propose",
            "zero budget",
            "zero/nonzero budgets",
            "coherent/conflicting grant unions",
            "retired factory/profile/digest",
            "same-generation instantiated-adapter health",
            "Before operation creation",
            "GRANT_DENIED",
            "CAPABILITY_UNAVAILABLE",
        ):
            if marker not in adm004_semantics:
                failures.append(f"A-ADM-004 missing budget/grant/method cross-product marker: {marker}")

    if (root / "WAL.md").exists():
        failures.append("public SDD package must not contain local-only WAL.md")

    frozen_baseline_files = validate_frozen_baseline(root, failures)

    artifact_files = [
        path
        for path in root.rglob("*")
        if path.is_file()
        and path.name not in {"spec-validation-receipt.json"}
        and "reviews" not in path.relative_to(root).parts
        and "__pycache__" not in path.relative_to(root).parts
        and path.suffix != ".pyc"
    ]
    return {
        "schema_version": "aar.prw-spec-validation.v1",
        "status": "pass" if not failures else "fail",
        "failures": failures,
        "requirement_count": len(requirement_ids),
        "acceptance_row_count": len(acceptance_ids),
        "covered_requirement_count": len(covered),
        "markdown_file_count": len(markdown_files),
        "artifact_file_count": len(artifact_files),
        "tree_digest": tree_digest(root, artifact_files),
        "files": {
            path.relative_to(root).as_posix(): "sha256:" + sha256_file(path)
            for path in sorted(artifact_files, key=lambda item: item.relative_to(root).as_posix())
        },
        "frozen_baseline_files": frozen_baseline_files,
        "non_claim": "This receipt proves specification structure only; it proves no implementation or runtime behavior."
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
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.receipt.with_suffix(args.receipt.suffix + ".tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.receipt)
    print(json.dumps({key: result[key] for key in ("status", "requirement_count", "acceptance_row_count", "covered_requirement_count", "artifact_file_count", "tree_digest")}, sort_keys=True))
    if result["failures"]:
        for failure in result["failures"]:
            print(f"FAIL: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
