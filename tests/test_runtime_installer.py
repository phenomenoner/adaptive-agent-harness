from __future__ import annotations

import hashlib
import io
import json
import os
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

import pytest

from aar import admin
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_install_models import (
    FactoryEntry,
    InstallCandidateReceipt,
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
from aar.provider_ready_package_factory import (
    PACKAGE_FACTORY_DECLARATIONS,
    PACKAGE_FACTORY_WHEEL_MEMBER,
    factory_entries_from_member_digests,
)
from aar.provider_ready_runtime_models import (
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    WorkbenchGrantSet,
)
from aar.runtime import _install_evidence as evidence_module
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
from aar.runtime.provider_ready_activation import ProviderReadyActivationStore
from aar.runtime.registry import OperationRegistry

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
        package_version="0.6.0a0",
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
) -> tuple[Path, Path, Path, Path]:
    target = tmp_path / "runtime"
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

    with pytest.raises(InstallerError, match="FRESH_INSTALL_READBACK_FAILED"):
        verify_published_install(target)

    startup = verify_published_install(target, allow_runtime_state=True)
    assert startup.profile == initial.profile
    assert startup.authority == initial.authority
    assert startup.database_versions == (1, 2, 3, 4, 5, 6)


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
