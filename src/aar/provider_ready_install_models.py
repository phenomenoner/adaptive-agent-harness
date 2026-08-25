"""Strict models used by the standalone clean-install boundary.

The install receipt is deliberately separate from the older caller-work receipt
and from the transition-shaped operator documents.  It binds only credential-free
candidate and wheel evidence; filesystem and SQLite authority lives in
:mod:`aar.runtime.installer`.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, Self

from pydantic import Field, StringConstraints, model_validator

from aar.canonical import canonical_sha256
from aar.provider_ready_models import ProviderReadyCandidate, SourceCommit
from aar.schemas import CapabilityName, Digest, StrictModel

_INSTALL_EPOCH = Annotated[
    str,
    StringConstraints(pattern=r"^install-[0-9a-f]{64}$", strict=True),
]
_INSTALL_SNAPSHOT = Annotated[
    str,
    StringConstraints(pattern=r"^empty-v5-[0-9a-f]{64}$", strict=True),
]
_POSIX_MEMBER = Annotated[str, StringConstraints(min_length=1, max_length=512, strict=True)]
_UNIX_MS = Annotated[int, Field(ge=0, le=9_223_372_036_854_775_807, strict=True)]
_POSITIVE_BYTES = Annotated[
    int,
    Field(gt=0, le=9_223_372_036_854_775_807, strict=True),
]

INSTALL_CANDIDATE_RECEIPT_SCHEMA_VERSION = "aar.install-candidate-receipt.v1"
CLEAN_INSTALL_PREPARATION_SCHEMA_VERSION = "aar.clean-install-preparation.v1"
CLEAN_INSTALL_DATABASE_IDENTITY_SCHEMA_VERSION = "aar.clean-install-database-identity.v1"
MIGRATION_ATTESTATION_PAYLOAD_SCHEMA_VERSION = "aar.migration-v6-attestation-payload.v1"


class InstallCandidateFactoryEntry(StrictModel):
    """One package-owned factory member declared by an install receipt."""

    factory_id: CapabilityName
    wheel_member: _POSIX_MEMBER
    implementation_digest: Digest

    @model_validator(mode="after")
    def member_is_safe(self) -> Self:
        member = self.wheel_member
        if (
            not member.startswith("aar/")
            or member.endswith("/")
            or "\\" in member
            or "\x00" in member
            or member.startswith("/")
            or any(part in {"", ".", ".."} for part in member.split("/"))
        ):
            raise ValueError("wheel_member must be a safe relative POSIX aar/ member")
        return self


class InstallCandidateReceipt(StrictModel):
    """The exact self-digested candidate/wheel receipt accepted by C1."""

    schema_version: Literal["aar.install-candidate-receipt.v1"]
    candidate: ProviderReadyCandidate
    wheel_size_bytes: _POSITIVE_BYTES
    wheel_digest: Digest
    contract_manifest_digest: Digest
    skill_digest: Digest
    factory_entries: tuple[InstallCandidateFactoryEntry, ...] = Field(min_length=1, max_length=6)
    receipt_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        schema_version: Literal[
            "aar.install-candidate-receipt.v1"
        ] = INSTALL_CANDIDATE_RECEIPT_SCHEMA_VERSION,
        candidate: ProviderReadyCandidate,
        wheel_size_bytes: int,
        wheel_digest: Digest,
        contract_manifest_digest: Digest,
        skill_digest: Digest,
        factory_entries: tuple[InstallCandidateFactoryEntry, ...],
    ) -> Self:
        payload = {
            "schema_version": schema_version,
            "candidate": candidate,
            "wheel_size_bytes": wheel_size_bytes,
            "wheel_digest": wheel_digest,
            "contract_manifest_digest": contract_manifest_digest,
            "skill_digest": skill_digest,
            "factory_entries": tuple(sorted(factory_entries, key=lambda item: item.factory_id)),
        }
        provisional = cls.model_construct(
            **payload,
            receipt_digest="sha256:" + "0" * 64,
            _fields_set=set(payload) | {"receipt_digest"},
        )
        payload["receipt_digest"] = canonical_sha256(
            {
                key: value
                for key, value in provisional.model_dump(mode="json").items()
                if key != "receipt_digest"
            }
        )
        return cls(**payload)

    @model_validator(mode="after")
    def repeated_fields_and_entries_are_canonical(self) -> Self:
        candidate = self.candidate
        if self.wheel_digest != candidate.wheel_digest:
            raise ValueError("receipt wheel_digest must equal candidate.wheel_digest")
        if self.contract_manifest_digest != candidate.contract_manifest_digest:
            raise ValueError(
                "receipt contract_manifest_digest must equal candidate.contract_manifest_digest"
            )
        if self.skill_digest != candidate.skill_digest:
            raise ValueError("receipt skill_digest must equal candidate.skill_digest")
        factory_ids = [entry.factory_id for entry in self.factory_entries]
        if factory_ids != sorted(factory_ids) or len(factory_ids) != len(set(factory_ids)):
            raise ValueError("factory_entries must be sorted by unique factory_id")
        if self.receipt_digest != _self_digest(self, "receipt_digest"):
            raise ValueError("receipt digest does not match canonical receipt bytes")
        return self


class CleanInstallCandidateProjection(StrictModel):
    """The four candidate fields permitted in preparation/attestation evidence."""

    source_commit: SourceCommit
    wheel_digest: Digest
    contract_manifest_digest: Digest
    skill_digest: Digest

    @classmethod
    def from_candidate(cls, candidate: ProviderReadyCandidate) -> Self:
        return cls(
            source_commit=candidate.source_commit,
            wheel_digest=candidate.wheel_digest,
            contract_manifest_digest=candidate.contract_manifest_digest,
            skill_digest=candidate.skill_digest,
        )


class CleanInstallDatabaseIdentity(StrictModel):
    """The canonical, path-independent database identity material."""

    schema_version: Literal["aar.clean-install-database-identity.v1"]
    runtime_home_digest: Digest
    database_name: Literal["reference.sqlite3"]


class CleanInstallPreparation(StrictModel):
    """The sole clean-install preparation object (without a staging path)."""

    schema_version: Literal["aar.clean-install-preparation.v1"]
    install_epoch: _INSTALL_EPOCH
    runtime_home_digest: Digest
    database_identity: Annotated[
        str,
        StringConstraints(pattern=r"^db-[0-9a-f]{64}$", strict=True),
    ]
    database_name: Literal["reference.sqlite3"]
    empty_v5_backup_digest: Digest
    empty_v5_backup_size_bytes: _POSITIVE_BYTES
    canonical_v5_row_set_digest: Digest
    intent_digest: Digest
    candidate: CleanInstallCandidateProjection
    migration_sql_digest: Digest
    projected_external_authority_store_id: Annotated[
        str,
        StringConstraints(pattern=r"^authority-[0-9a-f]{64}$", strict=True),
    ]

    @property
    def external_authority_prepared_digest(self) -> str:
        """Return the digest stored by the v6 attestation for this object."""

        return canonical_sha256(self.model_dump(mode="json"))


class MigrationAttestationPayload(StrictModel):
    """The unchanged v6 attestation payload accepted by ``apply_registry_v6``."""

    schema_version: Literal["aar.migration-v6-attestation-payload.v1"]
    migration_version: Literal[6]
    cutover_epoch: _INSTALL_EPOCH
    snapshot_id: _INSTALL_SNAPSHOT
    snapshot_sha256: Digest
    snapshot_size_bytes: _POSITIVE_BYTES
    canonical_v5_row_set_digest: Digest
    source_commit: SourceCommit
    wheel_digest: Digest
    profile_digest: Digest
    skill_digest: Digest
    contract_manifest_digest: Digest
    migration_sql_digest: Digest
    external_authority_store_id: Annotated[
        str,
        StringConstraints(pattern=r"^authority-[0-9a-f]{64}$", strict=True),
    ]
    external_authority_prepared_digest: Digest
    started_at_unix_ms: _UNIX_MS
    completed_at_unix_ms: _UNIX_MS
    foreign_key_violation_count: Literal[0]
    integrity_result: Literal["ok"]

    @model_validator(mode="after")
    def timestamp_order_is_explicit(self) -> Self:
        if self.started_at_unix_ms > self.completed_at_unix_ms:
            raise ValueError(
                "started_at_unix_ms must be less than or equal to completed_at_unix_ms"
            )
        return self


class MigrationAttestationDocument(StrictModel):
    """A self-digested wrapper matching the frozen migration contract."""

    attestation: MigrationAttestationPayload
    attestation_digest: Digest

    @classmethod
    def issue(cls, payload: MigrationAttestationPayload) -> Self:
        return cls(
            attestation=payload,
            attestation_digest=canonical_sha256(payload.model_dump(mode="json")),
        )

    @model_validator(mode="after")
    def digest_is_content_bound(self) -> Self:
        if self.attestation_digest != canonical_sha256(self.attestation.model_dump(mode="json")):
            raise ValueError("attestation digest does not match canonical payload bytes")
        return self


def _self_digest(document: StrictModel, digest_field: str) -> str:
    payload = document.model_dump(mode="json")
    payload.pop(digest_field, None)
    return canonical_sha256(payload)


def require_sorted_unique(values: Iterable[str], field_name: str) -> None:
    """Validate a canonical string collection at an external composition seam."""

    materialized = list(values)
    if materialized != sorted(materialized) or len(materialized) != len(set(materialized)):
        raise ValueError(f"{field_name} must be sorted and unique")


def candidate_projection(candidate: ProviderReadyCandidate) -> CleanInstallCandidateProjection:
    """Return the exact four-field clean-install candidate projection."""

    return CleanInstallCandidateProjection.from_candidate(candidate)


# Short aliases make the ownership boundary convenient without reusing the
# older caller-work CandidateReceipt name.
FactoryEntry = InstallCandidateFactoryEntry
InstallReceipt = InstallCandidateReceipt

__all__ = [
    "CLEAN_INSTALL_DATABASE_IDENTITY_SCHEMA_VERSION",
    "CLEAN_INSTALL_PREPARATION_SCHEMA_VERSION",
    "INSTALL_CANDIDATE_RECEIPT_SCHEMA_VERSION",
    "MIGRATION_ATTESTATION_PAYLOAD_SCHEMA_VERSION",
    "CleanInstallCandidateProjection",
    "CleanInstallDatabaseIdentity",
    "CleanInstallPreparation",
    "FactoryEntry",
    "InstallCandidateFactoryEntry",
    "InstallCandidateReceipt",
    "InstallReceipt",
    "MigrationAttestationDocument",
    "MigrationAttestationPayload",
    "candidate_projection",
    "require_sorted_unique",
]
