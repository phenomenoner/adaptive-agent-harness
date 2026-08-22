"""Strict, inert operator-authority document models.

The models in this module describe operator-authored cutover, restore, and
activation-authority documents.  They validate only transport-neutral bytes and
local relationships; they do not inspect or mutate any external state.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self, cast

from pydantic import Field, StringConstraints, model_validator

from aar.canonical import canonical_sha256
from aar.provider_ready_models import (
    LocalAuthorityStoreId,
    ProviderReadyCandidate,
)
from aar.schemas import BudgetCounter, Digest, OpaqueToken, PositiveCounter, StrictModel

ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION = "aar.activation-generation-authority.v1"
CUTOVER_PLAN_SCHEMA_VERSION = "aar.cutover-plan.v1"
OPERATOR_PREPARED_MARKER_SCHEMA_VERSION = "aar.operator-prepared-marker.v1"
CUTOVER_RECEIPT_SCHEMA_VERSION = "aar.cutover-receipt.v1"
RESTORE_RECEIPT_SCHEMA_VERSION = "aar.restore-receipt.v1"
OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION = "aar.operator-terminal-marker.v1"

_MAX_COUNTER = 9_223_372_036_854_775_807
UnixMs = Annotated[int, Field(ge=0, le=_MAX_COUNTER, strict=True)]
OperatorPath = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=4096,
        pattern=r"^[^\x00]*$",
        strict=True,
    ),
]

RecoveryAction = Literal["abort", "apply", "reconcile", "status"]
PreparedKind = Literal["cutover", "restore"]
CutoverOutcome = Literal["committed", "aborted_before_db_commit", "recovery_required"]
DatabaseCommitState = Literal["not_committed", "committed", "unknown"]
RestoreOutcome = Literal["restored_pre_frontier", "recovery_required"]
ReplacementCommitState = Literal["not_committed", "committed", "unknown"]
SidecarDisposition = Literal["unchanged", "absent", "removed_after_verified_replace"]
TerminalKind = Literal[
    "cutover_committed",
    "cutover_aborted",
    "cutover_recovery_required",
    "restore_committed",
    "restore_recovery_required",
]

_PLACEHOLDER_DIGEST = "sha256:" + "0" * 64


def _compute_self_digest(document: StrictModel, digest_field: str) -> str:
    """Hash a complete validated document after removing only its root digest."""

    payload = document.model_dump(mode="json")
    try:
        del payload[digest_field]
    except KeyError as error:
        raise ValueError(f"self-digest field {digest_field!r} is missing") from error
    return canonical_sha256(payload)


def _issue_document(
    model_type: type[StrictModel], payload: dict[str, object], digest_field: str
) -> StrictModel:
    """Build one self-digested document without introducing a digest cycle."""

    provisional = model_type.model_construct(
        **payload,
        **{digest_field: _PLACEHOLDER_DIGEST},
        _fields_set=set(payload) | {digest_field},
    )
    digest = _compute_self_digest(provisional, digest_field)
    return model_type(**payload, **{digest_field: digest})


def _require_sorted_unique(values: Iterable[str], field_name: str) -> None:
    materialized = list(values)
    if materialized != sorted(materialized) or len(materialized) != len(set(materialized)):
        raise ValueError(f"{field_name} must be sorted and unique")


def _require_digest(document: StrictModel, digest_field: str) -> None:
    observed = getattr(document, digest_field)
    expected = _compute_self_digest(document, digest_field)
    if observed != expected:
        raise ValueError(f"{digest_field} does not match canonical document bytes")


class FileArtifact(StrictModel):
    """A verified operator-visible file identity, not a path or file handle."""

    artifact_id: OpaqueToken
    digest: Digest
    size_bytes: BudgetCounter


class SidecarObservation(StrictModel):
    state: Literal["absent", "present"]
    file_identity: OpaqueToken | None
    size_bytes: UnixMs | None
    digest: Digest | None

    @model_validator(mode="after")
    def state_matches_nullable_observations(self) -> Self:
        values = (self.file_identity, self.size_bytes, self.digest)
        if self.state == "absent" and any(value is not None for value in values):
            raise ValueError("absent sidecar observations require all nullable values to be null")
        if self.state == "present" and any(value is None for value in values):
            raise ValueError("present sidecar observations require all nullable values")
        return self


class DatabaseChecks(StrictModel):
    integrity_result: Literal["ok"]
    foreign_key_violation_count: Literal[0]


class CandidateBinding(ProviderReadyCandidate):
    """A1a candidate projection with byte-equivalent fields and validation."""


class EpochOwnerBinding(StrictModel):
    authority_store_id: LocalAuthorityStoreId
    operator_identity_digest: Digest
    runtime_owner_state: Literal["absent"]
    exclusive_lock_state: Literal["available"]


class NonterminalCounts(StrictModel):
    operations: BudgetCounter
    attempts: BudgetCounter
    workbench_jobs: BudgetCounter
    caller_tickets: BudgetCounter
    workers: BudgetCounter

    @model_validator(mode="after")
    def all_counts_are_zero(self) -> Self:
        if any(
            value != 0
            for value in (
                self.operations,
                self.attempts,
                self.workbench_jobs,
                self.caller_tickets,
                self.workers,
            )
        ):
            raise ValueError("all nonterminal counts must be zero")
        return self


class SnapshotPlan(StrictModel):
    snapshot_id: OpaqueToken
    destination: OperatorPath
    backup_mode: Literal["sqlite_backup"]


class ActivationGenerationAuthority(StrictModel):
    schema_version: Literal["aar.activation-generation-authority.v1"]
    authority_store_id: LocalAuthorityStoreId
    profile_id: OpaqueToken
    activation_generation: PositiveCounter
    intent_digest: Digest
    profile_digest: Digest
    migration_attestation_digest: Digest
    previous_activation_authority_digest: Digest | None
    authority_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.activation-generation-authority.v1"],
        authority_store_id: LocalAuthorityStoreId,
        profile_id: OpaqueToken,
        activation_generation: PositiveCounter,
        intent_digest: Digest,
        profile_digest: Digest,
        migration_attestation_digest: Digest,
        previous_activation_authority_digest: Digest | None,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "authority_store_id": authority_store_id,
            "profile_id": profile_id,
            "activation_generation": activation_generation,
            "intent_digest": intent_digest,
            "profile_digest": profile_digest,
            "migration_attestation_digest": migration_attestation_digest,
            "previous_activation_authority_digest": previous_activation_authority_digest,
        }
        return cast(Self, _issue_document(cls, payload, "authority_digest"))

    @model_validator(mode="after")
    def authority_digest_is_content_bound(self) -> Self:
        _require_digest(self, "authority_digest")
        return self


class CutoverPlan(StrictModel):
    schema_version: Literal["aar.cutover-plan.v1"]
    cutover_epoch: OpaqueToken
    runtime_home_digest: Digest
    database_identity: OpaqueToken
    database_file: FileArtifact
    owner: EpochOwnerBinding
    source_registry_version: Literal[5]
    source_registry_schema_digest: Digest
    canonical_v5_row_set_digest: Digest
    wal: SidecarObservation
    shm: SidecarObservation
    database_checks: DatabaseChecks
    nonterminal_counts: NonterminalCounts
    snapshot: SnapshotPlan
    candidate: CandidateBinding
    migration_sql_digest: Digest
    activation_intent_digest: Digest
    final_profile_output: OperatorPath
    profile_id: OpaqueToken
    proposed_activation_generation: PositiveCounter
    previous_activation_authority_digest: Digest | None
    authority_store_id: LocalAuthorityStoreId
    allowed_recovery_actions: Annotated[
        tuple[RecoveryAction, ...], Field(min_length=1, max_length=4)
    ]
    created_at_unix_ms: UnixMs
    expires_at_unix_ms: UnixMs
    plan_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.cutover-plan.v1"],
        cutover_epoch: OpaqueToken,
        runtime_home_digest: Digest,
        database_identity: OpaqueToken,
        database_file: FileArtifact,
        owner: EpochOwnerBinding,
        source_registry_version: Literal[5],
        source_registry_schema_digest: Digest,
        canonical_v5_row_set_digest: Digest,
        wal: SidecarObservation,
        shm: SidecarObservation,
        database_checks: DatabaseChecks,
        nonterminal_counts: NonterminalCounts,
        snapshot: SnapshotPlan,
        candidate: CandidateBinding,
        migration_sql_digest: Digest,
        activation_intent_digest: Digest,
        final_profile_output: OperatorPath,
        profile_id: OpaqueToken,
        proposed_activation_generation: PositiveCounter,
        previous_activation_authority_digest: Digest | None,
        authority_store_id: LocalAuthorityStoreId,
        allowed_recovery_actions: tuple[RecoveryAction, ...],
        created_at_unix_ms: UnixMs,
        expires_at_unix_ms: UnixMs,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "cutover_epoch": cutover_epoch,
            "runtime_home_digest": runtime_home_digest,
            "database_identity": database_identity,
            "database_file": database_file,
            "owner": owner,
            "source_registry_version": source_registry_version,
            "source_registry_schema_digest": source_registry_schema_digest,
            "canonical_v5_row_set_digest": canonical_v5_row_set_digest,
            "wal": wal,
            "shm": shm,
            "database_checks": database_checks,
            "nonterminal_counts": nonterminal_counts,
            "snapshot": snapshot,
            "candidate": candidate,
            "migration_sql_digest": migration_sql_digest,
            "activation_intent_digest": activation_intent_digest,
            "final_profile_output": final_profile_output,
            "profile_id": profile_id,
            "proposed_activation_generation": proposed_activation_generation,
            "previous_activation_authority_digest": previous_activation_authority_digest,
            "authority_store_id": authority_store_id,
            "allowed_recovery_actions": tuple(sorted(allowed_recovery_actions)),
            "created_at_unix_ms": created_at_unix_ms,
            "expires_at_unix_ms": expires_at_unix_ms,
        }
        return cast(Self, _issue_document(cls, payload, "plan_digest"))

    @model_validator(mode="after")
    def plan_fields_are_coherent(self) -> Self:
        if self.authority_store_id != self.owner.authority_store_id:
            raise ValueError("plan authority_store_id must equal owner authority_store_id")
        if self.created_at_unix_ms >= self.expires_at_unix_ms:
            raise ValueError("plan creation time must precede expiry time")
        _require_sorted_unique(self.allowed_recovery_actions, "allowed_recovery_actions")
        return self

    @model_validator(mode="after")
    def plan_digest_is_content_bound(self) -> Self:
        _require_digest(self, "plan_digest")
        return self


class CutoverPreparation(StrictModel):
    plan: CutoverPlan
    plan_digest: Digest
    snapshot: FileArtifact
    pre_migration_database_digest: Digest
    wal: SidecarObservation
    shm: SidecarObservation

    @model_validator(mode="after")
    def preparation_matches_plan(self) -> Self:
        if self.plan_digest != self.plan.plan_digest:
            raise ValueError("cutover preparation plan_digest must match plan")
        if self.snapshot.artifact_id != self.plan.snapshot.snapshot_id:
            raise ValueError("snapshot artifact_id must match plan snapshot_id")
        if self.pre_migration_database_digest != self.plan.database_file.digest:
            raise ValueError("pre-migration database digest must match plan database digest")
        if self.wal != self.plan.wal or self.shm != self.plan.shm:
            raise ValueError("prepared sidecar observations must match plan")
        return self


class RestoreFrontierProof(StrictModel):
    no_committed_v6_attestation: Literal[True]
    no_activation_history_record: Literal[True]
    no_v6_runtime_or_workbench_write: Literal[True]


class RestorePreparation(StrictModel):
    cutover_plan_digest: Digest
    cutover_prepared_marker_digest: Digest
    cutover_terminal_marker_digest: Digest
    snapshot: FileArtifact
    runtime_home_digest: Digest
    target_database_identity: OpaqueToken
    pre_restore_database: FileArtifact
    pre_restore_wal: SidecarObservation
    pre_restore_shm: SidecarObservation
    no_committed_v6_attestation: Literal[True]
    no_activation_history_record: Literal[True]
    no_v6_runtime_or_workbench_write: Literal[True]


class OperatorPreparedMarker(StrictModel):
    schema_version: Literal["aar.operator-prepared-marker.v1"]
    kind: PreparedKind
    epoch: OpaqueToken
    operator_identity_digest: Digest
    prepared_at_unix_ms: UnixMs
    cutover: CutoverPreparation | None
    restore: RestorePreparation | None
    prepared_marker_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.operator-prepared-marker.v1"],
        kind: PreparedKind,
        epoch: OpaqueToken,
        operator_identity_digest: Digest,
        prepared_at_unix_ms: UnixMs,
        cutover: CutoverPreparation | None,
        restore: RestorePreparation | None,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "kind": kind,
            "epoch": epoch,
            "operator_identity_digest": operator_identity_digest,
            "prepared_at_unix_ms": prepared_at_unix_ms,
            "cutover": cutover,
            "restore": restore,
        }
        return cast(Self, _issue_document(cls, payload, "prepared_marker_digest"))

    @model_validator(mode="after")
    def prepared_union_and_epoch_are_coherent(self) -> Self:
        if self.kind == "cutover":
            if self.cutover is None or self.restore is not None:
                raise ValueError("cutover prepared marker requires cutover only")
            if self.epoch != self.cutover.plan.cutover_epoch:
                raise ValueError("prepared marker epoch must match cutover plan epoch")
            if not (
                self.cutover.plan.created_at_unix_ms
                <= self.prepared_at_unix_ms
                < self.cutover.plan.expires_at_unix_ms
            ):
                raise ValueError("prepared marker time must be within plan validity window")
        else:
            if self.cutover is not None or self.restore is None:
                raise ValueError("restore prepared marker requires restore only")
        return self

    @model_validator(mode="after")
    def prepared_marker_digest_is_content_bound(self) -> Self:
        _require_digest(self, "prepared_marker_digest")
        return self


class CutoverReceipt(StrictModel):
    schema_version: Literal["aar.cutover-receipt.v1"]
    cutover_epoch: OpaqueToken
    plan_digest: Digest
    outcome: CutoverOutcome
    database_commit_state: DatabaseCommitState
    prepared_marker_digest: Digest
    snapshot: FileArtifact
    pre_migration_database_digest: Digest
    canonical_v5_row_set_digest: Digest
    migration_sql_digest: Digest
    migration_attestation_digest: Digest | None
    profile_id: OpaqueToken
    activation_generation: PositiveCounter
    previous_activation_authority_digest: Digest | None
    activation_authority_digest: Digest | None
    post_migration_database_digest: Digest | None
    database_checks: DatabaseChecks | None
    candidate: CandidateBinding
    intent_digest: Digest
    generated_profile_digest: Digest | None
    generated_profile_output: OperatorPath
    started_at_unix_ms: UnixMs
    db_committed_at_unix_ms: UnixMs | None
    completed_at_unix_ms: UnixMs
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.cutover-receipt.v1"],
        cutover_epoch: OpaqueToken,
        plan_digest: Digest,
        outcome: CutoverOutcome,
        database_commit_state: DatabaseCommitState,
        prepared_marker_digest: Digest,
        snapshot: FileArtifact,
        pre_migration_database_digest: Digest,
        canonical_v5_row_set_digest: Digest,
        migration_sql_digest: Digest,
        migration_attestation_digest: Digest | None,
        profile_id: OpaqueToken,
        activation_generation: PositiveCounter,
        previous_activation_authority_digest: Digest | None,
        activation_authority_digest: Digest | None,
        post_migration_database_digest: Digest | None,
        database_checks: DatabaseChecks | None,
        candidate: CandidateBinding,
        intent_digest: Digest,
        generated_profile_digest: Digest | None,
        generated_profile_output: OperatorPath,
        started_at_unix_ms: UnixMs,
        db_committed_at_unix_ms: UnixMs | None,
        completed_at_unix_ms: UnixMs,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "cutover_epoch": cutover_epoch,
            "plan_digest": plan_digest,
            "outcome": outcome,
            "database_commit_state": database_commit_state,
            "prepared_marker_digest": prepared_marker_digest,
            "snapshot": snapshot,
            "pre_migration_database_digest": pre_migration_database_digest,
            "canonical_v5_row_set_digest": canonical_v5_row_set_digest,
            "migration_sql_digest": migration_sql_digest,
            "migration_attestation_digest": migration_attestation_digest,
            "profile_id": profile_id,
            "activation_generation": activation_generation,
            "previous_activation_authority_digest": previous_activation_authority_digest,
            "activation_authority_digest": activation_authority_digest,
            "post_migration_database_digest": post_migration_database_digest,
            "database_checks": database_checks,
            "candidate": candidate,
            "intent_digest": intent_digest,
            "generated_profile_digest": generated_profile_digest,
            "generated_profile_output": generated_profile_output,
            "started_at_unix_ms": started_at_unix_ms,
            "db_committed_at_unix_ms": db_committed_at_unix_ms,
            "completed_at_unix_ms": completed_at_unix_ms,
        }
        return cast(Self, _issue_document(cls, payload, "receipt_digest"))

    @model_validator(mode="after")
    def outcome_matrix_is_coherent(self) -> Self:
        if self.started_at_unix_ms > self.completed_at_unix_ms:
            raise ValueError("receipt completion time cannot precede start time")
        if self.db_committed_at_unix_ms is not None and not (
            self.started_at_unix_ms
            <= self.db_committed_at_unix_ms
            <= self.completed_at_unix_ms
        ):
            raise ValueError("database commit time must be ordered within receipt lifetime")

        post_commit_values = (
            self.migration_attestation_digest,
            self.activation_authority_digest,
            self.post_migration_database_digest,
            self.database_checks,
            self.generated_profile_digest,
        )
        if self.outcome == "committed":
            if self.database_commit_state != "committed":
                raise ValueError("committed receipt requires database_commit_state=committed")
            if any(value is None for value in post_commit_values):
                raise ValueError("committed receipt requires all post-commit observations")
            if self.db_committed_at_unix_ms is None:
                raise ValueError("committed receipt requires db_committed_at_unix_ms")
        elif self.outcome == "aborted_before_db_commit":
            if self.database_commit_state != "not_committed":
                raise ValueError(
                    "aborted_before_db_commit requires database_commit_state=not_committed"
                )
            if any(value is not None for value in post_commit_values):
                raise ValueError("aborted receipt cannot contain post-commit observations")
            if self.db_committed_at_unix_ms is not None:
                raise ValueError("aborted receipt cannot contain a committed timestamp")
        elif self.database_commit_state == "not_committed" and any(
            value is not None for value in post_commit_values
        ):
            raise ValueError(
                "not_committed recovery receipt cannot contain post-commit observations"
            )
        elif (
            self.database_commit_state == "not_committed"
            and self.db_committed_at_unix_ms is not None
        ):
            raise ValueError("not_committed recovery receipt cannot contain a committed timestamp")
        elif self.database_commit_state == "committed":
            if any(value is None for value in post_commit_values):
                raise ValueError("committed database state requires observed post-commit fields")
            if self.db_committed_at_unix_ms is None:
                raise ValueError("committed database state requires a committed timestamp")
        elif self.database_commit_state == "unknown" and self.db_committed_at_unix_ms is not None:
            raise ValueError("unknown database state cannot contain a committed timestamp")
        return self

    @model_validator(mode="after")
    def receipt_digest_is_content_bound(self) -> Self:
        _require_digest(self, "receipt_digest")
        return self


class RestoreReceipt(StrictModel):
    schema_version: Literal["aar.restore-receipt.v1"]
    restore_epoch: OpaqueToken
    cutover_plan_digest: Digest
    outcome: RestoreOutcome
    replacement_commit_state: ReplacementCommitState
    snapshot: FileArtifact
    runtime_home_digest: Digest
    target_database_identity: OpaqueToken
    pre_restore_database: FileArtifact
    pre_restore_wal: SidecarObservation
    pre_restore_shm: SidecarObservation
    restored_database_digest: Digest | None
    sidecar_disposition: SidecarDisposition
    database_checks: DatabaseChecks | None
    cutover_prepared_marker_digest: Digest
    cutover_terminal_marker_digest: Digest
    restore_prepared_marker_digest: Digest
    activation_authority_digest_before_restore: Digest | None
    activation_authority_digest_after_restore: Digest | None
    activation_authority_disposition: Literal["unchanged"]
    frontier_proof: RestoreFrontierProof
    started_at_unix_ms: UnixMs
    replacement_committed_at_unix_ms: UnixMs | None
    completed_at_unix_ms: UnixMs
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.restore-receipt.v1"],
        restore_epoch: OpaqueToken,
        cutover_plan_digest: Digest,
        outcome: RestoreOutcome,
        replacement_commit_state: ReplacementCommitState,
        snapshot: FileArtifact,
        runtime_home_digest: Digest,
        target_database_identity: OpaqueToken,
        pre_restore_database: FileArtifact,
        pre_restore_wal: SidecarObservation,
        pre_restore_shm: SidecarObservation,
        restored_database_digest: Digest | None,
        sidecar_disposition: SidecarDisposition,
        database_checks: DatabaseChecks | None,
        cutover_prepared_marker_digest: Digest,
        cutover_terminal_marker_digest: Digest,
        restore_prepared_marker_digest: Digest,
        activation_authority_digest_before_restore: Digest | None,
        activation_authority_digest_after_restore: Digest | None,
        activation_authority_disposition: Literal["unchanged"],
        frontier_proof: RestoreFrontierProof,
        started_at_unix_ms: UnixMs,
        replacement_committed_at_unix_ms: UnixMs | None,
        completed_at_unix_ms: UnixMs,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "restore_epoch": restore_epoch,
            "cutover_plan_digest": cutover_plan_digest,
            "outcome": outcome,
            "replacement_commit_state": replacement_commit_state,
            "snapshot": snapshot,
            "runtime_home_digest": runtime_home_digest,
            "target_database_identity": target_database_identity,
            "pre_restore_database": pre_restore_database,
            "pre_restore_wal": pre_restore_wal,
            "pre_restore_shm": pre_restore_shm,
            "restored_database_digest": restored_database_digest,
            "sidecar_disposition": sidecar_disposition,
            "database_checks": database_checks,
            "cutover_prepared_marker_digest": cutover_prepared_marker_digest,
            "cutover_terminal_marker_digest": cutover_terminal_marker_digest,
            "restore_prepared_marker_digest": restore_prepared_marker_digest,
            "activation_authority_digest_before_restore": (
                activation_authority_digest_before_restore
            ),
            "activation_authority_digest_after_restore": activation_authority_digest_after_restore,
            "activation_authority_disposition": activation_authority_disposition,
            "frontier_proof": frontier_proof,
            "started_at_unix_ms": started_at_unix_ms,
            "replacement_committed_at_unix_ms": replacement_committed_at_unix_ms,
            "completed_at_unix_ms": completed_at_unix_ms,
        }
        return cast(Self, _issue_document(cls, payload, "receipt_digest"))

    @model_validator(mode="after")
    def restore_outcome_matrix_is_coherent(self) -> Self:
        if self.started_at_unix_ms > self.completed_at_unix_ms:
            raise ValueError("restore completion time cannot precede start time")
        if self.replacement_committed_at_unix_ms is not None and not (
            self.started_at_unix_ms
            <= self.replacement_committed_at_unix_ms
            <= self.completed_at_unix_ms
        ):
            raise ValueError("replacement commit time must be ordered within restore lifetime")

        if self.outcome == "restored_pre_frontier":
            if self.replacement_commit_state != "committed":
                raise ValueError(
                    "restored_pre_frontier requires replacement_commit_state=committed"
                )
            if (
                self.restored_database_digest is None
                or self.database_checks is None
                or self.replacement_committed_at_unix_ms is None
            ):
                raise ValueError("restored receipt requires replacement observations")
            if (
                self.activation_authority_digest_before_restore
                != self.activation_authority_digest_after_restore
            ):
                raise ValueError("restored receipt requires equal activation authority digests")
        elif self.replacement_commit_state == "not_committed":
            if (
                self.restored_database_digest is not None
                or self.database_checks is not None
                or self.replacement_committed_at_unix_ms is not None
            ):
                raise ValueError("not_committed restore cannot contain replacement observations")
        elif self.replacement_commit_state == "unknown" and (
            self.replacement_committed_at_unix_ms is not None
        ):
            raise ValueError("unknown replacement state cannot contain a committed timestamp")
        elif self.replacement_commit_state == "committed":
            if (
                self.restored_database_digest is None
                or self.database_checks is None
                or self.replacement_committed_at_unix_ms is None
            ):
                raise ValueError("committed replacement state requires replacement observations")

        before = self.activation_authority_digest_before_restore
        after = self.activation_authority_digest_after_restore
        if before is not None and after is not None and before != after:
            raise ValueError("restore cannot claim an activation-authority rollback")
        return self

    @model_validator(mode="after")
    def receipt_digest_is_content_bound(self) -> Self:
        _require_digest(self, "receipt_digest")
        return self


class OperatorTerminalMarker(StrictModel):
    schema_version: Literal["aar.operator-terminal-marker.v1"]
    kind: TerminalKind
    epoch: OpaqueToken
    receipt_schema_version: Literal[
        "aar.cutover-receipt.v1", "aar.restore-receipt.v1"
    ]
    receipt: Annotated[CutoverReceipt | RestoreReceipt, Field(discriminator="schema_version")]
    published_at_unix_ms: UnixMs
    marker_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal["aar.operator-terminal-marker.v1"],
        kind: TerminalKind,
        epoch: OpaqueToken,
        receipt_schema_version: Literal["aar.cutover-receipt.v1", "aar.restore-receipt.v1"],
        receipt: CutoverReceipt | RestoreReceipt,
        published_at_unix_ms: UnixMs,
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "kind": kind,
            "epoch": epoch,
            "receipt_schema_version": receipt_schema_version,
            "receipt": receipt,
            "published_at_unix_ms": published_at_unix_ms,
        }
        return cast(Self, _issue_document(cls, payload, "marker_digest"))

    @model_validator(mode="after")
    def terminal_mapping_is_exhaustive(self) -> Self:
        if self.receipt_schema_version == CUTOVER_RECEIPT_SCHEMA_VERSION:
            if not isinstance(self.receipt, CutoverReceipt):
                raise ValueError("cutover receipt_schema_version requires a cutover receipt")
            if self.epoch != self.receipt.cutover_epoch:
                raise ValueError("terminal epoch must match cutover receipt epoch")
            expected_kind = cast(
                TerminalKind,
                {
                    "committed": "cutover_committed",
                    "aborted_before_db_commit": "cutover_aborted",
                    "recovery_required": "cutover_recovery_required",
                }[self.receipt.outcome],
            )
        else:
            if not isinstance(self.receipt, RestoreReceipt):
                raise ValueError("restore receipt_schema_version requires a restore receipt")
            if self.epoch != self.receipt.restore_epoch:
                raise ValueError("terminal epoch must match restore receipt epoch")
            expected_kind = cast(
                TerminalKind,
                (
                    "restore_committed"
                    if self.receipt.outcome == "restored_pre_frontier"
                    else "restore_recovery_required"
                ),
            )
        if self.kind != expected_kind:
            raise ValueError("terminal kind does not match receipt outcome")
        if self.published_at_unix_ms < self.receipt.completed_at_unix_ms:
            raise ValueError("terminal publication cannot precede receipt completion")
        return self

    @model_validator(mode="after")
    def marker_digest_is_content_bound(self) -> Self:
        _require_digest(self, "marker_digest")
        return self


OPERATOR_AUTHORITY_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION: ActivationGenerationAuthority,
    CUTOVER_PLAN_SCHEMA_VERSION: CutoverPlan,
    OPERATOR_PREPARED_MARKER_SCHEMA_VERSION: OperatorPreparedMarker,
    CUTOVER_RECEIPT_SCHEMA_VERSION: CutoverReceipt,
    RESTORE_RECEIPT_SCHEMA_VERSION: RestoreReceipt,
    OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION: OperatorTerminalMarker,
}
PROVIDER_READY_OPERATOR_SCHEMA_MODELS = OPERATOR_AUTHORITY_SCHEMA_MODELS

__all__ = [
    "ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION",
    "CUTOVER_PLAN_SCHEMA_VERSION",
    "CUTOVER_RECEIPT_SCHEMA_VERSION",
    "OPERATOR_AUTHORITY_SCHEMA_MODELS",
    "OPERATOR_PREPARED_MARKER_SCHEMA_VERSION",
    "OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION",
    "PROVIDER_READY_OPERATOR_SCHEMA_MODELS",
    "RESTORE_RECEIPT_SCHEMA_VERSION",
    "ActivationGenerationAuthority",
    "CandidateBinding",
    "CutoverPlan",
    "CutoverPreparation",
    "CutoverReceipt",
    "DatabaseChecks",
    "EpochOwnerBinding",
    "FileArtifact",
    "NonterminalCounts",
    "OperatorPath",
    "OperatorPreparedMarker",
    "OperatorTerminalMarker",
    "RestoreFrontierProof",
    "RestorePreparation",
    "RestoreReceipt",
    "SidecarObservation",
    "SnapshotPlan",
    "UnixMs",
]
