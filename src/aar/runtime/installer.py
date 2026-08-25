"""Public façade for the standalone clean-install preparation core."""

from __future__ import annotations

import contextlib
import os
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aar.provider_ready_install_models import (
    CleanInstallPreparation,
    MigrationAttestationDocument,
)
from aar.provider_ready_models import (
    HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
    HostActivationProfile,
)
from aar.provider_ready_operator_models import (
    ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
    ActivationGenerationAuthority,
)
from aar.runtime._install_artifacts import (
    OpenedInput,
    WheelInspection,
    _digest_bytes,
    _open_input,
    _validate_intent,
    _validate_receipt,
    inspect_wheel_bytes,
    validate_receipt_against_intent,
)
from aar.runtime._install_evidence import (
    PublishedInstallReadback,
    _verify_staged_evidence,
    _write_install_evidence,
    build_clean_install_preparation,
    build_migration_attestation,
    project_authority_store_id,
    project_snapshot_id,
    verify_install_epoch_binding,
    verify_published_install,
)
from aar.runtime._install_fs import (
    BACKUP_NAME,
    CURRENT_AUTHORITY_PATH,
    DATABASE_NAME,
    HISTORY_AUTHORITY_DIR,
    HISTORY_AUTHORITY_NAME,
    CleanInstallError,
    FileIdentity,
    InstallerError,
    PublicationIndeterminate,
    PublicationState,
    RetainedDirectoryChain,
    StageHandle,
    TargetIdentity,
    _create_stage,
    _fsync_child,
    _fsync_fd,
    _issue_epoch,
    _preflight_target_with_chain,
    _publish,
    _require_publication_support,
    _verify_target_binding,
    cleanup_stage,
    compute_database_identity,
    compute_runtime_home_digest,
    preflight_target,
    reconcile_removed_stage_entries,
    record_stage_ownership,
    validate_stage_ownership,
)
from aar.runtime._install_fs import (
    _verify_publication as _fs_verify_publication,
)
from aar.runtime._install_sqlite import (
    SQLiteStageAdapter as _SQLiteStageAdapter,
)
from aar.runtime._install_sqlite import (
    _migration_sql_bytes,
    _require_linux_sqlite_support,
)
from aar.runtime.migrations import (
    SQLiteBackupSnapshot,
    apply_registry_v6,
    create_sqlite_backup,
)


@dataclass(frozen=True, slots=True)
class InstallResult:
    target: Path
    runtime_home_digest: str
    database_identity: str
    install_epoch: str
    snapshot_id: str
    attestation_digest: str
    profile_digest: str
    authority_digest: str
    preparation: CleanInstallPreparation
    attestation: MigrationAttestationDocument
    profile: HostActivationProfile
    authority: ActivationGenerationAuthority

def _clock(value: int | Callable[[], int] | None) -> Callable[[], int]:
    if value is None:
        return lambda: time.time_ns() // 1_000_000
    if isinstance(value, int) and not isinstance(value, bool):
        return lambda: value
    if callable(value):
        return value
    raise TypeError("now_ms must be an integer or a callable returning an integer")

class SQLiteStageAdapter(_SQLiteStageAdapter):
    """Compatibility façade preserving installer-level migration seams."""

    def create_backup(
        self,
        *,
        backup_factory: Callable[[Path, Path], SQLiteBackupSnapshot] | None = None,
    ) -> SQLiteBackupSnapshot:
        return super().create_backup(backup_factory=backup_factory or create_sqlite_backup)

    def apply_v6(
        self,
        attestation: MigrationAttestationDocument,
        *,
        fail_after_statement: int | str | None = None,
        migration_factory: Callable[..., Any] | None = None,
    ) -> None:
        return super().apply_v6(
            attestation,
            fail_after_statement=fail_after_statement,
            migration_factory=migration_factory or apply_registry_v6,
        )


def _verify_publication(
    stage: StageHandle,
    chain: RetainedDirectoryChain,
    target: TargetIdentity,
) -> None:
    """Keep the façade monkeypatch seam while delegating ownership to _install_fs."""

    return _fs_verify_publication(stage, chain, target)

def install_clean_runtime(
    runtime_home: os.PathLike[str] | str,
    intent_path: os.PathLike[str] | str,
    candidate_receipt_path: os.PathLike[str] | str,
    wheel_path: os.PathLike[str] | str,
    *,
    now_ms: int | Callable[[], int] | None = None,
    epoch_factory: Callable[[], str] | None = None,
    fail_after_statement: int | str | None = None,
) -> InstallResult:
    """Install one exact candidate into one absent target, or publish nothing."""

    _require_linux_sqlite_support()
    _require_publication_support()
    target, chain = _preflight_target_with_chain(runtime_home)
    clock = _clock(now_ms)
    intent_input: OpenedInput | None = None
    receipt_input: OpenedInput | None = None
    wheel_input: OpenedInput | None = None
    stage: StageHandle | None = None
    publication = PublicationState()
    install_epoch: str | None = None
    try:
        intent_input = _open_input(intent_path, label="intent")
        intent = _validate_intent(intent_input.data)
        _verify_target_binding(intent, target)
        receipt_input = _open_input(candidate_receipt_path, label="candidate receipt")
        wheel_input = _open_input(wheel_path, label="wheel")
        receipt = _validate_receipt(receipt_input.data)
        wheel = inspect_wheel_bytes(wheel_input.data, receipt)
        validate_receipt_against_intent(receipt, intent, wheel)
        install_epoch = _issue_epoch(epoch_factory)
        stage = _create_stage(chain, target.final_name, install_epoch)
        adapter = SQLiteStageAdapter(
            stage.descriptor,
            stage.identity,
            now_ms=clock,
            migration_sql_bytes=_migration_sql_bytes(),
        )
        validate_stage_ownership(stage)
        try:
            row_digest = adapter.construct_empty_v5()
        except InstallerError:
            # The stage was empty before v5 construction.  Reconcile only the
            # adapter's known SQLite names so a contained v5 rollback can
            # remove its own partial database without adopting unknown residue.
            try:
                adapter._check_entries(backup=False)
            except InstallerError:
                pass
            else:
                record_stage_ownership(stage)
            raise
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        backup = adapter.create_backup()
        record_stage_ownership(stage)
        snapshot_id = project_snapshot_id(backup.sha256, backup.size_bytes, install_epoch)
        migration_sql_digest = _digest_bytes(adapter.migration_sql_bytes)
        preparation = build_clean_install_preparation(
            install_epoch=install_epoch,
            runtime_home_digest=target.runtime_home_digest,
            database_identity=target.database_identity,
            empty_v5_backup_digest=backup.sha256,
            empty_v5_backup_size_bytes=backup.size_bytes,
            canonical_v5_row_set_digest=row_digest,
            intent=intent,
            migration_sql_digest=migration_sql_digest,
        )
        started = clock()
        attestation = build_migration_attestation(
            preparation=preparation,
            snapshot_id=snapshot_id,
            intent=intent,
            migration_sql_digest=migration_sql_digest,
            started_at_unix_ms=started,
            completed_at_unix_ms=max(started, clock()),
        )
        verify_install_epoch_binding(preparation, attestation)
        validate_stage_ownership(stage)
        adapter.prepare_v6_sidecars(
            {
                name: stage.owned_entries[(name,)]
                for name in (
                    f"{DATABASE_NAME}-wal",
                    f"{DATABASE_NAME}-shm",
                )
                if (name,) in stage.owned_entries
            }
        )
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        try:
            adapter.apply_v6(attestation, fail_after_statement=fail_after_statement)
        except InstallerError:
            reconcile_removed_stage_entries(stage)
            raise
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        adapter.verify_v6(attestation)
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        adapter.quiesce_database()
        record_stage_ownership(stage)
        profile = HostActivationProfile.issue(
            schema_version=HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
            intent=intent,
            migration_attestation_digest=attestation.attestation_digest,
        )
        authority = ActivationGenerationAuthority.issue(
            schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
            authority_store_id=intent.cutover_authority_store_id,
            profile_id=intent.profile_id,
            activation_generation=1,
            intent_digest=intent.intent_digest,
            profile_digest=profile.profile_digest,
            migration_attestation_digest=attestation.attestation_digest,
            previous_activation_authority_digest=None,
        )
        validate_stage_ownership(stage)
        _write_install_evidence(
            stage.descriptor,
            receipt_bytes=receipt_input.data,
            preparation=preparation,
            attestation=attestation,
            profile=profile,
            authority=authority,
        )
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        adapter.remove_backup()
        record_stage_ownership(stage)
        validate_stage_ownership(stage)
        _verify_staged_evidence(
            stage,
            receipt=receipt,
            receipt_bytes=receipt_input.data,
            preparation=preparation,
            attestation=attestation,
            profile=profile,
            authority=authority,
        )
        validate_stage_ownership(stage)
        _fsync_child(stage.descriptor, DATABASE_NAME)
        _fsync_fd(stage.descriptor)
        chain.verify()
        _publish(stage, chain, target, publication)
        _verify_publication(stage, chain, target)
        published = verify_published_install(target.canonical_target)
        if (
            published.target != target.canonical_target
            or published.runtime_home_digest != target.runtime_home_digest
            or published.database_identity != target.database_identity
            or published.receipt != receipt
            or published.preparation != preparation
            or published.attestation != attestation
            or published.profile != profile
            or published.authority != authority
            or published.database_versions != (1, 2, 3, 4, 5, 6)
        ):
            raise InstallerError(
                "FRESH_INSTALL_READBACK_FAILED",
                "published install readback differs from the prepared candidate",
            )
        return InstallResult(
            target=target.canonical_target,
            runtime_home_digest=target.runtime_home_digest,
            database_identity=target.database_identity,
            install_epoch=install_epoch,
            snapshot_id=snapshot_id,
            attestation_digest=attestation.attestation_digest,
            profile_digest=profile.profile_digest,
            authority_digest=authority.authority_digest,
            preparation=preparation,
            attestation=attestation,
            profile=profile,
            authority=authority,
        )
    except InstallerError as error:
        if publication.renamed:
            if install_epoch is None:
                raise AssertionError("published installation has no install epoch") from error
            raise PublicationIndeterminate(
                error.code,
                error.message,
                target=target.canonical_target,
                install_epoch=install_epoch,
            ) from error
        if stage is not None and chain is not None and not publication.renamed:
            try:
                cleanup_stage(stage, chain)
            except InstallerError as cleanup_error:
                raise cleanup_error
        raise
    except (OSError, sqlite3.Error, ValidationError, ValueError) as error:
        if publication.renamed:
            if install_epoch is None:
                raise AssertionError("published installation has no install epoch") from error
            raise PublicationIndeterminate(
                "FRESH_INSTALL_PUBLICATION_FAILED",
                "post-publication durability or readback is indeterminate",
                target=target.canonical_target,
                install_epoch=install_epoch,
            ) from error
        if stage is not None and chain is not None and not publication.renamed:
            try:
                cleanup_stage(stage, chain)
            except InstallerError as cleanup_error:
                raise cleanup_error from error
        raise InstallerError("FRESH_INSTALL_FAILED", "clean installation failed") from error
    finally:
        if stage is not None:
            with contextlib.suppress(OSError):
                os.close(stage.descriptor)
        if chain is not None:
            chain.close()
        for opened in (wheel_input, receipt_input, intent_input):
            if opened is not None:
                with contextlib.suppress(OSError):
                    opened.close()

install_runtime = install_clean_runtime
resolve_target_identity = preflight_target

__all__ = [
    "BACKUP_NAME",
    "CURRENT_AUTHORITY_PATH",
    "DATABASE_NAME",
    "HISTORY_AUTHORITY_DIR",
    "HISTORY_AUTHORITY_NAME",
    "CleanInstallError",
    "FileIdentity",
    "InstallResult",
    "InstallerError",
    "MigrationAttestationDocument",
    "PublicationIndeterminate",
    "PublishedInstallReadback",
    "RetainedDirectoryChain",
    "SQLiteStageAdapter",
    "TargetIdentity",
    "WheelInspection",
    "build_clean_install_preparation",
    "build_migration_attestation",
    "cleanup_stage",
    "compute_database_identity",
    "compute_runtime_home_digest",
    "inspect_wheel_bytes",
    "install_clean_runtime",
    "install_runtime",
    "preflight_target",
    "project_authority_store_id",
    "project_snapshot_id",
    "resolve_target_identity",
    "validate_receipt_against_intent",
    "verify_published_install",
]
