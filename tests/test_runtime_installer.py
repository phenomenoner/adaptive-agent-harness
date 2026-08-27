from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from aar import admin
from aar.broker_models import ModelRouteCatalog, ModelRouteProfile
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_install_models import (
    CleanInstallPreparation,
    FactoryEntry,
    InstallCandidateReceipt,
    MigrationAttestationDocument,
)
from aar.provider_ready_models import (
    ActivationGrantPolicy,
    ActivationPlannerBinding,
    ActivationRoutePolicy,
    ActivationRuntimeBinding,
    GrantBudgetCeiling,
    HostActivationIntent,
    MethodAdapterManifest,
    ProviderReadyCandidate,
)
from aar.provider_ready_operator_inputs import issue_initial_host_activation_intent
from aar.provider_ready_package_factory import (
    PACKAGE_FACTORY_DECLARATIONS,
    PACKAGE_FACTORY_WHEEL_MEMBER,
    factory_entries_from_member_digests,
)
from aar.provider_ready_runtime_models import (
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    ActivationReadback,
    WorkbenchGrantSet,
)
from aar.runtime import _install_evidence as evidence_module
from aar.runtime import _install_fs as fs_module
from aar.runtime import _install_sqlite as sqlite_module
from aar.runtime import installer as installer_module
from aar.runtime._install_artifacts import inspect_wheel_bytes, validate_receipt_against_intent
from aar.runtime.installer import (
    BACKUP_NAME,
    DATABASE_NAME,
    HISTORY_AUTHORITY_NAME,
    InstallerError,
    PublicationIndeterminate,
    SQLiteStageAdapter,
    compute_database_identity,
    compute_runtime_home_digest,
    install_clean_runtime,
    verify_published_install,
)
from aar.runtime.migrations import V6_STATEMENT_NAMES
from aar.runtime.ownership import RuntimeOwnershipLock
from aar.runtime.provider_ready_activation import ProviderReadyActivationStore
from aar.runtime.reference_host import ReferenceHost
from aar.runtime.registry import OperationRegistry
from aar.versions import PACKAGE_VERSION

DIGEST = canonical_sha256({"fixture": "clean-install"})
ZERO_COMMIT = "0" * 40
EPOCH = "install-" + "1" * 64
V6_BOUNDARIES = (
    *(
        f"fail_after_statement_{index:02d}_{name}"
        for index, name in enumerate(V6_STATEMENT_NAMES, start=1)
    ),
    "before-first-statement",
    "before-attestation-insert",
    "before-schema-migration-insert",
    "before-commit",
    "after-commit-readback",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())


def _path_snapshot(path: Path) -> tuple[Any, ...]:
    """Capture type, identity, and bytes without following symlinks."""

    if path.is_symlink():
        observed = path.lstat()
        return ("symlink", observed.st_dev, observed.st_ino, os.readlink(path))
    if path.is_dir():
        observed = path.stat()
        children = tuple(
            (child.name, _path_snapshot(child))
            for child in sorted(path.iterdir(), key=lambda item: item.name)
        )
        return (
            "directory",
            observed.st_dev,
            observed.st_ino,
            stat.S_IMODE(observed.st_mode),
            children,
        )
    if path.is_file():
        observed = path.stat()
        return (
            "file",
            observed.st_dev,
            observed.st_ino,
            stat.S_IMODE(observed.st_mode),
            path.read_bytes(),
        )
    if path.exists():
        observed = path.lstat()
        return ("other", observed.st_dev, observed.st_ino, observed.st_mode)
    return ("missing",)


def _stage_names(parent: Path) -> tuple[str, ...]:
    return tuple(sorted(path.name for path in parent.glob(".runtime.install-*")))


def _directory_chain_snapshot(path: Path) -> tuple[tuple[str, int, int], ...]:
    chain: list[tuple[str, int, int]] = []
    current = path
    while True:
        observed = current.stat()
        chain.append((current.as_posix(), observed.st_dev, observed.st_ino))
        if current == Path(os.sep):
            break
        current = current.parent
    return tuple(reversed(chain))


def _wheel_bytes(repo: Path) -> tuple[bytes, dict[str, bytes]]:
    schema = (repo / "schemas" / "aar-provider-ready-schemas-v1.json").read_bytes()
    manifest = (repo / "tests" / "fixtures" / "provider-ready" / "manifest.json").read_bytes()
    skill = b"clean-install test skill\n"
    files: dict[str, bytes] = {
        "aar/bundled/schemas/aar-provider-ready-schemas-v1.json": schema,
        "aar/bundled/fixtures/provider-ready/manifest.json": manifest,
        "aar/bundled/aar-operations/SKILL.md": skill,
    }
    manifest_obj = json.loads(manifest)
    for fixture in manifest_obj["fixtures"]:
        path = "aar/bundled/fixtures/provider-ready/" + fixture["path"]
        fixture_path = repo / "tests" / "fixtures" / "provider-ready" / fixture["path"]
        files[path] = fixture_path.read_bytes()
    files[PACKAGE_FACTORY_WHEEL_MEMBER] = (repo / "src" / PACKAGE_FACTORY_WHEEL_MEMBER).read_bytes()
    files[f"adaptive_agent_runtime-{PACKAGE_VERSION}.dist-info/METADATA"] = (
        "Metadata-Version: 2.4\n"
        "Name: adaptive-agent-runtime\n"
        f"Version: {PACKAGE_VERSION}\n"
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name in sorted(files):
            archive.writestr(name, files[name])
    return output.getvalue(), files


def _candidate_and_factory_data(
    wheel: bytes, files: dict[str, bytes]
) -> tuple[ProviderReadyCandidate, tuple[FactoryEntry, ...], dict[str, str]]:
    manifest = json.loads(files["aar/bundled/fixtures/provider-ready/manifest.json"])
    bundle = json.loads(files["aar/bundled/schemas/aar-provider-ready-schemas-v1.json"])
    contract_digest = canonical_sha256(
        {
            "schema_bundle_digest": bundle["bundle_digest"],
            "fixture_set_digest": manifest["fixture_set_digest"],
        }
    )
    skill_digest = (
        "sha256:" + hashlib.sha256(files["aar/bundled/aar-operations/SKILL.md"]).hexdigest()
    )
    candidate = ProviderReadyCandidate(
        package_version=PACKAGE_VERSION,
        source_commit=ZERO_COMMIT,
        wheel_digest="sha256:" + hashlib.sha256(wheel).hexdigest(),
        contract_manifest_digest=contract_digest,
        skill_digest=skill_digest,
    )
    member_digests = {
        name: "sha256:" + hashlib.sha256(content).hexdigest() for name, content in files.items()
    }
    entries = factory_entries_from_member_digests(member_digests)
    factory_digests = {
        declaration.factory_id: member_digests[declaration.wheel_member]
        for declaration in PACKAGE_FACTORY_DECLARATIONS
    }
    return candidate, entries, factory_digests


def _intent(
    target: Path,
    candidate: ProviderReadyCandidate,
    factory_digests: dict[str, str],
) -> HostActivationIntent:
    adapters = []
    for declaration in PACKAGE_FACTORY_DECLARATIONS:
        short = declaration.method.replace(".", "-")
        adapters.append(
            MethodAdapterManifest.issue(
                schema_version="aar.method-adapter-manifest.v1",
                method=declaration.method,
                contract_id=declaration.contract_id,
                request_schema_digest=declaration.request_schema_digest,
                response_schema_digest=declaration.response_schema_digest,
                backend_kind="native",
                factory_id=declaration.factory_id,
                factory_digest=factory_digests[declaration.factory_id],
                adapter_id=f"adapter-{short}-v1",
                adapter_generation_policy="runtime_generation",
                reference_only=False,
                evidence_tier="host_receipt_bound",
                lookup_supported=True,
                cancel_supported=True,
            )
        )
    budget = GrantBudgetCeiling(
        wall_time_ms=1000,
        model_requests=1,
        input_tokens=1,
        output_tokens=1,
        child_operations=0,
        artifact_bytes=0,
    )
    runtime_digest = compute_runtime_home_digest(target)
    runtime = ActivationRuntimeBinding(
        runtime_home_digest=runtime_digest,
        database_identity=compute_database_identity(runtime_digest),
        required_registry_version=6,
        programmable_backend="ipython",
        security_profile="trusted_local",
    )
    planner = ActivationPlannerBinding(
        mode="service_managed",
        method="model.request",
        directive_schema_version="aar.rlm-directive.v1",
        directive_schema_digest=DIGEST,
    )
    routes = ActivationRoutePolicy.issue(
        catalog_digest=DIGEST,
        allowed_profile_ids=("route-primary",),
        fallback_policy="none",
        cache_policy="disabled",
    )
    grant_policy = ActivationGrantPolicy.issue(
        principal_patterns=("principal-local",),
        capabilities=("aar-capability-model-request",),
        budget_ceiling=budget,
        max_deadline_ms=1000,
    )
    return HostActivationIntent.issue(
        schema_version="aar.host-activation-intent.v1",
        profile_id="profile-primary",
        activation_generation=1,
        previous_activation_authority_digest=None,
        candidate=candidate,
        runtime=runtime,
        planner=planner,
        adapters=tuple(adapters),
        routes=routes,
        grant_policy=grant_policy,
        cutover_authority_store_id="local-file-authority-v1:primary",
        recovery_compatibility_digest=DIGEST,
    )


def _inputs_for_wheel(
    tmp_path: Path,
    wheel: bytes,
    files: dict[str, bytes],
    *,
    target: Path | None = None,
) -> tuple[Path, Path, Path, Path]:
    target = target or tmp_path / "runtime"
    candidate, entries, factory_digests = _candidate_and_factory_data(wheel, files)
    receipt = InstallCandidateReceipt.issue(
        candidate=candidate,
        wheel_size_bytes=len(wheel),
        wheel_digest=candidate.wheel_digest,
        contract_manifest_digest=candidate.contract_manifest_digest,
        skill_digest=candidate.skill_digest,
        factory_entries=entries,
    )
    intent = _intent(target, candidate, factory_digests)
    intent_path = tmp_path / "intent.json"
    receipt_path = tmp_path / "receipt.json"
    wheel_path = tmp_path / "candidate.whl"
    intent_path.write_bytes(canonical_json_bytes(intent.model_dump(mode="json")))
    receipt_path.write_bytes(canonical_json_bytes(receipt.model_dump(mode="json")))
    wheel_path.write_bytes(wheel)
    return target, intent_path, receipt_path, wheel_path


def test_public_initial_intent_issuer_reconstructs_target_bound_authority(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, _wheel_path = _inputs(tmp_path, repo)
    expected_template = HostActivationIntent.model_validate_json(
        intent_path.read_bytes(), strict=True
    )
    catalog = ModelRouteCatalog.issue(
        (
            ModelRouteProfile(
                profile_id="route-primary",
                provider_driver="host-caller-driver-v1",
                provider="test-provider",
                model="test-model",
                reasoning_effort="high",
                max_output_tokens=8192,
                fallback_policy="none",
                cache_policy="disabled",
            ),
        )
    )
    catalog_path = tmp_path / "route-catalog.json"
    catalog_path.write_bytes(canonical_json_bytes(catalog.model_dump(mode="json")))
    expected = HostActivationIntent.issue(
        schema_version=expected_template.schema_version,
        profile_id=expected_template.profile_id,
        activation_generation=1,
        previous_activation_authority_digest=None,
        candidate=expected_template.candidate,
        runtime=expected_template.runtime,
        planner=expected_template.planner,
        adapters=expected_template.adapters,
        routes=ActivationRoutePolicy.issue(
            catalog_digest=catalog.catalog_digest,
            allowed_profile_ids=expected_template.routes.allowed_profile_ids,
            fallback_policy=expected_template.routes.fallback_policy,
            cache_policy=expected_template.routes.cache_policy,
        ),
        grant_policy=expected_template.grant_policy,
        cutover_authority_store_id=expected_template.cutover_authority_store_id,
        recovery_compatibility_digest=expected_template.recovery_compatibility_digest,
    )
    template = {
        key: expected_template.model_dump(mode="json")[key]
        for key in (
            "profile_id",
            "planner",
            "adapters",
            "routes",
            "grant_policy",
            "cutover_authority_store_id",
            "recovery_compatibility_digest",
        )
    }
    template_path = tmp_path / "intent-template.json"
    template_path.write_bytes(canonical_json_bytes(template))

    issued = issue_initial_host_activation_intent(
        target,
        candidate_receipt_path=receipt_path,
        route_catalog_path=catalog_path,
        template_path=template_path,
    )

    assert issued == expected
    assert not target.exists()

    assert (
        admin.main(
            [
                "activation",
                "intent",
                "--runtime-home",
                str(target),
                "--candidate-receipt",
                str(receipt_path),
                "--route-catalog",
                str(catalog_path),
                "--template",
                str(template_path),
            ]
        )
        == 0
    )
    cli_issued = HostActivationIntent.model_validate_json(
        capsys.readouterr().out.encode(), strict=True
    )
    assert cli_issued == expected
    assert not target.exists()

    extra_template = dict(template)
    extra_template["candidate"] = expected.candidate.model_dump(mode="json")
    extra_path = tmp_path / "intent-template-extra.json"
    extra_path.write_bytes(canonical_json_bytes(extra_template))
    with pytest.raises(InstallerError, match="FRESH_INSTALL_INPUT_INVALID"):
        issue_initial_host_activation_intent(
            target,
            candidate_receipt_path=receipt_path,
            route_catalog_path=catalog_path,
            template_path=extra_path,
        )
    assert not target.exists()

    target.mkdir()
    with pytest.raises(InstallerError, match="FRESH_INSTALL_TARGET_EXISTS"):
        issue_initial_host_activation_intent(
            target,
            candidate_receipt_path=receipt_path,
            route_catalog_path=catalog_path,
            template_path=template_path,
        )


def test_admin_emits_exact_candidate_receipt(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path(__file__).parents[1]
    wheel, _files = _wheel_bytes(repo)
    wheel_path = tmp_path / "candidate.whl"
    wheel_path.write_bytes(wheel)

    assert (
        admin.main(
            [
                "runtime",
                "candidate",
                "--wheel",
                str(wheel_path),
                "--source-commit",
                ZERO_COMMIT,
            ]
        )
        == 0
    )
    issued = InstallCandidateReceipt.model_validate_json(
        capsys.readouterr().out.encode(), strict=True
    )
    assert issued.candidate.package_version == PACKAGE_VERSION
    assert issued.candidate.source_commit == ZERO_COMMIT
    assert issued.wheel_size_bytes == len(wheel)
    assert issued.wheel_digest == "sha256:" + hashlib.sha256(wheel).hexdigest()


def _inputs(tmp_path: Path, repo: Path) -> tuple[Path, Path, Path, Path]:
    wheel, files = _wheel_bytes(repo)
    return _inputs_for_wheel(tmp_path, wheel, files)


def test_duplicate_intent_factory_id_cannot_collapse_receipt_inventory(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    wheel_bytes, files = _wheel_bytes(repo)
    candidate, entries, factory_digests = _candidate_and_factory_data(wheel_bytes, files)
    intent = _intent(tmp_path / "runtime", candidate, factory_digests)
    first, second, *remaining = intent.adapters
    duplicate = MethodAdapterManifest.issue(
        schema_version=second.schema_version,
        method=second.method,
        contract_id=second.contract_id,
        request_schema_digest=second.request_schema_digest,
        response_schema_digest=second.response_schema_digest,
        backend_kind=second.backend_kind,
        factory_id=first.factory_id,
        factory_digest=first.factory_digest,
        adapter_id=second.adapter_id,
        adapter_generation_policy=second.adapter_generation_policy,
        reference_only=second.reference_only,
        evidence_tier=second.evidence_tier,
        lookup_supported=second.lookup_supported,
        cancel_supported=second.cancel_supported,
    )
    duplicate_intent = HostActivationIntent.issue(
        schema_version=intent.schema_version,
        profile_id=intent.profile_id,
        activation_generation=intent.activation_generation,
        previous_activation_authority_digest=intent.previous_activation_authority_digest,
        candidate=intent.candidate,
        runtime=intent.runtime,
        planner=intent.planner,
        adapters=(first, duplicate, *remaining),
        routes=intent.routes,
        grant_policy=intent.grant_policy,
        cutover_authority_store_id=intent.cutover_authority_store_id,
        recovery_compatibility_digest=intent.recovery_compatibility_digest,
    )
    collapsed_entries = tuple(entry for entry in entries if entry.factory_id != second.factory_id)
    receipt = InstallCandidateReceipt.issue(
        candidate=candidate,
        wheel_size_bytes=len(wheel_bytes),
        wheel_digest=candidate.wheel_digest,
        contract_manifest_digest=candidate.contract_manifest_digest,
        skill_digest=candidate.skill_digest,
        factory_entries=collapsed_entries,
    )
    wheel = inspect_wheel_bytes(wheel_bytes, receipt)

    with pytest.raises(InstallerError, match="unique package factory") as raised:
        validate_receipt_against_intent(receipt, duplicate_intent, wheel)

    assert raised.value.code == "FRESH_INSTALL_FACTORY_MISMATCH"


def test_clean_install_round_trip_and_exact_initial_authority(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    result = install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    assert result.install_epoch == EPOCH
    assert result.authority.activation_generation == 1
    assert result.authority.previous_activation_authority_digest is None
    assert result.attestation.attestation.profile_digest == result.preparation.intent_digest
    assert not (target / BACKUP_NAME).exists()
    readback = verify_published_install(target)
    assert readback.database_versions == (1, 2, 3, 4, 5, 6)
    assert readback.authority == result.authority
    assert readback.profile == result.profile
    assert (target / "authority" / "current.json").read_bytes() == (
        target / "authority" / "history" / HISTORY_AUTHORITY_NAME
    ).read_bytes()


def test_runtime_identity_is_equal_at_issuer_installer_stage_and_startup_owners(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    identity = installer_module.preflight_target(target)
    expected_runtime_digest = canonical_sha256(
        {
            "database": f"{target.as_posix()}/{DATABASE_NAME}",
            "runtime_home": target.as_posix(),
        }
    )
    expected_database_identity = "db-" + canonical_sha256(
        {
            "schema_version": "aar.clean-install-database-identity.v1",
            "runtime_home_digest": expected_runtime_digest,
            "database_name": DATABASE_NAME,
        }
    ).removeprefix("sha256:")
    staged_preparations: list[Any] = []
    original_verify = installer_module._verify_staged_evidence

    def capture_staged(*args: Any, **kwargs: Any) -> None:
        staged_preparations.append(kwargs["preparation"])
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(installer_module, "_verify_staged_evidence", capture_staged)
    result = install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    startup = verify_published_install(target)
    assert _read_json(intent_path)["runtime"]["runtime_home_digest"] == expected_runtime_digest
    assert identity.runtime_home_digest == expected_runtime_digest
    assert identity.database_identity == expected_database_identity
    assert staged_preparations[0].runtime_home_digest == expected_runtime_digest
    assert staged_preparations[0].database_identity == expected_database_identity
    assert result.runtime_home_digest == expected_runtime_digest
    assert result.database_identity == expected_database_identity
    assert startup.runtime_home_digest == expected_runtime_digest
    assert startup.database_identity == expected_database_identity


def test_path_free_projections_have_independent_sources_and_positive_snapshot_size(
    tmp_path: Path,
) -> None:
    authority_source = "local-file-authority-v1:primary"
    projected_authority = installer_module.project_authority_store_id(authority_source)
    expected_authority = "authority-" + canonical_sha256(
        {
            "schema_version": "aar.clean-install-authority-projection.v1",
            "intent_authority_store_id": authority_source,
        }
    ).removeprefix("sha256:")
    projected_snapshot = installer_module.project_snapshot_id(DIGEST, 123, EPOCH)
    expected_snapshot = "empty-v5-" + canonical_sha256(
        {
            "schema_version": "aar.clean-install-snapshot-id.v1",
            "empty_v5_backup_digest": DIGEST,
            "empty_v5_backup_size_bytes": 123,
            "install_epoch": EPOCH,
        }
    ).removeprefix("sha256:")
    assert projected_authority == expected_authority
    assert projected_snapshot == expected_snapshot
    assert ":" not in projected_authority and "/" not in projected_snapshot

    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    result = install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    invalid = result.preparation.model_dump(mode="json")
    invalid["empty_v5_backup_size_bytes"] = 0
    with pytest.raises(ValidationError) as raised:
        CleanInstallPreparation.model_validate(invalid)
    assert any(
        error["loc"] == ("empty_v5_backup_size_bytes",) and error["type"] == "greater_than"
        for error in raised.value.errors()
    )


def test_startup_readback_is_read_only_and_tree_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _installed_runtime(tmp_path)
    before = _path_snapshot(target)
    original_connect = sqlite3.connect
    write_actions: list[int] = []
    write_opcodes = {
        sqlite3.SQLITE_CREATE_INDEX,
        sqlite3.SQLITE_CREATE_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_INDEX,
        sqlite3.SQLITE_CREATE_TEMP_TABLE,
        sqlite3.SQLITE_CREATE_TEMP_TRIGGER,
        sqlite3.SQLITE_CREATE_TEMP_VIEW,
        sqlite3.SQLITE_CREATE_TRIGGER,
        sqlite3.SQLITE_CREATE_VIEW,
        sqlite3.SQLITE_DELETE,
        sqlite3.SQLITE_DROP_INDEX,
        sqlite3.SQLITE_DROP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_INDEX,
        sqlite3.SQLITE_DROP_TEMP_TABLE,
        sqlite3.SQLITE_DROP_TEMP_TRIGGER,
        sqlite3.SQLITE_DROP_TEMP_VIEW,
        sqlite3.SQLITE_DROP_TRIGGER,
        sqlite3.SQLITE_DROP_VIEW,
        sqlite3.SQLITE_INSERT,
        sqlite3.SQLITE_UPDATE,
        sqlite3.SQLITE_ALTER_TABLE,
    }

    def read_only_connect(*args: Any, **kwargs: Any) -> sqlite3.Connection:
        connection = original_connect(*args, **kwargs)

        def authorizer(action: int, *_details: Any) -> int:
            if action in write_opcodes:
                write_actions.append(action)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        return connection

    monkeypatch.setattr(evidence_module.sqlite3, "connect", read_only_connect)
    readback = verify_published_install(target)
    assert readback.database_versions == (1, 2, 3, 4, 5, 6)
    assert write_actions == []
    assert _path_snapshot(target) == before


def test_startup_distribution_member_drift_fails_before_runtime_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _installed_runtime(tmp_path)
    before = _path_snapshot(target)

    def reject_installed_members(_receipt: object) -> None:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED",
            "installed distribution member differs from receipt",
        )

    monkeypatch.setattr(
        evidence_module,
        "verify_installed_distribution_members",
        reject_installed_members,
    )

    with pytest.raises(InstallerError) as raised:
        verify_published_install(
            target,
            allow_runtime_state=True,
            verify_distribution_members=True,
        )

    assert raised.value.code == "FRESH_INSTALL_READBACK_FAILED"
    assert _path_snapshot(target) == before
    assert not (target / "authority" / "runtime-generations").exists()
    assert not (target / "supervisor").exists()


def test_successful_install_rerun_rejects_target_without_replacement(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    before = _path_snapshot(target)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert raised.value.code == "FRESH_INSTALL_TARGET_EXISTS"
    assert _path_snapshot(target) == before
    assert _stage_names(tmp_path) == ()


@pytest.mark.parametrize("variant", ["fresh-host", "untouched-legacy-root"])
def test_clean_install_does_not_adopt_or_mutate_an_old_root(
    tmp_path: Path, variant: str
) -> None:
    repo = Path(__file__).parents[1]
    base = tmp_path / "host"
    base.mkdir()
    new_target = base / "new-runtime"
    old_root = base / "legacy-runtime"
    wheel, files = _wheel_bytes(repo)
    target, intent_path, receipt_path, wheel_path = _inputs_for_wheel(
        base,
        wheel,
        files,
        target=new_target,
    )
    before_old_root: tuple[Any, ...] | None = None
    if variant == "untouched-legacy-root":
        (old_root / "authority").mkdir(parents=True, mode=0o700)
        (old_root / "authority" / "current.json").write_bytes(b"legacy-authority")
        (old_root / "legacy-marker").write_bytes(b"v5-root")
        before_old_root = _path_snapshot(old_root)
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    profile = _read_json(target / "authority" / "profile.json")
    assert profile["intent"]["runtime"]["runtime_home_digest"] == canonical_sha256(
        {
            "database": f"{new_target.as_posix()}/{DATABASE_NAME}",
            "runtime_home": new_target.as_posix(),
        }
    )
    assert not (base / "legacy-runtime.archive").exists()
    assert not (base / "old-root.archive").exists()
    if before_old_root is None:
        assert not old_root.exists()
    else:
        assert _path_snapshot(old_root) == before_old_root


def _installed_runtime(tmp_path: Path) -> Path:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    return target


def test_installed_v6_activation_verify_reports_reconcile_without_mutation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = _installed_runtime(tmp_path)
    before = _path_snapshot(target)

    assert (
        admin.main(
            [
                "activation",
                "verify",
                "--runtime-home",
                str(target),
                "--profile",
                str(target / "authority" / "profile.json"),
            ]
        )
        == 0
    )

    captured = capsys.readouterr()
    readback = ActivationReadback.model_validate_json(captured.out, strict=True)
    assert readback.state == "recovery_required"
    assert readback.reason_code == "RECONCILE_INPUT_REQUIRED"
    assert readback.registry_schema_version == 6
    assert readback.evidence_sources == ("registry",)
    assert "REGISTRY_VERSION_UNSUPPORTED" not in captured.out
    assert captured.err == ""
    assert _path_snapshot(target) == before


def test_startup_readback_accepts_only_valid_postpublication_runtime_state(
    tmp_path: Path,
) -> None:
    target = _installed_runtime(tmp_path)
    initial = verify_published_install(target)
    registry = OperationRegistry(target / DATABASE_NAME, lambda: 1235)
    try:
        runtime_generation = registry.start_runtime()
    finally:
        registry.close()
    grant_policy = initial.profile.intent.grant_policy
    grant_set = WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=runtime_generation,
        activation_generation=initial.profile.intent.activation_generation,
        profile_id=initial.profile.intent.profile_id,
        profile_digest=initial.profile.profile_digest,
        activation_authority_digest=initial.authority.authority_digest,
        capability_digest=DIGEST,
        route_catalog_digest=initial.profile.intent.routes.catalog_digest,
        principal_ids=grant_policy.principal_patterns,
        session_binding_policy="bind_exact_request_session",
        capabilities=grant_policy.capabilities,
        budget_ceiling=grant_policy.budget_ceiling,
        max_ttl_ms=grant_policy.max_deadline_ms,
    )
    store = ProviderReadyActivationStore(
        target / "authority",
        current_runtime_generation=runtime_generation,
    )
    store.activate(
        runtime_generation=runtime_generation,
        grant_set=grant_set,
        activation_generation=initial.profile.intent.activation_generation,
        profile_id=initial.profile.intent.profile_id,
        profile_digest=initial.profile.profile_digest,
        capability_digest=DIGEST,
        activation_authority_digest=initial.authority.authority_digest,
        route_catalog_digest=initial.profile.intent.routes.catalog_digest,
    )
    host = ReferenceHost(target / DATABASE_NAME, programmable_backend="plain")
    host.close()
    supervisor = target / "supervisor"
    supervisor.mkdir(mode=0o700)
    with RuntimeOwnershipLock(target / DATABASE_NAME) as ownership:
        assert ownership.path == (target / DATABASE_NAME).resolve()
        assert not (supervisor / "runtime-owner.lock").exists()

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(target)

    startup = verify_published_install(target, allow_runtime_state=True)
    assert startup.profile == initial.profile
    assert startup.authority == initial.authority
    assert startup.database_versions == (1, 2, 3, 4, 5, 6)


@pytest.mark.parametrize("document", ["attestation", "profile"])
def test_startup_damaged_attestation_or_profile_fails_before_runtime_state(
    tmp_path: Path, document: str
) -> None:
    target = _installed_runtime(tmp_path)
    if document == "attestation":
        path = target / "authority" / "migration-attestation.json"
        payload = _read_json(path)
        payload["attestation"]["snapshot_id"] = "empty-v5-" + "f" * 64
        payload["attestation_digest"] = canonical_sha256(payload["attestation"])
    else:
        path = target / "authority" / "profile.json"
        payload = _read_json(path)
        payload["migration_attestation_digest"] = canonical_sha256({"damaged": document})
        payload["profile_digest"] = canonical_sha256(
            {key: value for key, value in payload.items() if key != "profile_digest"}
        )
    path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(InstallerError) as raised:
        verify_published_install(target)
    assert raised.value.code == "FRESH_INSTALL_READBACK_FAILED"
    assert not (target / "authority" / "runtime-generations").exists()
    assert not (target / "supervisor").exists()


def test_startup_readback_rejects_unknown_postpublication_schema_object(tmp_path: Path) -> None:
    target = _installed_runtime(tmp_path)
    host = ReferenceHost(target / DATABASE_NAME, programmable_backend="plain")
    host.close()
    connection = sqlite3.connect(target / DATABASE_NAME)
    try:
        connection.execute("CREATE TABLE unknown_runtime_object (value INTEGER)")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(InstallerError, match="v6 object inventory differs"):
        verify_published_install(target, allow_runtime_state=True)


@pytest.mark.parametrize("variant", ("supervisor-mode", "generation-symlink", "wal-mode"))
def test_startup_readback_rejects_unsafe_runtime_owned_state(tmp_path: Path, variant: str) -> None:
    target = _installed_runtime(tmp_path)
    if variant == "supervisor-mode":
        path = target / "supervisor"
        path.mkdir(mode=0o700)
        path.chmod(0o755)
    elif variant == "generation-symlink":
        foreign = tmp_path / "foreign-generations"
        foreign.mkdir(mode=0o700)
        (target / "authority" / "runtime-generations").symlink_to(foreign, target_is_directory=True)
    else:
        path = target / f"{DATABASE_NAME}-wal"
        path.write_bytes(b"foreign wal bytes")
        path.chmod(0o644)

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(target, allow_runtime_state=True)


def test_published_readback_rejects_runtime_tree_moved_to_another_path(tmp_path: Path) -> None:
    target = _installed_runtime(tmp_path)
    moved = tmp_path / "moved-runtime"
    target.rename(moved)

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(moved)


def test_published_readback_rejects_valid_authority_from_another_install(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = _installed_runtime(first_root)
    second = _installed_runtime(second_root)
    displaced = tmp_path / "displaced-authority"
    (first / "authority").rename(displaced)
    (second / "authority").rename(first / "authority")

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(first)


@pytest.mark.parametrize(
    "relative",
    [
        (DATABASE_NAME,),
        ("authority", "current.json"),
    ],
)
def test_published_readback_rejects_public_file_mode_drift(
    tmp_path: Path,
    relative: tuple[str, ...],
) -> None:
    target = _installed_runtime(tmp_path)
    os.chmod(target.joinpath(*relative), 0o644)

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(target)


def test_published_readback_rejects_extra_database_object(tmp_path: Path) -> None:
    target = _installed_runtime(tmp_path)
    connection = sqlite3.connect(target / DATABASE_NAME, isolation_level=None)
    try:
        connection.execute("CREATE TABLE injected_foreign_state(value TEXT NOT NULL)")
    finally:
        connection.close()

    with pytest.raises(InstallerError, match="FRESH_INSTALL_V6_INVALID"):
        verify_published_install(target)


def test_published_readback_rejects_name_replacement_during_final_fence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _installed_runtime(tmp_path)
    authority = target / "authority"
    replaced = False

    def replace_current(boundary: str) -> None:
        nonlocal replaced
        if boundary != "before_final_published_name_fence" or replaced:
            return
        replaced = True
        current = authority / "current.json"
        raw = current.read_bytes()
        current.rename(authority / "displaced-current.json")
        current.write_bytes(raw)
        current.chmod(0o600)

    monkeypatch.setattr(evidence_module, "_TEST_HOOK", replace_current)
    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(target)


def test_absent_target_refuses_all_existing_filesystem_forms(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    target.mkdir()
    with pytest.raises(InstallerError, match="FRESH_INSTALL_TARGET_EXISTS"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    target.rmdir()
    target.write_bytes(b"file")
    with pytest.raises(InstallerError, match="FRESH_INSTALL_TARGET_EXISTS"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    target.unlink()
    target.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(InstallerError, match="FRESH_INSTALL_TARGET_EXISTS"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)


@pytest.mark.parametrize(
    "variant",
    (
        "regular-file",
        "empty-directory",
        "nonempty-directory",
        "symlink",
        "broken-symlink",
        "database-file",
        "wal-shm-tree",
        "authority-history-current-tree",
        "unknown-directory-entry",
    ),
)
def test_existing_target_matrix_is_rejected_without_any_mutation(
    tmp_path: Path, variant: str
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    if variant == "regular-file":
        target.write_bytes(b"pre-existing")
    elif variant == "empty-directory":
        target.mkdir()
    elif variant == "nonempty-directory":
        target.mkdir()
        (target / "sentinel").write_bytes(b"keep")
    elif variant == "symlink":
        foreign = tmp_path / "foreign-target"
        foreign.mkdir()
        target.symlink_to(foreign, target_is_directory=True)
    elif variant == "broken-symlink":
        target.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    elif variant == "database-file":
        target.write_bytes(b"SQLite format 3\x00pre-existing")
    elif variant == "wal-shm-tree":
        target.mkdir()
        for suffix in ("", "-wal", "-shm"):
            (target / f"{DATABASE_NAME}{suffix}").write_bytes(b"foreign")
    elif variant == "authority-history-current-tree":
        (target / "authority" / "history").mkdir(parents=True)
        (target / "authority" / "current.json").write_bytes(b"foreign-current")
        (target / "authority" / "history" / HISTORY_AUTHORITY_NAME).write_bytes(b"foreign-history")
    else:
        target.mkdir()
        (target / "unknown-entry").write_bytes(b"foreign")
    before = _path_snapshot(target)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert raised.value.code == "FRESH_INSTALL_TARGET_EXISTS"
    assert _path_snapshot(target) == before
    assert _stage_names(tmp_path) == ()


@pytest.mark.parametrize(
    "variant",
    (
        "relative-final",
        "nul-final",
        "nonutf8-final",
        "missing-ancestor",
        "file-ancestor",
        "nonutf8-ancestor",
    ),
)
def test_path_preflight_rejects_unsafe_forms_without_filesystem_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    if variant == "relative-final":
        monkeypatch.chdir(tmp_path)
        unsafe_target: Any = "relative-runtime"
    elif variant == "nul-final":
        unsafe_target = str(target) + "\x00invalid"
    elif variant == "nonutf8-final":
        unsafe_target = os.fsencode(tmp_path) + b"/runtime-\xff"
    elif variant == "missing-ancestor":
        unsafe_target = tmp_path / "missing-parent" / "runtime"
    elif variant == "file-ancestor":
        file_parent = tmp_path / "file-parent"
        file_parent.write_bytes(b"not a directory")
        unsafe_target = file_parent / "runtime"
    else:
        unsafe_target = os.fsencode(tmp_path) + b"/ancestor-\xff/runtime"
    before_inputs = tuple(path.read_bytes() for path in (intent_path, receipt_path, wheel_path))
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(unsafe_target, intent_path, receipt_path, wheel_path)
    assert raised.value.code == "FRESH_INSTALL_PATH_UNSAFE"
    assert (
        tuple(path.read_bytes() for path in (intent_path, receipt_path, wheel_path))
        == before_inputs
    )
    assert _stage_names(tmp_path) == ()
    if isinstance(unsafe_target, Path):
        assert not unsafe_target.exists()


def test_safe_absolute_path_identity_has_an_independent_canonical_oracle(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, _, _, _ = _inputs(tmp_path, repo)
    identity = installer_module.preflight_target(target)
    expected_runtime_digest = canonical_sha256(
        {
            "database": f"{target.as_posix()}/{DATABASE_NAME}",
            "runtime_home": target.as_posix(),
        }
    )
    expected_database_identity = "db-" + canonical_sha256(
        {
            "schema_version": "aar.clean-install-database-identity.v1",
            "runtime_home_digest": expected_runtime_digest,
            "database_name": DATABASE_NAME,
        }
    ).removeprefix("sha256:")
    assert identity.requested_target == target
    assert identity.canonical_target == target
    assert identity.runtime_home_digest == expected_runtime_digest
    assert identity.database_identity == expected_database_identity


def test_install_issues_distinct_epochs_and_binds_each_attestation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    tokens = iter(("a" * 64, "b" * 64))
    calls: list[int] = []

    def issue_token(byte_count: int) -> str:
        calls.append(byte_count)
        return next(tokens)

    monkeypatch.setattr(fs_module.secrets, "token_hex", issue_token)
    results = []
    for name in ("first", "second"):
        root = tmp_path / name
        root.mkdir()
        target, intent_path, receipt_path, wheel_path = _inputs(root, repo)
        results.append(
            install_clean_runtime(
                target,
                intent_path,
                receipt_path,
                wheel_path,
                now_ms=1234,
            )
        )
    assert calls == [32, 32]
    assert results[0].install_epoch == "install-" + "a" * 64
    assert results[1].install_epoch == "install-" + "b" * 64
    assert results[0].install_epoch != results[1].install_epoch
    assert results[0].attestation.attestation.cutover_epoch == results[0].preparation.install_epoch
    assert results[1].attestation.attestation.cutover_epoch == results[1].preparation.install_epoch


@pytest.mark.parametrize(
    "invalid_epoch",
    (
        "install-" + "A" * 64,
        "install-" + "a" * 63,
        "install-" + "a" * 65,
        "epoch-" + "a" * 64,
        "install-" + "g" * 64,
    ),
)
def test_invalid_epoch_is_rejected_before_stage_or_target(
    tmp_path: Path, invalid_epoch: str
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            epoch_factory=lambda: invalid_epoch,
        )
    assert raised.value.code == "FRESH_INSTALL_EPOCH_INVALID"
    assert not target.exists()
    assert _stage_names(tmp_path) == ()


def test_reused_prior_epoch_stage_is_never_adopted(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    stale = tmp_path / f".runtime.install-{EPOCH.removeprefix('install-')}"
    stale.mkdir(mode=0o700)
    (stale / "prior-invocation").write_bytes(b"must-remain")
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            epoch_factory=lambda: EPOCH,
        )
    assert raised.value.code == "FRESH_INSTALL_STAGE_EXISTS"
    assert not target.exists()
    assert (stale / "prior-invocation").read_bytes() == b"must-remain"


def test_mismatched_attestation_epoch_is_rejected_before_v6_or_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    original_builder = installer_module.build_migration_attestation

    def mismatched_attestation(*args: Any, **kwargs: Any) -> MigrationAttestationDocument:
        original = original_builder(*args, **kwargs)
        payload = original.attestation.model_copy(
            update={"cutover_epoch": "install-" + "2" * 64}
        )
        return MigrationAttestationDocument.issue(payload)

    monkeypatch.setattr(installer_module, "build_migration_attestation", mismatched_attestation)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            epoch_factory=lambda: EPOCH,
        )
    assert raised.value.code == "FRESH_INSTALL_EPOCH_INVALID"
    assert not target.exists()
    assert _stage_names(tmp_path) == ()


def test_receipt_wheel_mismatch_is_rejected_before_stage(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    wheel_path.write_bytes(wheel_path.read_bytes() + b"tampered")
    with pytest.raises(InstallerError, match="FRESH_INSTALL_RECEIPT_WHEEL_MISMATCH"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert not target.exists()
    assert not list(tmp_path.glob(".runtime.install-*"))


@pytest.mark.parametrize("duplicate_level", ["root", "nested"])
def test_duplicate_receipt_keys_are_rejected_at_raw_parser_before_stage(
    tmp_path: Path,
    duplicate_level: str,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    raw = receipt_path.read_bytes()
    if duplicate_level == "root":
        raw = raw.replace(
            b'"schema_version":',
            b'"schema_version":"aar.install-candidate-receipt.v1","schema_version":',
            1,
        )
    else:
        raw = raw.replace(
            b'"candidate":{"contract_manifest_digest":',
            (
                f'"candidate":{{"contract_manifest_digest":"{DIGEST}","contract_manifest_digest":'
            ).encode(),
            1,
        )
    receipt_path.write_bytes(raw)

    assert raw.count(b'"schema_version"') > 1 or raw.count(b'"contract_manifest_digest"') > 2
    with pytest.raises(InstallerError, match="FRESH_INSTALL_INPUT_INVALID"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert not target.exists()
    assert not list(tmp_path.glob(".runtime.install-*"))


def test_traversal_wheel_is_rejected_after_exact_receipt_digest_binding(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    wheel, files = _wheel_bytes(repo)
    output = io.BytesIO(wheel)
    with zipfile.ZipFile(output, "a", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("../escape.py", b"must-not-escape\n")
    target, intent_path, receipt_path, wheel_path = _inputs_for_wheel(
        tmp_path,
        output.getvalue(),
        files,
    )

    with pytest.raises(InstallerError, match="FRESH_INSTALL_WHEEL_INVALID"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert not target.exists()
    assert not (tmp_path / "escape.py").exists()
    assert not list(tmp_path.glob(".runtime.install-*"))


def test_initial_generation_and_predecessor_are_refused(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    payload = _read_json(intent_path)
    payload["activation_generation"] = 2
    payload["previous_activation_authority_digest"] = DIGEST
    payload["intent_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "intent_digest"}
    )
    intent_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(InstallerError, match="FRESH_INSTALL_INITIAL_AUTHORITY_INVALID"):
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert not target.exists()


@pytest.mark.parametrize(
    ("generation", "previous"),
    [
        (2, None),
        (1, DIGEST),
    ],
)
def test_each_noninitial_authority_axis_is_rejected_without_staging(
    tmp_path: Path, generation: int, previous: str | None
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    payload = _read_json(intent_path)
    payload["activation_generation"] = generation
    payload["previous_activation_authority_digest"] = previous
    payload["intent_digest"] = canonical_sha256(
        {key: value for key, value in payload.items() if key != "intent_digest"}
    )
    intent_path.write_bytes(canonical_json_bytes(payload))
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert raised.value.code == "FRESH_INSTALL_INITIAL_AUTHORITY_INVALID"
    assert not target.exists()
    assert _stage_names(tmp_path) == ()


def test_sqlite_support_unavailable_fails_before_target_or_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    original_isdir = sqlite_module.os.path.isdir

    def proc_fd_unavailable(path: str | os.PathLike[str]) -> bool:
        if path == "/proc/self/fd":
            return False
        return original_isdir(path)

    monkeypatch.setattr(sqlite_module.os.path, "isdir", proc_fd_unavailable)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(target, intent_path, receipt_path, wheel_path)
    assert raised.value.code == "FRESH_INSTALL_SQLITE_UNSUPPORTED"
    assert not target.exists()
    assert _stage_names(tmp_path) == ()


def test_v5_transaction_fault_rolls_back_before_any_install_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    original_apply_v3 = OperationRegistry._apply_v3_schema

    def fail_inside_v5_transaction(registry: OperationRegistry) -> None:
        original_apply_v3(registry)
        raise RuntimeError("injected v5 construction fault")

    monkeypatch.setattr(OperationRegistry, "_apply_v3_schema", fail_inside_v5_transaction)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            epoch_factory=lambda: EPOCH,
        )
    assert raised.value.code == "FRESH_INSTALL_V5_INVALID"
    assert not target.exists()
    assert _stage_names(tmp_path) == ()


def test_v6_failure_leaves_no_target_or_stage(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    with pytest.raises(InstallerError, match="FRESH_INSTALL_V6_FAILED"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
            fail_after_statement=1,
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".runtime.install-*"))


@pytest.mark.parametrize("boundary", V6_BOUNDARIES, ids=V6_BOUNDARIES)
def test_all_named_v6_failpoints_leave_no_published_target_or_stage(
    tmp_path: Path,
    boundary: str,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    with pytest.raises(InstallerError, match="FRESH_INSTALL_V6_FAILED"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
            fail_after_statement=boundary,
        )
    assert not target.exists()
    assert not list(tmp_path.glob(".runtime.install-*"))


def test_cleanup_refuses_unknown_stage_entry_before_deleting_owned_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)

    def contaminate_stage(stage_fd: int, **_kwargs: Any) -> None:
        os.mkdir("unknown-entry", dir_fd=stage_fd)
        raise InstallerError("SIMULATED_PREPUBLICATION_FAILURE", "stop before publication")

    monkeypatch.setattr(installer_module, "_write_install_evidence", contaminate_stage)
    with pytest.raises(InstallerError, match="FRESH_INSTALL_CLEANUP_REFUSED"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
        )
    assert not target.exists()
    stages = list(tmp_path.glob(".runtime.install-*"))
    assert len(stages) == 1
    assert (stages[0] / "unknown-entry").is_dir()
    assert (stages[0] / DATABASE_NAME).is_file()


def test_publication_eexist_is_not_overwritten(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)

    def race_target() -> str:
        target.mkdir()
        (target / "sentinel").write_bytes(b"untouched")
        return EPOCH

    with pytest.raises(InstallerError, match="FRESH_INSTALL_TARGET_RACE"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=race_target,
        )
    assert (target / "sentinel").read_bytes() == b"untouched"
    assert not list(tmp_path.glob(".runtime.install-*"))


def test_post_rename_failure_never_runs_prepublication_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)

    def fail_post_publication(*_args: Any, **_kwargs: Any) -> None:
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_FAILED",
            "simulated post-rename readback failure",
        )

    monkeypatch.setattr(installer_module, "_verify_publication", fail_post_publication)
    with pytest.raises(PublicationIndeterminate, match="FRESH_INSTALL_PUBLICATION_FAILED"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
        )
    assert target.is_dir()
    assert (target / "reference.sqlite3").is_file()
    assert not list(tmp_path.glob(".runtime.install-*"))


def test_publication_binds_stage_inode_chain_and_parent_fsync_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    events: list[str] = []
    stage_identity: tuple[int, int] | None = None
    before_chain: tuple[tuple[str, int, int], ...] | None = None
    after_chain: tuple[tuple[str, int, int], ...] | None = None
    original_publish = installer_module._publish

    def capture_publish(*args: Any, **kwargs: Any) -> None:
        nonlocal stage_identity
        stage = args[0]
        observed = os.fstat(stage.descriptor)
        stage_identity = (observed.st_dev, observed.st_ino)
        return original_publish(*args, **kwargs)

    def observe(boundary: str) -> None:
        nonlocal stage_identity, before_chain, after_chain
        events.append(boundary)
        if boundary == "before_publish":
            stage = next(tmp_path.glob(".runtime.install-*"))
            observed = stage.stat()
            stage_identity = (observed.st_dev, observed.st_ino)
            before_chain = _directory_chain_snapshot(target.parent)
        elif boundary == "after_parent_fsync":
            after_chain = _directory_chain_snapshot(target.parent)

    monkeypatch.setattr(fs_module, "_TEST_HOOK", observe)
    monkeypatch.setattr(installer_module, "_publish", capture_publish)
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    published = target.stat()
    assert stage_identity == (published.st_dev, published.st_ino)
    assert before_chain == after_chain
    assert events.index("after_publish") < events.index("before_parent_fsync")
    assert events.index("before_parent_fsync") < events.index("after_parent_fsync")


@pytest.mark.parametrize("replace_ancestor", [False, True])
def test_publish_fence_rejects_parent_or_ancestor_replacement_with_contained_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_ancestor: bool
) -> None:
    repo = Path(__file__).parents[1]
    base = tmp_path / "workspace"
    ancestor = base / "ancestor"
    parent = ancestor / "parent"
    parent.mkdir(parents=True)
    target, intent_path, receipt_path, wheel_path = _inputs_for_wheel(
        base,
        *_wheel_bytes(repo),
        target=parent / "runtime",
    )
    replaced = False

    def replace_chain(boundary: str) -> None:
        nonlocal replaced
        if boundary != "before_publish_fence" or replaced:
            return
        replaced = True
        source = ancestor if replace_ancestor else parent
        displaced = base / f"displaced-{source.name}"
        source.rename(displaced)
        if replace_ancestor:
            (ancestor / "parent").mkdir(parents=True)
        else:
            parent.mkdir()

    monkeypatch.setattr(fs_module, "_TEST_HOOK", replace_chain)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
        )
    assert raised.value.code == "FRESH_INSTALL_PARENT_REPLACED"
    assert not target.exists()
    displaced = base / f"displaced-{'ancestor' if replace_ancestor else 'parent'}"
    assert tuple(path.name for path in displaced.rglob(".runtime.install-*"))


def test_staged_verification_refuses_substituted_stage_before_publish_or_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    original_verify = installer_module._verify_staged_evidence
    replaced = False

    def substitute_stage(*args: Any, **kwargs: Any) -> None:
        nonlocal replaced
        if not replaced:
            replaced = True
            stage_path = next(tmp_path.glob(".runtime.install-*"))
            stage_path.rename(tmp_path / "displaced-stage")
            stage_path.mkdir(mode=0o700)
            (stage_path / "foreign").write_bytes(b"must-remain")
        return original_verify(*args, **kwargs)

    monkeypatch.setattr(installer_module, "_verify_staged_evidence", substitute_stage)
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
        )
    assert raised.value.code == "FRESH_INSTALL_STAGE_REPLACED"
    assert not target.exists()
    assert (tmp_path / "displaced-stage").is_dir()
    assert (next(tmp_path.glob(".runtime.install-*")) / "foreign").read_bytes() == b"must-remain"


def test_caught_failure_removes_owned_stage_and_rerun_ignores_abrupt_residue(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    stale_epoch = "install-" + "3" * 64
    stale = tmp_path / f".runtime.install-{stale_epoch.removeprefix('install-')}"
    stale.mkdir(mode=0o700)
    (stale / "abrupt-residue").write_bytes(b"must-remain")
    with pytest.raises(InstallerError) as raised:
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=1234,
            epoch_factory=lambda: EPOCH,
            fail_after_statement=1,
        )
    assert raised.value.code == "FRESH_INSTALL_V6_FAILED"
    assert not target.exists()
    assert (stale / "abrupt-residue").read_bytes() == b"must-remain"
    fresh_epoch = "install-" + "4" * 64
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: fresh_epoch,
    )
    assert target.is_dir()
    assert (stale / "abrupt-residue").read_bytes() == b"must-remain"
    assert _stage_names(tmp_path) == (stale.name,)


def test_temporary_backup_is_removed_before_publish_fsync_and_startup_readback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    events: list[str] = []
    original_remove_backup = installer_module.SQLiteStageAdapter.remove_backup
    original_publish = installer_module._publish
    original_startup = installer_module.verify_published_install

    def remove_backup(adapter: SQLiteStageAdapter) -> None:
        events.append("before_backup_remove")
        original_remove_backup(adapter)
        events.append("after_backup_remove")

    def publish(*args: Any, **kwargs: Any) -> None:
        events.append("publish")
        return original_publish(*args, **kwargs)

    def startup(*args: Any, **kwargs: Any) -> Any:
        events.append("startup_readback")
        return original_startup(*args, **kwargs)

    def fs_boundary(boundary: str) -> None:
        if boundary == "after_parent_fsync":
            events.append(boundary)

    monkeypatch.setattr(installer_module.SQLiteStageAdapter, "remove_backup", remove_backup)
    monkeypatch.setattr(installer_module, "_publish", publish)
    monkeypatch.setattr(installer_module, "verify_published_install", startup)
    monkeypatch.setattr(fs_module, "_TEST_HOOK", fs_boundary)
    install_clean_runtime(
        target,
        intent_path,
        receipt_path,
        wheel_path,
        now_ms=1234,
        epoch_factory=lambda: EPOCH,
    )
    assert events.index("after_backup_remove") < events.index("publish")
    assert events.index("publish") < events.index("after_parent_fsync")
    assert events.index("after_parent_fsync") < events.index("startup_readback")
    assert not (target / BACKUP_NAME).exists()


def test_real_cli_post_rename_failure_emits_exact_indeterminate_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)

    def fail_post_publication(*_args: Any, **_kwargs: Any) -> None:
        raise PublicationIndeterminate(
            "FRESH_INSTALL_PUBLICATION_FAILED",
            "simulated post-rename readback failure",
        )

    monkeypatch.setattr(installer_module, "_verify_publication", fail_post_publication)
    exit_code = admin.main(
        [
            "runtime",
            "install",
            "--runtime-home",
            str(target),
            "--intent",
            str(intent_path),
            "--candidate-receipt",
            str(receipt_path),
            "--wheel",
            str(wheel_path),
        ]
    )
    captured = capsys.readouterr()
    document = json.loads(captured.out)
    assert exit_code == 3
    assert captured.err == ""
    assert document["target"] == str(target)
    assert document["install_epoch"].startswith("install-")
    assert document["publication_state"] == "rename_linearized"
    assert document["target_may_exist"] is True
    assert document["automatic_retry_safe"] is False
    assert target.is_dir()


def test_parent_symlink_is_rejected_before_mutation(tmp_path: Path) -> None:
    repo = Path(__file__).parents[1]
    _target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    real_parent = tmp_path / "real"
    real_parent.mkdir()
    link_parent = tmp_path / "link"
    link_parent.symlink_to(real_parent, target_is_directory=True)
    linked_target = link_parent / "runtime"
    with pytest.raises(InstallerError, match="FRESH_INSTALL_PATH_UNSAFE"):
        install_clean_runtime(linked_target, intent_path, receipt_path, wheel_path)
    assert not (real_parent / "runtime").exists()


def test_sqlite_adapter_rejects_allowed_name_with_wrong_file_type(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    (stage / DATABASE_NAME).mkdir()
    descriptor = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        adapter = SQLiteStageAdapter(descriptor, now_ms=lambda: 1234)
        with pytest.raises(InstallerError, match="FRESH_INSTALL_SQLITE_RESIDUE"):
            adapter.construct_empty_v5()
    finally:
        os.close(descriptor)


def test_sqlite_adapter_rejects_database_inode_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    descriptor = os.open(stage, os.O_RDONLY | os.O_DIRECTORY)
    try:
        adapter = SQLiteStageAdapter(descriptor, now_ms=lambda: 1234)
        adapter.construct_empty_v5()
        create_backup = installer_module.create_sqlite_backup

        def replace_database(database_path: Path, snapshot_path: Path) -> Any:
            snapshot = create_backup(database_path, snapshot_path)
            replacement = database_path.with_name("replacement.sqlite3")
            replacement.write_bytes(database_path.read_bytes())
            os.replace(replacement, database_path)
            return snapshot

        monkeypatch.setattr(installer_module, "create_sqlite_backup", replace_database)
        with pytest.raises(InstallerError, match="FRESH_INSTALL_SQLITE_REPLACED"):
            adapter.create_backup()
    finally:
        os.close(descriptor)


def test_v6_sidecar_replacement_is_preserved_and_never_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).parents[1]
    target, intent_path, receipt_path, wheel_path = _inputs(tmp_path, repo)
    original = installer_module.SQLiteStageAdapter.prepare_v6_sidecars

    def replace_owned_sidecar(
        adapter: SQLiteStageAdapter,
        expected_identities: dict[str, Any],
    ) -> None:
        stage = Path(f"/proc/self/fd/{adapter.stage_fd}")
        replacement = stage / "replacement-wal"
        replacement.write_bytes(b"foreign-sidecar")
        os.replace(replacement, stage / f"{DATABASE_NAME}-wal")
        original(adapter, expected_identities)

    monkeypatch.setattr(
        installer_module.SQLiteStageAdapter,
        "prepare_v6_sidecars",
        replace_owned_sidecar,
    )
    with pytest.raises(InstallerError, match="FRESH_INSTALL_CLEANUP_REFUSED"):
        install_clean_runtime(
            target,
            intent_path,
            receipt_path,
            wheel_path,
            now_ms=lambda: 1234,
            epoch_factory=lambda: EPOCH,
        )
    assert not target.exists()
    stages = list(tmp_path.glob(".runtime.install-*"))
    assert len(stages) == 1
    assert (stages[0] / f"{DATABASE_NAME}-wal").read_bytes() == b"foreign-sidecar"
