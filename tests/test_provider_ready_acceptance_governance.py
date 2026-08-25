"""Discriminating static governance oracles for the provider-ready acceptance gaps.

These checks deliberately stay below the real-runtime boundary.  They bind exact
frozen bytes and ownership seams, then exercise the pure projection models with
otherwise-valid witnesses and one-axis mutants.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_evaluation_models import ProviderAliasAttestation
from aar.provider_ready_install_models import (
    CleanInstallCandidateProjection,
    CleanInstallPreparation,
    MigrationAttestationDocument,
    MigrationAttestationPayload,
)
from aar.provider_ready_models import HostActivationIntent
from aar.runtime._install_evidence import (
    build_clean_install_preparation,
    build_migration_attestation,
    project_authority_store_id,
    project_snapshot_id,
)
from aar.runtime._install_fs import (
    DATABASE_NAME,
    InstallerError,
    compute_database_identity,
    compute_runtime_home_digest,
    preflight_target,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
THIS_TEST = "tests/test_provider_ready_acceptance_governance.py"
BASELINE_COMMIT = "8115e8570d92c507dc7ec176699f3131168be6f0"

EXPECTED_SCHEMA_PATHS = (
    "schemas/aar-acceptance-evidence-v1.schema.json",
    "schemas/aar-artifact-publication-v1.schema.json",
    "schemas/aar-broker-catalog-v2.json",
    "schemas/aar-caller-work-v1.schema.json",
    "schemas/aar-mcp-tools-v4.json",
    "schemas/aar-mcp-tools-v5.json",
    "schemas/aar-mcp-tools-v7.json",
    "schemas/aar-mcp-tools-v8-combined.json",
    "schemas/aar-mcp-tools-v8.json",
    "schemas/aar-migration-cutover-v1.schema.json",
    "schemas/aar-provider-ready-schemas-v1.json",
    "schemas/aar-rlm-workbench-v1.schema.json",
    "schemas/aar-schemas-v1.json",
    "schemas/aar-workspace-broker-frame-v1.schema.json",
)
PLANNED_RECEIPT_SCHEMA = "schemas/aar-install-candidate-receipt-v1.schema.json"

# The three clean-install product files were clean at task start.  Keeping their
# task-start hashes here makes a product edit fail even if it is staged or later
# hidden by a broad test fixture.
TASK_START_PRODUCT_HASHES = {
    "src/aar/runtime/installer.py": (
        "sha256:46958d8773b130cc9e30e6bcab3aba1cbf290e845f3cf0c1c8c59683bd905702"
    ),
    "src/aar/runtime/_install_evidence.py": (
        "sha256:f967e6ddcb1204030344f859760a3d82fae85a832424ec9fd67084d515e496f0"
    ),
    "src/aar/runtime/_install_fs.py": (
        "sha256:207137114383e570b13e24b8b35569add5257b183e44d7d46bf08721d86ddf4c"
    ),
}

# These are the exact D1/D2 predecessor bytes protected by the SDD validator.
FROZEN_PREDECESSOR_HASHES = {
    "schemas/aar-broker-catalog-v2.json": (
        "sha256:88099616e61f41c7d72d5e1c81fba8ebfe29995467fd583acc9da0bb15aeadbc"
    ),
    "schemas/aar-rlm-workbench-v1.schema.json": (
        "sha256:0d52527f9019cae724459e3eb36a0165b91822771ae64f815f1c2fd18d3054ef"
    ),
    "schemas/aar-caller-work-v1.schema.json": (
        "sha256:76deb95fb89081c7ec90734821689e14a893040696af6727725b059676c2b7e3"
    ),
    "docs/sdd/aar-rlm-native-workbench-v2/migration-v6.sql": (
        "sha256:8e7080b319aadb4eb98b5e3b9e12efe8c189c0bd12a82dc8ba1eea7c7bfe29b7"
    ),
    "src/aar/runtime/assets/migration-v6.sql": (
        "sha256:8e7080b319aadb4eb98b5e3b9e12efe8c189c0bd12a82dc8ba1eea7c7bfe29b7"
    ),
}

FORBIDDEN_FIELD_NAMES = {
    "api_key",
    "command",
    "credential",
    "credentials",
    "endpoint",
    "executable_content",
    "import_path",
    "module_path",
    "password",
    "passwd",
    "private_key",
    "provider_url",
    "secret",
    "token_value",
}
SECRET_CANARIES = (
    ("aws_access_key", re.compile(rb"AKIA[0-9A-Z]{16}")),
    ("openai_key", re.compile(rb"sk-[A-Za-z0-9]{20,}")),
    ("github_token", re.compile(rb"gh[pousr]_[A-Za-z0-9_]{20,}")),
    ("private_key", re.compile(rb"-----BEGIN [A-Z ]+PRIVATE KEY-----")),
    ("bearer_token", re.compile(rb"Bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    (
        "named_secret",
        re.compile(
            rb"(?i)(?:password|passwd|secret|api[_-]?key)\s*[:=]\s*"
            rb"[\"']?[A-Za-z0-9/+_=-]{12,}"
        ),
    ),
)

EVALUATION_MODULE = "aar.provider_ready_evaluation_models"
EVALUATION_GENERATOR_MODULE = "aar.provider_ready_contract_generator"
OWNERSHIP_MODULES = (
    "aar.admin",
    "aar.runtime.operator",
    "aar.runtime.installer",
    "aar.runtime._install_fs",
    "aar.runtime._install_evidence",
    "aar.runtime.provider_ready_activation",
    "aar.runtime.provider_ready_startup",
    "aar.runtime.supervisor",
    "aar.runtime.reference_host",
    "aar.runtime.registry",
    "aar.mcp.server",
)
MECHANISM_MODULES = (
    "aar.provider_ready_install_models",
    "aar.runtime._install_fs",
    "aar.runtime._install_evidence",
    "aar.runtime.installer",
    "aar.runtime.provider_ready_activation",
)
FORBIDDEN_EXTERNAL_MODULES = (
    "alembic",
    "anthropic",
    "boto3",
    "httpx",
    "openai",
    "redis",
    "requests",
    "sqlalchemy",
    "subprocess",
)


def _sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _json_file(relative: str) -> Any:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def _module_path(module: str) -> Path | None:
    candidate = SRC.joinpath(*module.split("."))
    source = candidate.with_suffix(".py")
    if source.is_file():
        return source
    package = candidate / "__init__.py"
    return package if package.is_file() else None


def _module_tree(module: str) -> ast.Module:
    path = _module_path(module)
    if path is None:  # pragma: no cover - an inventory failure is clearer below
        raise AssertionError(f"module is not present in source inventory: {module}")
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _local_module_exists(module: str) -> bool:
    return _module_path(module) is not None


def _direct_imports(module: str) -> set[str]:
    tree = _module_tree(module)
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names if _local_module_exists(alias.name))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            package = module.split(".")[: -node.level]
            if node.module:
                package.extend(node.module.split("."))
            base = ".".join(package)
        else:
            base = node.module or ""
        candidates = {base} if base else set()
        for alias in node.names:
            if base and alias.name != "*":
                candidates.add(f"{base}.{alias.name}")
        imports.update(candidate for candidate in candidates if _local_module_exists(candidate))
    return imports


def _local_import_graph(start: str) -> set[str]:
    seen = {start}
    pending = [start]
    while pending:
        current = pending.pop()
        for dependency in _direct_imports(current):
            if dependency not in seen:
                seen.add(dependency)
                pending.append(dependency)
    return seen


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _calls_by_function(module: str) -> dict[str, set[str]]:
    tree = _module_tree(module)
    result: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = {
            name
            for child in ast.walk(node)
            if isinstance(child, ast.Call)
            for name in [_call_name(child.func)]
            if name is not None
        }
        result.setdefault(node.name, set()).update(calls)
    return result


def _all_calls(module: str) -> set[str]:
    return set().union(*_calls_by_function(module).values())


def _field_names(module: str) -> set[str]:
    fields: set[str] = set()
    for node in _module_tree(module).body:
        if not isinstance(node, ast.ClassDef):
            continue
        fields.update(
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        )
    return fields


def _definitions_named(name: str, modules: tuple[str, ...]) -> list[str]:
    locations: list[str] = []
    for module in modules:
        if any(
            isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
            for node in ast.walk(_module_tree(module))
        ):
            locations.append(module)
    return locations


def _keyword_call_exists(module: str, function: str, callee: str, keyword: str) -> bool:
    tree = _module_tree(module)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != function:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or _call_name(child.func) != callee:
                continue
            if any(argument.arg == keyword for argument in child.keywords):
                return True
    return False


def _positional_call_exists(
    module: str,
    function: str,
    callee: str,
    position: int,
    argument: str,
) -> bool:
    tree = _module_tree(module)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or node.name != function:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call) or _call_name(child.func) != callee:
                continue
            if len(child.args) > position and _call_name(child.args[position]) == argument:
                return True
    return False


def _json_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_json_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.update(_json_keys(nested))
    return keys


def _persisted_contract_paths() -> list[Path]:
    roots = (
        ROOT / "docs" / "sdd" / "aar-provider-ready-workbench-v1",
        ROOT / "tests" / "fixtures" / "provider-ready",
        ROOT / "src" / "aar" / "runtime",
    )
    paths: list[Path] = []
    for root in roots:
        paths.extend(
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in {".json", ".log", ".sql"}
        )
    return sorted(paths)


def _secret_hits(raw: bytes) -> tuple[str, ...]:
    return tuple(name for name, pattern in SECRET_CANARIES if pattern.search(raw))


def _fixture_intent() -> HostActivationIntent:
    path = ROOT / "tests" / "fixtures" / "provider-ready" / "valid" / "activation-intent.json"
    return HostActivationIntent.model_validate_json(path.read_bytes(), strict=True)


def _preparation() -> CleanInstallPreparation:
    return build_clean_install_preparation(
        install_epoch="install-" + "e" * 64,
        runtime_home_digest="sha256:" + "a" * 64,
        database_identity="db-" + "b" * 64,
        empty_v5_backup_digest="sha256:" + "c" * 64,
        empty_v5_backup_size_bytes=4096,
        canonical_v5_row_set_digest="sha256:" + "d" * 64,
        intent=_fixture_intent(),
        migration_sql_digest="sha256:" + "f" * 64,
    )


def _attestation() -> MigrationAttestationDocument:
    preparation = _preparation()
    return build_migration_attestation(
        preparation=preparation,
        snapshot_id=project_snapshot_id(
            preparation.empty_v5_backup_digest,
            preparation.empty_v5_backup_size_bytes,
            preparation.install_epoch,
        ),
        intent=_fixture_intent(),
        migration_sql_digest=preparation.migration_sql_digest,
        started_at_unix_ms=100,
        completed_at_unix_ms=101,
    )


def test_frozen_compatibility_manifest_is_complete_ordered_and_content_bound() -> None:
    """A-COMP-001: omission, reorder, size, hash, and bundle drift all fail."""

    requirements = _json_file(
        "docs/sdd/aar-provider-ready-workbench-v1/verification/requirements.json"
    )
    manifest = requirements["frozen_compatibility_manifest"]
    assert manifest["schema_version"] == "aar.prw-frozen-compatibility-manifest.v1"
    schemas = manifest["schemas"]
    schema_paths = [entry["path"] for entry in schemas]
    assert schema_paths == list(EXPECTED_SCHEMA_PATHS)
    assert schema_paths == sorted(schema_paths)
    observed_schema_paths = sorted(
        path.relative_to(ROOT).as_posix() for path in (ROOT / "schemas").glob("*.json")
    )
    assert observed_schema_paths == sorted([*EXPECTED_SCHEMA_PATHS, PLANNED_RECEIPT_SCHEMA])

    fixture_manifest_path = "tests/fixtures/provider-ready/manifest.json"
    fixture_manifest_entry = manifest["fixture_manifest"]
    assert fixture_manifest_entry["path"] == fixture_manifest_path
    declared_fixture_manifest = _json_file(fixture_manifest_path)
    declared_paths = [
        f"tests/fixtures/provider-ready/{entry['path']}"
        for entry in declared_fixture_manifest["fixtures"]
    ]
    fixture_entries = manifest["fixtures"]
    fixture_paths = [entry["path"] for entry in fixture_entries]
    assert len(fixture_entries) == 64
    assert fixture_paths == declared_paths
    assert fixture_paths == sorted(fixture_paths)
    assert len(set(fixture_paths)) == len(fixture_paths)

    entries = [*schemas, fixture_manifest_entry, *fixture_entries]
    for entry in entries:
        assert set(entry) == {"path", "size_bytes", "sha256"}
        raw = (ROOT / entry["path"]).read_bytes()
        assert entry["size_bytes"] == len(raw)
        assert entry["sha256"] == _sha256_bytes(raw)

    core = {
        key: manifest[key] for key in ("schema_version", "schemas", "fixture_manifest", "fixtures")
    }
    assert manifest["bundle_digest"] == canonical_sha256(core)


def test_frozen_predecessor_and_migration_bytes_are_exact_read_only_inputs() -> None:
    """A-COMP-001: v6/v7/v8/D1/D2 bytes and the packaged SQL stay identical."""

    for relative, expected in FROZEN_PREDECESSOR_HASHES.items():
        raw = (ROOT / relative).read_bytes()
        assert _sha256_bytes(raw) == expected, relative
    assert (
        ROOT / "docs" / "sdd" / "aar-rlm-native-workbench-v2" / "migration-v6.sql"
    ).read_bytes() == (
        ROOT / "src" / "aar" / "runtime" / "assets" / "migration-v6.sql"
    ).read_bytes()


def test_task_scope_and_task_start_product_hashes_are_unchanged() -> None:
    """A-CUST-001: only this acceptance file may be dirty in this worktree."""

    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    changed_paths = set()
    for line in result.stdout.splitlines():
        if len(line) >= 3:
            changed_paths.add(line[3:].split(" -> ")[-1])
    assert changed_paths <= {THIS_TEST}
    for relative, expected in TASK_START_PRODUCT_HASHES.items():
        assert _sha256_bytes((ROOT / relative).read_bytes()) == expected, relative
    for relative in FROZEN_PREDECESSOR_HASHES:
        assert (ROOT / relative).is_file(), relative


def test_static_ownership_manifest_keeps_install_and_activation_seams_narrow() -> None:
    """A-MOD-001..003: ownership is proven by AST imports and call edges."""

    for module in OWNERSHIP_MODULES:
        assert _module_path(module) is not None

    forbidden_direct = {
        "aar.admin": {
            "aar.runtime._install_evidence",
            "aar.runtime._install_fs",
            "aar.runtime._install_sqlite",
            "aar.runtime.migrations",
            "aar.runtime.provider_ready_activation",
            "aar.runtime.reference_host",
            "aar.runtime.registry",
            "aar.runtime.supervisor",
            "aar.mcp.server",
        },
        "aar.runtime.operator": {
            "aar.runtime._install_evidence",
            "aar.runtime._install_fs",
            "aar.runtime._install_sqlite",
            "aar.runtime.installer",
            "aar.runtime.migrations",
            "aar.runtime.provider_ready_activation",
            "aar.runtime.registry",
            "aar.runtime.supervisor",
            "aar.mcp.server",
        },
        "aar.runtime.installer": {
            "aar.admin",
            "aar.mcp.server",
            "aar.runtime.operator",
            "aar.runtime.provider_ready_activation",
            "aar.runtime.reference_host",
            "aar.runtime.supervisor",
        },
        "aar.runtime.provider_ready_activation": {
            "aar.admin",
            "aar.mcp.server",
            "aar.runtime._install_evidence",
            "aar.runtime._install_fs",
            "aar.runtime.installer",
            "aar.runtime.registry",
            "aar.runtime.supervisor",
        },
        "aar.runtime.supervisor": {
            "aar.runtime._install_evidence",
            "aar.runtime._install_fs",
            "aar.runtime.installer",
            "aar.runtime.migrations",
            "aar.runtime.registry",
        },
        "aar.runtime.reference_host": {
            "aar.admin",
            "aar.runtime._install_evidence",
            "aar.runtime._install_fs",
            "aar.runtime.installer",
            "aar.runtime.migrations",
            "aar.runtime.provider_ready_activation",
            "aar.runtime.supervisor",
        },
    }
    for module, forbidden in forbidden_direct.items():
        assert not (_direct_imports(module) & forbidden), module

    assert {
        "aar.runtime._install_evidence",
        "aar.runtime._install_fs",
        "aar.runtime._install_sqlite",
        "aar.runtime.migrations",
    } <= _direct_imports("aar.runtime.installer")
    assert {"aar.runtime._activation_store", "aar.runtime._session_grants"} <= _direct_imports(
        "aar.runtime.provider_ready_activation"
    )
    assert _definitions_named("OperationRegistry", OWNERSHIP_MODULES) == ["aar.runtime.registry"]
    assert _definitions_named("ProviderReadyActivationStore", OWNERSHIP_MODULES) == [
        "aar.runtime.provider_ready_activation"
    ]

    admin_calls = _calls_by_function("aar.admin")["main"]
    assert "install_clean_runtime" in admin_calls
    assert not {"_create_stage", "_publish", "apply_registry_v6", "OperationRegistry"} & admin_calls
    assert not {
        "install_clean_runtime",
        "_create_stage",
        "_publish",
        "apply_registry_v6",
        "OperationRegistry",
    } & _all_calls("aar.runtime.operator")
    assert not {
        "OperationRegistry",
        "_create_stage",
        "_publish",
        "apply_registry_v6",
    } & _all_calls("aar.mcp.server")
    assert not {
        "OperationRegistry",
        "_create_stage",
        "_publish",
        "apply_registry_v6",
    } & _all_calls("aar.runtime.supervisor")
    assert "OperationRegistry" in _calls_by_function("aar.runtime.reference_host")["__init__"]

    # One explicit startup object crosses the supervisor -> MCP -> host seam;
    # no second coordinator is constructed by a transport or CLI adapter.
    assert _keyword_call_exists(
        "aar.runtime.supervisor", "_start", "build_server", "provider_ready_startup"
    )
    assert _keyword_call_exists(
        "aar.mcp.server", "build_server", "ReferenceHost", "provider_ready_startup"
    )
    assert _positional_call_exists(
        "aar.runtime.reference_host",
        "__init__",
        "provider_ready_startup.activate",
        0,
        "self.runtime_generation",
    )
    assert (
        "provider_ready_startup.activate"
        in _calls_by_function("aar.runtime.reference_host")["__init__"]
    )


def test_direct_platform_primitive_has_one_stage_owner_and_no_second_authority() -> None:
    """A-NEC-001: exact symbol inventory falsifies a second durable mechanism."""

    assert _definitions_named("_create_stage", MECHANISM_MODULES) == ["aar.runtime._install_fs"]
    assert _definitions_named("_publish", MECHANISM_MODULES) == ["aar.runtime._install_fs"]
    assert _definitions_named("StageHandle", MECHANISM_MODULES) == ["aar.runtime._install_fs"]
    assert _definitions_named("CleanInstallPreparation", MECHANISM_MODULES) == [
        "aar.provider_ready_install_models"
    ]
    forbidden_class_names = {
        "AttemptAllocator",
        "CredentialStore",
        "DaemonAuthority",
        "ProviderClient",
        "RecoveryLedger",
        "SecondStageOwner",
        "WorkbenchStore",
    }
    for module in MECHANISM_MODULES:
        classes = {
            node.name for node in ast.walk(_module_tree(module)) if isinstance(node, ast.ClassDef)
        }
        assert not classes & forbidden_class_names, module
        assert not set(_direct_imports(module)) & set(FORBIDDEN_EXTERNAL_MODULES), module
    assert _definitions_named("ProviderReadyActivationStore", MECHANISM_MODULES) == [
        "aar.runtime.provider_ready_activation"
    ]


def test_identity_owners_share_one_canonical_posix_utf8_algorithm(tmp_path: Path) -> None:
    """A-CLEAN-001 and A-IDENT-001: four owners bind one semantic identity."""

    calls = _calls_by_function("aar.runtime._install_fs")
    assert {"compute_runtime_home_digest", "compute_database_identity"} <= calls[
        "_preflight_target_with_chain"
    ]
    assert {"compute_runtime_home_digest", "compute_database_identity"} <= _calls_by_function(
        "aar.runtime._install_evidence"
    )["verify_published_install"]
    assert (
        "_preflight_target_with_chain"
        in _calls_by_function("aar.runtime.installer")["install_clean_runtime"]
    )
    assert (
        "verify_published_install"
        in _calls_by_function("aar.runtime.provider_ready_startup")["preflight"]
    )

    target = (tmp_path / "runtime-é").resolve()
    expected = canonical_sha256(
        {
            "database": f"{target.as_posix()}/{DATABASE_NAME}",
            "runtime_home": target.as_posix(),
        }
    )
    assert compute_runtime_home_digest(target) == expected
    readback = preflight_target(target)
    assert readback.canonical_target == target
    assert readback.runtime_home_digest == expected
    assert readback.database_identity == compute_database_identity(expected)
    assert not target.exists(), "identity preflight must not create the target"

    # Requirement wording is byte-preserving, not alias-rejecting: these values
    # must remain distinct and are not case-folded or Unicode-normalized.
    assert compute_runtime_home_digest(Path("/tmp/Provider-Ready")) != compute_runtime_home_digest(
        Path("/tmp/provider-ready")
    )
    composed = Path("/tmp/provider-ready-\u00e9")
    decomposed = Path("/tmp/provider-ready-e\u0301")
    assert compute_runtime_home_digest(composed) != compute_runtime_home_digest(decomposed)
    with pytest.raises(InstallerError) as relative_error:
        preflight_target("relative/provider-ready")
    assert relative_error.value.code == "FRESH_INSTALL_PATH_UNSAFE"
    with pytest.raises(InstallerError) as encoding_error:
        preflight_target(b"/tmp/provider-ready-" + bytes([0xFF]))
    assert encoding_error.value.code == "FRESH_INSTALL_PATH_UNSAFE"


def test_database_identity_is_db_token_over_only_semantic_identity() -> None:
    """A-IDENT-001: db- plus 64 lowercase hex excludes inode/dev/staging."""

    runtime_digest = "sha256:" + "a" * 64
    material = {
        "schema_version": "aar.clean-install-database-identity.v1",
        "runtime_home_digest": runtime_digest,
        "database_name": DATABASE_NAME,
    }
    expected = "db-" + canonical_sha256(material).removeprefix("sha256:")
    observed = compute_database_identity(runtime_digest)
    assert observed == expected
    assert re.fullmatch(r"db-[0-9a-f]{64}", observed)
    assert compute_database_identity("sha256:" + "b" * 64) != observed
    assert set(material) == {"schema_version", "runtime_home_digest", "database_name"}


def test_clean_install_projections_have_exact_path_free_field_sets() -> None:
    """A-V6-PROJ-002..004: preparation/authority/snapshot roots are exact."""

    preparation = _preparation()
    expected_fields = {
        "schema_version",
        "install_epoch",
        "runtime_home_digest",
        "database_identity",
        "database_name",
        "empty_v5_backup_digest",
        "empty_v5_backup_size_bytes",
        "canonical_v5_row_set_digest",
        "intent_digest",
        "candidate",
        "migration_sql_digest",
        "projected_external_authority_store_id",
    }
    assert set(CleanInstallPreparation.model_fields) == expected_fields
    assert set(preparation.model_dump(mode="json")) == expected_fields
    assert set(CleanInstallCandidateProjection.model_fields) == {
        "source_commit",
        "wheel_digest",
        "contract_manifest_digest",
        "skill_digest",
    }
    serialized = canonical_json_bytes(preparation.model_dump(mode="json"))
    for forbidden in (b"staging", b"stage", b"inode", b"device", b"st_dev", b"path"):
        assert forbidden not in serialized
    assert preparation.external_authority_prepared_digest == canonical_sha256(
        preparation.model_dump(mode="json")
    )

    authority_input = "local-file-authority-v1:runtime-primary"
    authority_material = {
        "schema_version": "aar.clean-install-authority-projection.v1",
        "intent_authority_store_id": authority_input,
    }
    authority = project_authority_store_id(authority_input)
    assert authority == "authority-" + canonical_sha256(authority_material).removeprefix("sha256:")
    assert re.fullmatch(r"authority-[0-9a-f]{64}", authority)
    assert ":" not in authority
    assert project_authority_store_id(authority_input + "-changed") != authority

    snapshot_inputs = (
        "sha256:" + "c" * 64,
        4096,
        "install-" + "e" * 64,
    )
    snapshot_material = {
        "schema_version": "aar.clean-install-snapshot-id.v1",
        "empty_v5_backup_digest": snapshot_inputs[0],
        "empty_v5_backup_size_bytes": snapshot_inputs[1],
        "install_epoch": snapshot_inputs[2],
    }
    snapshot = project_snapshot_id(*snapshot_inputs)
    assert snapshot == "empty-v5-" + canonical_sha256(snapshot_material).removeprefix("sha256:")
    assert re.fullmatch(r"empty-v5-[0-9a-f]{64}", snapshot)
    assert ":" not in snapshot
    assert (
        len(
            {
                project_snapshot_id(snapshot_inputs[0], snapshot_inputs[1], snapshot_inputs[2]),
                project_snapshot_id("sha256:" + "d" * 64, snapshot_inputs[1], snapshot_inputs[2]),
                project_snapshot_id(snapshot_inputs[0], snapshot_inputs[1] + 1, snapshot_inputs[2]),
                project_snapshot_id(snapshot_inputs[0], snapshot_inputs[1], "install-" + "f" * 64),
            }
        )
        == 4
    )


def test_zero_size_snapshot_and_staging_identity_are_rejected_at_target_validator() -> None:
    """A-CLEAN-003/004: zero-size is invalid while no path/inode/dev is projected."""

    payload = _attestation().attestation.model_dump(mode="json")
    payload["snapshot_size_bytes"] = 0
    with pytest.raises(ValidationError) as error:
        MigrationAttestationPayload.model_validate_json(canonical_json_bytes(payload), strict=True)
    assert any(item["loc"] == ("snapshot_size_bytes",) for item in error.value.errors())
    assert set(payload) - {"snapshot_size_bytes"} == set(
        _attestation().attestation.model_dump(mode="json")
    ) - {"snapshot_size_bytes"}
    assert not {"staging_path", "inode", "device", "st_dev"} & set(payload)


def test_attestation_timestamp_order_is_discriminating() -> None:
    """A-V6-PROJ-005: an otherwise-valid payload rejects only reversed time."""

    payload = _attestation().attestation.model_dump(mode="json")
    payload["started_at_unix_ms"] = 102
    payload["completed_at_unix_ms"] = 101
    with pytest.raises(ValidationError) as error:
        MigrationAttestationPayload.model_validate_json(canonical_json_bytes(payload), strict=True)
    assert "started_at_unix_ms" in str(error.value)
    valid = _attestation().attestation
    assert valid.started_at_unix_ms <= valid.completed_at_unix_ms


def test_attestation_self_digest_binds_every_payload_field() -> None:
    """A-V6-PROJ-006: stale and repaired roots distinguish digest coverage."""

    document = _attestation()
    wire = document.model_dump(mode="json")
    assert document.attestation_digest == canonical_sha256(wire["attestation"])
    wire["attestation"]["snapshot_size_bytes"] += 1
    with pytest.raises(ValidationError, match="attestation digest"):
        MigrationAttestationDocument.model_validate_json(canonical_json_bytes(wire), strict=True)
    wire["attestation_digest"] = canonical_sha256(wire["attestation"])
    repaired = MigrationAttestationDocument.model_validate_json(
        canonical_json_bytes(wire), strict=True
    )
    assert repaired.attestation.snapshot_size_bytes == document.attestation.snapshot_size_bytes + 1
    assert repaired.attestation_digest == canonical_sha256(wire["attestation"])


def test_evaluation_models_are_inert_under_local_import_and_call_graph() -> None:
    """A-EVAL-001: no T5 launcher, physical attempt, rerun, or winner authority."""

    graph = _local_import_graph(EVALUATION_MODULE)
    forbidden_prefixes = (
        "aar.mcp",
        "aar.providers",
        "aar.runtime",
        "http",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    )
    assert not any(module.startswith(forbidden_prefixes) for module in graph)
    for module in (EVALUATION_MODULE, EVALUATION_GENERATOR_MODULE):
        calls = _all_calls(module)
        assert not any(
            call.startswith(("subprocess.", "socket.", "requests.", "httpx.", "urllib."))
            for call in calls
        )
        assert not calls & {
            "launch",
            "execute",
            "run",
            "rerun",
            "score",
            "winner",
            "allocate_physical_attempt",
            "start_provider",
        }
    assert not _definitions_named("main", (EVALUATION_MODULE, EVALUATION_GENERATOR_MODULE))
    assert not _definitions_named("launch", (EVALUATION_MODULE, EVALUATION_GENERATOR_MODULE))
    assert not _definitions_named("execute", (EVALUATION_MODULE, EVALUATION_GENERATOR_MODULE))
    assert not _definitions_named("rerun", (EVALUATION_MODULE, EVALUATION_GENERATOR_MODULE))


def test_inert_identifier_fields_and_semantic_canaries_are_rejected() -> None:
    """A-SEC-001: only digest-shaped executable/credential references survive."""

    model_modules = (
        "aar.provider_ready_install_models",
        "aar.provider_ready_evaluation_models",
        "aar.provider_ready_models",
        "aar.provider_ready_operator_models",
        "aar.provider_ready_runtime_models",
    )
    for module in model_modules:
        assert not _field_names(module) & FORBIDDEN_FIELD_NAMES, module
    for path in _persisted_contract_paths():
        if path.suffix.lower() == ".json":
            try:
                document = json.loads(path.read_bytes())
            except (UnicodeDecodeError, json.JSONDecodeError):
                # Invalid-byte fixtures are still covered by the raw-byte
                # canary below; they have no trustworthy JSON key inventory.
                continue
            assert not _json_keys(document) & FORBIDDEN_FIELD_NAMES, path

    alias_path = (
        ROOT / "tests" / "fixtures" / "provider-ready" / "valid" / "provider-alias-attestation.json"
    )
    alias = json.loads(alias_path.read_text(encoding="utf-8"))
    ProviderAliasAttestation.model_validate_json(alias_path.read_bytes(), strict=True)
    for field in ("command", "endpoint", "import_path", "credential"):
        mutated = {**alias, field: "[REDACTED]"}
        with pytest.raises(ValidationError) as error:
            ProviderAliasAttestation.model_validate(mutated, strict=True)
        assert any(item["loc"] == (field,) for item in error.value.errors())


def test_persisted_install_evidence_and_log_bytes_have_secret_canaries() -> None:
    """A-SEC-001: scan every checked-in contract/evidence/log byte, not keywords."""

    for path in _persisted_contract_paths():
        assert not _secret_hits(path.read_bytes()), path
    synthetic_aws = b"AKIA" + b"0" * 16
    synthetic_secret = b"secret=" + b"x" * 12
    assert "aws_access_key" in _secret_hits(synthetic_aws)
    assert "named_secret" in _secret_hits(synthetic_secret)


@pytest.mark.parametrize(
    ("module", "function", "expected_calls"),
    (
        (
            "aar.runtime._install_fs",
            "_preflight_target_with_chain",
            {"compute_runtime_home_digest", "compute_database_identity"},
        ),
        (
            "aar.runtime._install_evidence",
            "verify_published_install",
            {"compute_runtime_home_digest", "compute_database_identity"},
        ),
        (
            "aar.runtime.installer",
            "install_clean_runtime",
            {"_preflight_target_with_chain", "_create_stage", "_publish"},
        ),
        (
            "aar.runtime.provider_ready_startup",
            "preflight",
            {"verify_published_install"},
        ),
    ),
)
def test_identity_and_publication_owner_call_edges_are_explicit(
    module: str, function: str, expected_calls: set[str]
) -> None:
    """A-CLEAN-001: the static four-owner inventory cannot silently fork."""

    assert expected_calls <= _calls_by_function(module)[function]
