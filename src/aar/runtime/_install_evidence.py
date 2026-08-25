"""Preparation, attestation, and immutable installed-tree evidence."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.provider_ready_install_models import (
    CLEAN_INSTALL_PREPARATION_SCHEMA_VERSION,
    CleanInstallPreparation,
    InstallCandidateReceipt,
    MigrationAttestationDocument,
    MigrationAttestationPayload,
    candidate_projection,
)
from aar.provider_ready_models import (
    HostActivationIntent,
    HostActivationProfile,
)
from aar.provider_ready_operator_models import (
    ActivationGenerationAuthority,
)
from aar.runtime._install_artifacts import verify_installed_distribution_members
from aar.runtime._install_fs import (
    DATABASE_NAME,
    HISTORY_AUTHORITY_NAME,
    FileIdentity,
    InstallerError,
    RetainedDirectoryChain,
    StageHandle,
    _fsync_fd,
    _mkdir_at,
    _path_text,
    _read_at,
    _write_at,
    compute_database_identity,
    compute_runtime_home_digest,
)
from aar.runtime._install_sqlite import verify_v6_readback


@dataclass(frozen=True, slots=True)
class PublishedInstallReadback:
    target: Path
    runtime_home_digest: str
    database_identity: str
    profile: HostActivationProfile
    authority: ActivationGenerationAuthority
    receipt: InstallCandidateReceipt
    preparation: CleanInstallPreparation
    attestation: MigrationAttestationDocument
    database_versions: tuple[int, ...]


_MAX_PUBLISHED_DOCUMENT_BYTES = 4 * 1024 * 1024
_TEST_HOOK: Callable[[str], None] | None = None


def _test_hook(boundary: str) -> None:
    hook = _TEST_HOOK
    if hook is not None:
        hook(boundary)


def verify_install_epoch_binding(
    preparation: CleanInstallPreparation,
    attestation: MigrationAttestationDocument,
) -> None:
    """Reject an attestation that does not belong to this install invocation."""

    if attestation.attestation.cutover_epoch != preparation.install_epoch:
        raise InstallerError(
            "FRESH_INSTALL_EPOCH_INVALID",
            "attestation cutover_epoch differs from preparation install_epoch",
        )


def _require_owned_directory(descriptor: int, *, label: str) -> FileIdentity:
    observed = os.fstat(descriptor)
    identity = FileIdentity.from_stat(observed)
    if (
        not stat.S_ISDIR(observed.st_mode)
        or stat.S_IMODE(observed.st_mode) != 0o700
        or observed.st_uid != os.getuid()
        or observed.st_gid != os.getgid()
    ):
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED",
            f"{label} is not an invocation-owned mode-0700 directory",
        )
    return identity


def _open_owned_directory(parent_fd: int, name: str, *, label: str) -> tuple[int, FileIdentity]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED", f"{label} cannot be opened safely"
        ) from error
    try:
        return descriptor, _require_owned_directory(descriptor, label=label)
    except BaseException:
        os.close(descriptor)
        raise


def _open_owned_runtime_file(parent_fd: int, name: str, *, label: str) -> tuple[int, FileIdentity]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED", f"{label} cannot be opened safely"
        ) from error
    try:
        observed = os.fstat(descriptor)
        identity = FileIdentity.from_stat(observed)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_uid != os.getuid()
            or observed.st_gid != os.getgid()
        ):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED",
                f"{label} is not an invocation-owned mode-0600 file",
            )
        return descriptor, identity
    except BaseException:
        os.close(descriptor)
        raise


def _read_owned_authority_file(
    parent_fd: int,
    name: str,
) -> tuple[bytes, FileIdentity]:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED", f"authority file cannot be opened: {name}"
        ) from error
    try:
        observed = os.fstat(descriptor)
        identity = FileIdentity.from_stat(observed)
        if (
            not stat.S_ISREG(observed.st_mode)
            or stat.S_IMODE(observed.st_mode) != 0o600
            or observed.st_uid != os.getuid()
            or observed.st_gid != os.getgid()
            or observed.st_size > _MAX_PUBLISHED_DOCUMENT_BYTES
        ):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED",
                f"authority file has unsafe type, mode, owner, or size: {name}",
            )
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        named = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        if not identity.exact(named):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED", f"authority file changed during readback: {name}"
            )
        return b"".join(chunks), identity
    finally:
        os.close(descriptor)


def _verify_install_bindings(
    *,
    target: Path,
    runtime_home_digest: str,
    database_identity: str,
    receipt: InstallCandidateReceipt,
    preparation: CleanInstallPreparation,
    attestation: MigrationAttestationDocument,
    profile: HostActivationProfile,
    authority: ActivationGenerationAuthority,
) -> None:
    intent = profile.intent
    attested = attestation.attestation
    expected_candidate = candidate_projection(intent.candidate)
    bindings_hold = (
        preparation.runtime_home_digest == runtime_home_digest
        and preparation.database_identity == database_identity
        and preparation.database_name == DATABASE_NAME
        and receipt.candidate == intent.candidate
        and preparation.intent_digest == intent.intent_digest
        and preparation.candidate == expected_candidate
        and preparation.candidate == candidate_projection(receipt.candidate)
        and attested.cutover_epoch == preparation.install_epoch
        and attested.snapshot_sha256 == preparation.empty_v5_backup_digest
        and attested.snapshot_size_bytes == preparation.empty_v5_backup_size_bytes
        and attested.canonical_v5_row_set_digest == preparation.canonical_v5_row_set_digest
        and attested.source_commit == preparation.candidate.source_commit
        and attested.wheel_digest == preparation.candidate.wheel_digest
        and attested.skill_digest == preparation.candidate.skill_digest
        and attested.contract_manifest_digest == preparation.candidate.contract_manifest_digest
        and attested.migration_sql_digest == preparation.migration_sql_digest
        and attested.profile_digest == intent.intent_digest
        and attested.external_authority_store_id
        == preparation.projected_external_authority_store_id
        and attested.external_authority_prepared_digest
        == preparation.external_authority_prepared_digest
        and profile.migration_attestation_digest == attestation.attestation_digest
        and authority.authority_store_id == intent.cutover_authority_store_id
        and authority.profile_id == intent.profile_id
        and authority.activation_generation == 1
        and authority.intent_digest == intent.intent_digest
        and authority.profile_digest == profile.profile_digest
        and authority.migration_attestation_digest == attestation.attestation_digest
        and authority.previous_activation_authority_digest is None
    )
    if not bindings_hold:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED",
            f"published install authority is not cross-bound to {target}",
        )


def _verify_named_identity(
    parent_fd: int,
    name: str,
    identity: FileIdentity,
    *,
    label: str,
) -> None:
    try:
        observed = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError as error:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED", f"{label} disappeared during readback"
        ) from error
    if not identity.exact(observed):
        raise InstallerError("FRESH_INSTALL_READBACK_FAILED", f"{label} changed during readback")


def project_authority_store_id(intent_authority_store_id: str) -> str:
    """Project the colon-bearing local authority id into the v6 token domain."""

    material = {
        "schema_version": "aar.clean-install-authority-projection.v1",
        "intent_authority_store_id": intent_authority_store_id,
    }
    return "authority-" + canonical_sha256(material).removeprefix("sha256:")


def project_snapshot_id(
    empty_v5_backup_digest: str,
    empty_v5_backup_size_bytes: int,
    install_epoch: str,
) -> str:
    """Project verified backup identity into the strict v6 snapshot token domain."""

    material = {
        "schema_version": "aar.clean-install-snapshot-id.v1",
        "empty_v5_backup_digest": empty_v5_backup_digest,
        "empty_v5_backup_size_bytes": empty_v5_backup_size_bytes,
        "install_epoch": install_epoch,
    }
    return "empty-v5-" + canonical_sha256(material).removeprefix("sha256:")


def build_clean_install_preparation(
    *,
    install_epoch: str,
    runtime_home_digest: str,
    database_identity: str,
    empty_v5_backup_digest: str,
    empty_v5_backup_size_bytes: int,
    canonical_v5_row_set_digest: str,
    intent: HostActivationIntent,
    migration_sql_digest: str,
) -> CleanInstallPreparation:
    """Construct the one path-free preparation object."""

    return CleanInstallPreparation(
        schema_version=CLEAN_INSTALL_PREPARATION_SCHEMA_VERSION,
        install_epoch=install_epoch,
        runtime_home_digest=runtime_home_digest,
        database_identity=database_identity,
        database_name=DATABASE_NAME,
        empty_v5_backup_digest=empty_v5_backup_digest,
        empty_v5_backup_size_bytes=empty_v5_backup_size_bytes,
        canonical_v5_row_set_digest=canonical_v5_row_set_digest,
        intent_digest=intent.intent_digest,
        candidate=candidate_projection(intent.candidate),
        migration_sql_digest=migration_sql_digest,
        projected_external_authority_store_id=project_authority_store_id(
            intent.cutover_authority_store_id
        ),
    )


def build_migration_attestation(
    *,
    preparation: CleanInstallPreparation,
    snapshot_id: str,
    intent: HostActivationIntent,
    migration_sql_digest: str,
    started_at_unix_ms: int,
    completed_at_unix_ms: int,
) -> MigrationAttestationDocument:
    """Build the complete frozen v6 attestation without a profile digest cycle."""

    payload = MigrationAttestationPayload(
        schema_version="aar.migration-v6-attestation-payload.v1",
        migration_version=6,
        cutover_epoch=preparation.install_epoch,
        snapshot_id=snapshot_id,
        snapshot_sha256=preparation.empty_v5_backup_digest,
        snapshot_size_bytes=preparation.empty_v5_backup_size_bytes,
        canonical_v5_row_set_digest=preparation.canonical_v5_row_set_digest,
        source_commit=intent.candidate.source_commit,
        wheel_digest=intent.candidate.wheel_digest,
        profile_digest=intent.intent_digest,
        skill_digest=intent.candidate.skill_digest,
        contract_manifest_digest=intent.candidate.contract_manifest_digest,
        migration_sql_digest=migration_sql_digest,
        external_authority_store_id=preparation.projected_external_authority_store_id,
        external_authority_prepared_digest=preparation.external_authority_prepared_digest,
        started_at_unix_ms=started_at_unix_ms,
        completed_at_unix_ms=completed_at_unix_ms,
        foreign_key_violation_count=0,
        integrity_result="ok",
    )
    return MigrationAttestationDocument.issue(payload)


def _write_install_evidence(
    stage_fd: int,
    *,
    receipt_bytes: bytes,
    preparation: CleanInstallPreparation,
    attestation: MigrationAttestationDocument,
    profile: HostActivationProfile,
    authority: ActivationGenerationAuthority,
) -> None:
    authority_fd = _mkdir_at(stage_fd, "authority")
    history_fd = -1
    try:
        history_fd = _mkdir_at(authority_fd, "history")
        _write_at(authority_fd, "install-candidate-receipt.json", receipt_bytes)
        _write_at(
            authority_fd,
            "install-preparation.json",
            canonical_json_bytes(preparation.model_dump(mode="json")),
        )
        _write_at(
            authority_fd,
            "migration-attestation.json",
            canonical_json_bytes(attestation.model_dump(mode="json")),
        )
        _write_at(
            authority_fd, "profile.json", canonical_json_bytes(profile.model_dump(mode="json"))
        )
        authority_bytes = canonical_json_bytes(authority.model_dump(mode="json"))
        _write_at(authority_fd, "current.json", authority_bytes)
        _write_at(history_fd, HISTORY_AUTHORITY_NAME, authority_bytes)
        _fsync_fd(history_fd)
        _fsync_fd(authority_fd)
    finally:
        if history_fd >= 0:
            os.close(history_fd)
        os.close(authority_fd)


def _verify_staged_evidence(
    stage: StageHandle,
    *,
    receipt: InstallCandidateReceipt,
    receipt_bytes: bytes,
    preparation: CleanInstallPreparation,
    attestation: MigrationAttestationDocument,
    profile: HostActivationProfile,
    authority: ActivationGenerationAuthority,
) -> None:
    stage.verify()
    names = set(os.listdir(stage.descriptor))
    if names != {DATABASE_NAME, "authority"}:
        raise InstallerError("FRESH_INSTALL_PROFILE_INVALID", "staged root inventory is not exact")
    authority_stat = os.stat("authority", dir_fd=stage.descriptor, follow_symlinks=False)
    if not stat.S_ISDIR(authority_stat.st_mode) or stat.S_IMODE(authority_stat.st_mode) != 0o700:
        raise InstallerError(
            "FRESH_INSTALL_PROFILE_INVALID", "authority directory is not mode 0700"
        )
    authority_fd = os.open(
        "authority",
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        dir_fd=stage.descriptor,
    )
    history_fd = -1
    try:
        expected_authority = {
            "install-candidate-receipt.json",
            "install-preparation.json",
            "migration-attestation.json",
            "profile.json",
            "current.json",
            "history",
        }
        if set(os.listdir(authority_fd)) != expected_authority:
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "authority inventory is not exact"
            )
        history_fd = os.open(
            "history",
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=authority_fd,
        )
        if set(os.listdir(history_fd)) != {HISTORY_AUTHORITY_NAME}:
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "authority history inventory is not exact"
            )
        receipt_readback = _read_at(authority_fd, "install-candidate-receipt.json")
        if receipt_readback != receipt_bytes:
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "receipt bytes changed in staging"
            )
        if InstallCandidateReceipt.model_validate_json(receipt_readback, strict=True) != receipt:
            raise InstallerError("FRESH_INSTALL_PROFILE_INVALID", "receipt readback differs")
        prep_readback = CleanInstallPreparation.model_validate_json(
            _read_at(authority_fd, "install-preparation.json"), strict=True
        )
        attestation_readback = MigrationAttestationDocument.model_validate_json(
            _read_at(authority_fd, "migration-attestation.json"), strict=True
        )
        profile_readback = HostActivationProfile.model_validate_json(
            _read_at(authority_fd, "profile.json"), strict=True
        )
        authority_readback = ActivationGenerationAuthority.model_validate_json(
            _read_at(authority_fd, "current.json"), strict=True
        )
        history_bytes = _read_at(history_fd, HISTORY_AUTHORITY_NAME)
        if history_bytes != _read_at(authority_fd, "current.json"):
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "current and history authority differ"
            )
        if prep_readback != preparation or attestation_readback != attestation:
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "preparation or attestation readback differs"
            )
        if profile_readback != profile or authority_readback != authority:
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "profile or authority readback differs"
            )
        if (
            authority_readback.activation_generation != 1
            or authority_readback.previous_activation_authority_digest is not None
        ):
            raise InstallerError(
                "FRESH_INSTALL_PROFILE_INVALID", "history is not initial authority"
            )
    except (ValidationError, ValueError) as error:
        raise InstallerError(
            "FRESH_INSTALL_PROFILE_INVALID", "staged evidence model is invalid"
        ) from error
    finally:
        if history_fd >= 0:
            os.close(history_fd)
        os.close(authority_fd)


def verify_published_install(
    runtime_home: os.PathLike[str] | str,
    *,
    allow_runtime_state: bool = False,
    verify_distribution_members: bool = False,
) -> PublishedInstallReadback:
    """Read back immutable install evidence without initializing or repairing it.

    ``allow_runtime_state`` is the narrow C2 startup view: immutable install
    evidence remains exact while the already-published runtime may contain its
    generation-owned authority subtree and populated v6 domain rows.
    ``verify_distribution_members`` rehashes receipt-bound installed members;
    it deliberately does not reconstruct or claim the deleted overall wheel.
    """

    target_text = _path_text(runtime_home, label="runtime_home")
    requested_target = Path(target_text)
    if (
        not requested_target.is_absolute()
        or target_text.endswith(os.sep)
        or requested_target.name in {"", ".", ".."}
    ):
        raise InstallerError("FRESH_INSTALL_PATH_UNSAFE", "runtime_home must be absolute")

    chain = RetainedDirectoryChain(requested_target.parent)
    root_fd = -1
    authority_fd = -1
    history_fd = -1
    runtime_directory_fds: dict[str, int] = {}
    runtime_directory_identities: dict[str, FileIdentity] = {}
    runtime_file_fds: dict[str, int] = {}
    runtime_file_identities: dict[str, FileIdentity] = {}
    try:
        canonical_parent = requested_target.parent.resolve(strict=True)
        chain.verify()
        target = canonical_parent / requested_target.name
        runtime_home_digest = compute_runtime_home_digest(target)
        database_identity = compute_database_identity(runtime_home_digest)
        root_fd, root_identity = _open_owned_directory(
            chain.parent.descriptor,
            requested_target.name,
            label="published target",
        )
        root_names = set(os.listdir(root_fd))
        required_root_names = {DATABASE_NAME, "authority"}
        allowed_root_names = set(required_root_names)
        if allow_runtime_state:
            allowed_root_names.update(
                {f"{DATABASE_NAME}-wal", f"{DATABASE_NAME}-shm", "supervisor"}
            )
        if not required_root_names.issubset(root_names) or not root_names.issubset(
            allowed_root_names
        ):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED", "published root inventory is not exact"
            )
        if allow_runtime_state and "supervisor" in root_names:
            descriptor, identity = _open_owned_directory(
                root_fd, "supervisor", label="published supervisor state"
            )
            runtime_directory_fds["supervisor"] = descriptor
            runtime_directory_identities["supervisor"] = identity
        if allow_runtime_state:
            for name in (f"{DATABASE_NAME}-wal", f"{DATABASE_NAME}-shm"):
                if name not in root_names:
                    continue
                descriptor, identity = _open_owned_runtime_file(
                    root_fd, name, label=f"published runtime file {name}"
                )
                runtime_file_fds[name] = descriptor
                runtime_file_identities[name] = identity
        authority_fd, authority_identity = _open_owned_directory(
            root_fd,
            "authority",
            label="published authority",
        )
        required_authority_names = {
            "install-candidate-receipt.json",
            "install-preparation.json",
            "migration-attestation.json",
            "profile.json",
            "current.json",
            "history",
        }
        allowed_authority_names = set(required_authority_names)
        if allow_runtime_state:
            allowed_authority_names.add("runtime-generations")
        authority_names = set(os.listdir(authority_fd))
        if not required_authority_names.issubset(authority_names) or not authority_names.issubset(
            allowed_authority_names
        ):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED", "published authority inventory is not exact"
            )
        if allow_runtime_state and "runtime-generations" in authority_names:
            descriptor, identity = _open_owned_directory(
                authority_fd,
                "runtime-generations",
                label="published runtime generations",
            )
            runtime_directory_fds["authority/runtime-generations"] = descriptor
            runtime_directory_identities["authority/runtime-generations"] = identity
        history_fd, history_identity = _open_owned_directory(
            authority_fd,
            "history",
            label="published authority history",
        )
        if set(os.listdir(history_fd)) != {HISTORY_AUTHORITY_NAME}:
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED", "published history inventory is not exact"
            )

        authority_file_names = (
            "install-candidate-receipt.json",
            "install-preparation.json",
            "migration-attestation.json",
            "profile.json",
            "current.json",
        )
        authority_files = {
            name: _read_owned_authority_file(authority_fd, name) for name in authority_file_names
        }
        history_bytes, history_file_identity = _read_owned_authority_file(
            history_fd, HISTORY_AUTHORITY_NAME
        )
        receipt = InstallCandidateReceipt.model_validate_json(
            authority_files["install-candidate-receipt.json"][0], strict=True
        )
        preparation = CleanInstallPreparation.model_validate_json(
            authority_files["install-preparation.json"][0], strict=True
        )
        attestation = MigrationAttestationDocument.model_validate_json(
            authority_files["migration-attestation.json"][0], strict=True
        )
        profile = HostActivationProfile.model_validate_json(
            authority_files["profile.json"][0], strict=True
        )
        authority_bytes = authority_files["current.json"][0]
        authority = ActivationGenerationAuthority.model_validate_json(authority_bytes, strict=True)
        if authority_bytes != history_bytes:
            raise InstallerError("FRESH_INSTALL_READBACK_FAILED", "current/history bytes differ")
        _verify_install_bindings(
            target=target,
            runtime_home_digest=runtime_home_digest,
            database_identity=database_identity,
            receipt=receipt,
            preparation=preparation,
            attestation=attestation,
            profile=profile,
            authority=authority,
        )
        if verify_distribution_members:
            verify_installed_distribution_members(receipt)

        database_fd = os.open(
            DATABASE_NAME,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=root_fd,
        )
        try:
            database_observed = os.fstat(database_fd)
            database_file_identity = FileIdentity.from_stat(database_observed)
            if (
                not stat.S_ISREG(database_observed.st_mode)
                or stat.S_IMODE(database_observed.st_mode) != 0o600
                or database_observed.st_uid != os.getuid()
                or database_observed.st_gid != os.getgid()
            ):
                raise InstallerError(
                    "FRESH_INSTALL_READBACK_FAILED",
                    "published database has unsafe type or owner",
                )
            connection = sqlite3.connect(
                f"file:/proc/self/fd/{database_fd}?mode=ro&immutable=1",
                uri=True,
                isolation_level=None,
            )
            try:
                connection.execute("PRAGMA query_only=ON")
                versions = verify_v6_readback(
                    connection,
                    attestation,
                    require_empty_domain=not allow_runtime_state,
                )
            finally:
                connection.close()
        finally:
            os.close(database_fd)

        _test_hook("before_final_published_name_fence")
        for name, (_raw, identity) in authority_files.items():
            _verify_named_identity(authority_fd, name, identity, label=f"authority/{name}")
        _verify_named_identity(
            history_fd,
            HISTORY_AUTHORITY_NAME,
            history_file_identity,
            label=f"authority/history/{HISTORY_AUTHORITY_NAME}",
        )
        _verify_named_identity(authority_fd, "history", history_identity, label="authority/history")
        for name, identity in runtime_file_identities.items():
            _verify_named_identity(root_fd, name, identity, label=name)
        if "supervisor" in runtime_directory_identities:
            _verify_named_identity(
                root_fd,
                "supervisor",
                runtime_directory_identities["supervisor"],
                label="supervisor",
            )
        if "authority/runtime-generations" in runtime_directory_identities:
            _verify_named_identity(
                authority_fd,
                "runtime-generations",
                runtime_directory_identities["authority/runtime-generations"],
                label="authority/runtime-generations",
            )
        _verify_named_identity(root_fd, "authority", authority_identity, label="authority")
        _verify_named_identity(root_fd, DATABASE_NAME, database_file_identity, label=DATABASE_NAME)
        _verify_named_identity(
            chain.parent.descriptor,
            requested_target.name,
            root_identity,
            label="published target",
        )
        chain.verify()
        return PublishedInstallReadback(
            target,
            runtime_home_digest,
            database_identity,
            profile,
            authority,
            receipt,
            preparation,
            attestation,
            versions,
        )
    except (ValidationError, ValueError) as error:
        raise InstallerError(
            "FRESH_INSTALL_READBACK_FAILED", "published evidence is invalid"
        ) from error
    finally:
        for descriptor in runtime_file_fds.values():
            os.close(descriptor)
        for descriptor in runtime_directory_fds.values():
            os.close(descriptor)
        if history_fd >= 0:
            os.close(history_fd)
        if authority_fd >= 0:
            os.close(authority_fd)
        if root_fd >= 0:
            os.close(root_fd)
        chain.close()
