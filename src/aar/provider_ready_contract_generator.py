"""Deterministic provider-ready schema, fixture, manifest, and graph assets."""

# The fixture builders intentionally keep several long exact-value rows readable.
# ruff: noqa: E501

from __future__ import annotations

import hashlib
import json
import tempfile
from collections.abc import Mapping
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from pydantic import ValidationError

from aar.canonical import canonical_json_bytes, canonical_sha256, pretty_json_bytes
from aar.provider_ready_contract import (
    PROVIDER_READY_SCHEMA_MODELS,
    ProviderReadyContractError,
    load_provider_ready_json_bytes,
    validate_provider_ready_document_bytes,
)
from aar.provider_ready_evaluation_models import (
    EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
    PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION,
    EvaluationEvidenceClassification,
    PairedEvaluationAdmission,
    ProviderAliasAttestation,
)
from aar.provider_ready_models import (
    HOST_ACTIVATION_INTENT_SCHEMA_VERSION,
    HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
    METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
    ActivationGrantPolicy,
    ActivationPlannerBinding,
    ActivationRoutePolicy,
    ActivationRuntimeBinding,
    GrantBudgetCeiling,
    HostActivationIntent,
    HostActivationProfile,
    MethodAdapterManifest,
    ProviderReadyCandidate,
)
from aar.provider_ready_operator_models import (
    ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
    CUTOVER_PLAN_SCHEMA_VERSION,
    CUTOVER_RECEIPT_SCHEMA_VERSION,
    OPERATOR_PREPARED_MARKER_SCHEMA_VERSION,
    OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION,
    RESTORE_RECEIPT_SCHEMA_VERSION,
    ActivationGenerationAuthority,
    CandidateBinding,
    CutoverPlan,
    CutoverPreparation,
    CutoverReceipt,
    DatabaseChecks,
    EpochOwnerBinding,
    FileArtifact,
    NonterminalCounts,
    OperatorPreparedMarker,
    OperatorTerminalMarker,
    RestoreFrontierProof,
    RestorePreparation,
    RestoreReceipt,
    SidecarObservation,
    SnapshotPlan,
)
from aar.provider_ready_runtime_models import (
    ACTIVATION_READBACK_SCHEMA_VERSION,
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    ActivationReadback,
    BackendAvailability,
    PlannerReadback,
    WorkbenchGrantSet,
)

SCHEMA_BUNDLE_SCHEMA_VERSION = "aar.provider-ready-schema-bundle.v1"
FIXTURE_MANIFEST_SCHEMA_VERSION = "aar.provider-ready-fixture-manifest.v1"
MAX_COUNTER = 9_223_372_036_854_775_807
AUTHORITY_STORE_ID = "local-file-authority-v1:runtime-primary"
BASE_DIGEST = canonical_sha256({"fixture": "a1c-gf-normative-v1"})
METHOD_ORDER = (
    "model.request",
    "subagent.submit",
    "subagent.result",
    "evidence.query",
    "artifact.put",
    "effect.propose",
)
EVIDENCE_SOURCES = (
    "installed_assets",
    "registry",
    "activation_history",
    "activation_current",
    "epoch_marker",
    "supervisor",
    "capability_registry",
    "grant_issuer",
)
VALID_PATHS = (
    "valid/method-adapter.json",
    "valid/activation-intent.json",
    "valid/final-profile.json",
    "valid/activation-generation-authority-first.json",
    "valid/cutover-plan.json",
    "valid/operator-prepared-cutover.json",
    "valid/operator-prepared-restore.json",
    "valid/cutover-receipt-committed.json",
    "valid/cutover-receipt-aborted.json",
    "valid/cutover-receipt-recovery-required.json",
    "valid/restore-receipt-committed.json",
    "valid/restore-receipt-recovery-required.json",
    "valid/operator-terminal-cutover-committed.json",
    "valid/operator-terminal-cutover-aborted.json",
    "valid/operator-terminal-cutover-recovery-required.json",
    "valid/operator-terminal-restore-committed.json",
    "valid/operator-terminal-restore-recovery-required.json",
    "valid/activation-readback-active.json",
    "valid/activation-readback-unconfigured.json",
    "valid/workbench-grant-set.json",
    "valid/provider-alias-attestation.json",
    "valid/evaluation-classification-aar.json",
    "valid/evaluation-classification-prime.json",
    "valid/evaluation-classification-insufficient.json",
    "valid/paired-admission.json",
)
STALE_BASES = (
    ("invalid/stale-method-adapter.json", "valid/method-adapter.json", "manifest_digest"),
    ("invalid/stale-activation-intent.json", "valid/activation-intent.json", "intent_digest"),
    ("invalid/stale-final-profile.json", "valid/final-profile.json", "profile_digest"),
    (
        "invalid/stale-activation-generation-authority.json",
        "valid/activation-generation-authority-first.json",
        "authority_digest",
    ),
    ("invalid/stale-cutover-plan.json", "valid/cutover-plan.json", "plan_digest"),
    (
        "invalid/stale-operator-prepared-marker.json",
        "valid/operator-prepared-cutover.json",
        "prepared_marker_digest",
    ),
    ("invalid/stale-cutover-receipt.json", "valid/cutover-receipt-committed.json", "receipt_digest"),
    ("invalid/stale-restore-receipt.json", "valid/restore-receipt-committed.json", "receipt_digest"),
    (
        "invalid/stale-operator-terminal-marker.json",
        "valid/operator-terminal-cutover-committed.json",
        "marker_digest",
    ),
    ("invalid/stale-activation-readback.json", "valid/activation-readback-active.json", "readback_digest"),
    ("invalid/stale-workbench-grant-set.json", "valid/workbench-grant-set.json", "grant_set_digest"),
    (
        "invalid/stale-provider-alias-attestation.json",
        "valid/provider-alias-attestation.json",
        "alias_digest",
    ),
    (
        "invalid/stale-evaluation-classification.json",
        "valid/evaluation-classification-aar.json",
        "classification_digest",
    ),
    ("invalid/stale-paired-admission.json", "valid/paired-admission.json", "admission_digest"),
)


def _dump(model: Any) -> dict[str, Any]:
    value = model.model_dump(mode="json")
    assert isinstance(value, dict)
    return value


def _redigest(payload: dict[str, Any], digest_field: str) -> dict[str, Any]:
    amended = deepcopy(payload)
    amended.pop(digest_field, None)
    amended[digest_field] = canonical_sha256(amended)
    return amended


def _candidate(*, package_version: str = "0.6.0a0") -> ProviderReadyCandidate:
    return ProviderReadyCandidate(
        package_version=package_version,
        source_commit="0" * 40,
        wheel_digest=BASE_DIGEST,
        contract_manifest_digest=BASE_DIGEST,
        skill_digest=BASE_DIGEST,
    )


def _budget() -> GrantBudgetCeiling:
    return GrantBudgetCeiling(
        wall_time_ms=900_000,
        model_requests=128,
        input_tokens=MAX_COUNTER,
        output_tokens=MAX_COUNTER,
        child_operations=64,
        artifact_bytes=33_554_432,
    )


def _manifest(
    method: str,
    *,
    backend_kind: str = "native",
    reference_only: bool = False,
    evidence_tier: str = "host_receipt_bound",
) -> MethodAdapterManifest:
    suffix = method.replace(".", "-")
    if reference_only:
        evidence_tier = "unknown"
    return MethodAdapterManifest.issue(
        schema_version=METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
        method=method,  # type: ignore[arg-type]
        contract_id=f"aar.broker-contract.{suffix}.v2",
        request_schema_digest=BASE_DIGEST,
        response_schema_digest=BASE_DIGEST,
        backend_kind=backend_kind,  # type: ignore[arg-type]
        factory_id=f"aar.factory.{suffix}.v1",
        factory_digest=BASE_DIGEST,
        adapter_id=f"adapter-{suffix}",
        adapter_generation_policy="runtime_generation",
        reference_only=reference_only,
        evidence_tier=evidence_tier,  # type: ignore[arg-type]
        lookup_supported=True,
        cancel_supported=False,
    )


def _intent(
    *,
    profile_id: str = "hermes-caller-luna-max-v1",
    activation_generation: int = 1,
    mode: str = "caller_delegated_ticketed",
    previous_activation_authority_digest: str | None = None,
) -> HostActivationIntent:
    model_backend = "caller_driver" if mode == "caller_delegated_ticketed" else "native"
    adapters = tuple(
        _manifest(
            method,
            backend_kind=model_backend if method == "model.request" else "native",
            evidence_tier="caller_observed" if method == "model.request" else "host_receipt_bound",
        )
        for method in METHOD_ORDER
    )
    return HostActivationIntent.issue(
        schema_version=HOST_ACTIVATION_INTENT_SCHEMA_VERSION,
        profile_id=profile_id,
        activation_generation=activation_generation,
        previous_activation_authority_digest=previous_activation_authority_digest,
        candidate=_candidate(),
        runtime=ActivationRuntimeBinding(
            runtime_home_digest=BASE_DIGEST,
            database_identity="runtime-primary",
            required_registry_version=6,
            programmable_backend="ipython",
            security_profile="trusted_local",
        ),
        planner=ActivationPlannerBinding(
            mode=mode,  # type: ignore[arg-type]
            method="model.request",
            directive_schema_version="aar.rlm-directive.v1",
            directive_schema_digest=BASE_DIGEST,
        ),
        adapters=adapters,
        routes=ActivationRoutePolicy.issue(
            catalog_digest=BASE_DIGEST,
            allowed_profile_ids=("hermes-codex-luna-max",),
            fallback_policy="none",
            cache_policy="disabled",
        ),
        grant_policy=ActivationGrantPolicy.issue(
            principal_patterns=("aar-eval-runner",),
            capabilities=("rlm.workbench.execute", "rlm.workbench.read"),
            budget_ceiling=_budget(),
            max_deadline_ms=900_000,
        ),
        cutover_authority_store_id=AUTHORITY_STORE_ID,
        recovery_compatibility_digest=BASE_DIGEST,
    )


def _profile(intent: HostActivationIntent) -> HostActivationProfile:
    return HostActivationProfile.issue(
        schema_version=HOST_ACTIVATION_PROFILE_SCHEMA_VERSION,
        intent=intent,
        migration_attestation_digest=BASE_DIGEST,
    )


def _file(*, artifact_id: str = "snapshot-001", size_bytes: int = 4096) -> FileArtifact:
    return FileArtifact(artifact_id=artifact_id, digest=BASE_DIGEST, size_bytes=size_bytes)


def _absent_sidecar() -> SidecarObservation:
    return SidecarObservation(state="absent", file_identity=None, size_bytes=None, digest=None)


def _checks() -> DatabaseChecks:
    return DatabaseChecks(integrity_result="ok", foreign_key_violation_count=0)


def _owner() -> EpochOwnerBinding:
    return EpochOwnerBinding(
        authority_store_id=AUTHORITY_STORE_ID,
        operator_identity_digest=BASE_DIGEST,
        runtime_owner_state="absent",
        exclusive_lock_state="available",
    )


def _counts() -> NonterminalCounts:
    return NonterminalCounts(
        operations=0,
        attempts=0,
        workbench_jobs=0,
        caller_tickets=0,
        workers=0,
    )


def _plan(*, intent: HostActivationIntent, profile: HostActivationProfile) -> CutoverPlan:
    return CutoverPlan.issue(
        schema_version=CUTOVER_PLAN_SCHEMA_VERSION,
        cutover_epoch="cutover-001",
        runtime_home_digest=BASE_DIGEST,
        database_identity="database-primary",
        database_file=_file(artifact_id="database-primary", size_bytes=8192),
        owner=_owner(),
        source_registry_version=5,
        source_registry_schema_digest=BASE_DIGEST,
        canonical_v5_row_set_digest=BASE_DIGEST,
        wal=_absent_sidecar(),
        shm=_absent_sidecar(),
        database_checks=_checks(),
        nonterminal_counts=_counts(),
        snapshot=SnapshotPlan(
            snapshot_id="snapshot-001",
            destination="/runtime/snapshots/snapshot-001.db",
            backup_mode="sqlite_backup",
        ),
        candidate=CandidateBinding(**_dump(_candidate())),
        migration_sql_digest=BASE_DIGEST,
        activation_intent_digest=intent.intent_digest,
        final_profile_output="/runtime/profiles/final.json",
        profile_id=profile.intent.profile_id,
        proposed_activation_generation=intent.activation_generation,
        previous_activation_authority_digest=intent.previous_activation_authority_digest,
        authority_store_id=AUTHORITY_STORE_ID,
        allowed_recovery_actions=("abort", "apply", "status"),
        created_at_unix_ms=100,
        expires_at_unix_ms=200,
    )


def _authority(*, intent: HostActivationIntent, profile: HostActivationProfile) -> ActivationGenerationAuthority:
    return ActivationGenerationAuthority.issue(
        schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
        authority_store_id=AUTHORITY_STORE_ID,
        profile_id=intent.profile_id,
        activation_generation=intent.activation_generation,
        intent_digest=intent.intent_digest,
        profile_digest=profile.profile_digest,
        migration_attestation_digest=profile.migration_attestation_digest,
        previous_activation_authority_digest=intent.previous_activation_authority_digest,
    )


def _cutover_preparation(plan: CutoverPlan) -> CutoverPreparation:
    return CutoverPreparation(
        plan=plan,
        plan_digest=plan.plan_digest,
        snapshot=_file(artifact_id=plan.snapshot.snapshot_id),
        pre_migration_database_digest=plan.database_file.digest,
        wal=plan.wal,
        shm=plan.shm,
    )


def _prepared_cutover(plan: CutoverPlan) -> OperatorPreparedMarker:
    return OperatorPreparedMarker.issue(
        schema_version=OPERATOR_PREPARED_MARKER_SCHEMA_VERSION,
        kind="cutover",
        epoch=plan.cutover_epoch,
        operator_identity_digest=canonical_sha256({"operator": "apply"}),
        prepared_at_unix_ms=150,
        cutover=_cutover_preparation(plan),
        restore=None,
    )


def _cutover_receipt(
    *,
    plan: CutoverPlan,
    prepared: OperatorPreparedMarker,
    intent: HostActivationIntent,
    profile: HostActivationProfile,
    authority: ActivationGenerationAuthority,
    outcome: str,
) -> CutoverReceipt:
    committed = outcome == "committed"
    return CutoverReceipt.issue(
        schema_version=CUTOVER_RECEIPT_SCHEMA_VERSION,
        cutover_epoch=plan.cutover_epoch,
        plan_digest=plan.plan_digest,
        outcome=outcome,  # type: ignore[arg-type]
        database_commit_state="committed" if committed else "not_committed",
        prepared_marker_digest=prepared.prepared_marker_digest,
        snapshot=_file(),
        pre_migration_database_digest=plan.database_file.digest,
        canonical_v5_row_set_digest=BASE_DIGEST,
        migration_sql_digest=BASE_DIGEST,
        migration_attestation_digest=profile.migration_attestation_digest if committed else None,
        profile_id=profile.intent.profile_id,
        activation_generation=intent.activation_generation,
        previous_activation_authority_digest=intent.previous_activation_authority_digest,
        activation_authority_digest=authority.authority_digest if committed else None,
        post_migration_database_digest=BASE_DIGEST if committed else None,
        database_checks=_checks() if committed else None,
        candidate=CandidateBinding(**_dump(_candidate())),
        intent_digest=intent.intent_digest,
        generated_profile_digest=profile.profile_digest if committed else None,
        generated_profile_output="/runtime/profiles/final.json",
        started_at_unix_ms=100,
        db_committed_at_unix_ms=150 if committed else None,
        completed_at_unix_ms=175,
    )


def _restore_preparation(
    *, plan: CutoverPlan, prepared: OperatorPreparedMarker, terminal: OperatorTerminalMarker
) -> RestorePreparation:
    return RestorePreparation(
        cutover_plan_digest=plan.plan_digest,
        cutover_prepared_marker_digest=prepared.prepared_marker_digest,
        cutover_terminal_marker_digest=terminal.marker_digest,
        snapshot=_file(),
        runtime_home_digest=plan.runtime_home_digest,
        target_database_identity=plan.database_identity,
        pre_restore_database=_file(artifact_id="pre-restore-db"),
        pre_restore_wal=_absent_sidecar(),
        pre_restore_shm=_absent_sidecar(),
        no_committed_v6_attestation=True,
        no_activation_history_record=True,
        no_v6_runtime_or_workbench_write=True,
    )


def _prepared_restore(
    *, plan: CutoverPlan, prepared: OperatorPreparedMarker, terminal: OperatorTerminalMarker
) -> OperatorPreparedMarker:
    return OperatorPreparedMarker.issue(
        schema_version=OPERATOR_PREPARED_MARKER_SCHEMA_VERSION,
        kind="restore",
        epoch="restore-001",
        operator_identity_digest=canonical_sha256({"operator": "restore"}),
        prepared_at_unix_ms=150,
        cutover=None,
        restore=_restore_preparation(plan=plan, prepared=prepared, terminal=terminal),
    )


def _restore_receipt(
    *,
    plan: CutoverPlan,
    prepared: OperatorPreparedMarker,
    terminal: OperatorTerminalMarker,
    restore_prepared: OperatorPreparedMarker,
    authority: ActivationGenerationAuthority,
    outcome: str,
) -> RestoreReceipt:
    committed = outcome == "restored_pre_frontier"
    return RestoreReceipt.issue(
        schema_version=RESTORE_RECEIPT_SCHEMA_VERSION,
        restore_epoch="restore-001",
        cutover_plan_digest=plan.plan_digest,
        outcome=outcome,  # type: ignore[arg-type]
        replacement_commit_state="committed" if committed else "unknown",
        snapshot=_file(),
        runtime_home_digest=plan.runtime_home_digest,
        target_database_identity=plan.database_identity,
        pre_restore_database=_file(artifact_id="pre-restore-db"),
        pre_restore_wal=_absent_sidecar(),
        pre_restore_shm=_absent_sidecar(),
        restored_database_digest=BASE_DIGEST if committed else None,
        sidecar_disposition="unchanged",
        database_checks=_checks() if committed else None,
        cutover_prepared_marker_digest=prepared.prepared_marker_digest,
        cutover_terminal_marker_digest=terminal.marker_digest,
        restore_prepared_marker_digest=restore_prepared.prepared_marker_digest,
        activation_authority_digest_before_restore=authority.authority_digest,
        activation_authority_digest_after_restore=authority.authority_digest,
        activation_authority_disposition="unchanged",
        frontier_proof=RestoreFrontierProof(
            no_committed_v6_attestation=True,
            no_activation_history_record=True,
            no_v6_runtime_or_workbench_write=True,
        ),
        started_at_unix_ms=100,
        replacement_committed_at_unix_ms=150 if committed else None,
        completed_at_unix_ms=175,
    )


def _terminal(receipt: CutoverReceipt | RestoreReceipt) -> OperatorTerminalMarker:
    if isinstance(receipt, CutoverReceipt):
        kind = {
            "committed": "cutover_committed",
            "aborted_before_db_commit": "cutover_aborted",
            "recovery_required": "cutover_recovery_required",
        }[receipt.outcome]
        return OperatorTerminalMarker.issue(
            schema_version=OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION,
            kind=kind,  # type: ignore[arg-type]
            epoch=receipt.cutover_epoch,
            receipt_schema_version=CUTOVER_RECEIPT_SCHEMA_VERSION,
            receipt=receipt,
            published_at_unix_ms=200,
        )
    kind = "restore_committed" if receipt.outcome == "restored_pre_frontier" else "restore_recovery_required"
    return OperatorTerminalMarker.issue(
        schema_version=OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION,
        kind=kind,  # type: ignore[arg-type]
        epoch=receipt.restore_epoch,
        receipt_schema_version=RESTORE_RECEIPT_SCHEMA_VERSION,
        receipt=receipt,
        published_at_unix_ms=200,
    )


def _row(
    method: str,
    *,
    backend_kind: str = "unconfigured",
    adapter_generation: int = 7,
    evidence_tier: str = "unknown",
) -> BackendAvailability:
    executable = backend_kind in {"native", "caller_driver"}
    reference = backend_kind == "reference"
    return BackendAvailability(
        method=method,
        contract_id=f"aar.broker-contract.{method.replace('.', '-')}.v2",
        request_schema_digest=BASE_DIGEST,
        response_schema_digest=BASE_DIGEST,
        backend_kind=backend_kind,  # type: ignore[arg-type]
        configured=backend_kind != "unconfigured",
        reference_only=reference,
        adapter_id=f"adapter-{method.replace('.', '-')}" if executable else None,
        adapter_generation=adapter_generation if executable else None,
        evidence_tier=(evidence_tier if executable else "unknown"),  # type: ignore[arg-type]
    )


def _inactive_methods() -> tuple[BackendAvailability, ...]:
    return tuple(_row(method, backend_kind="reference" if method == "artifact.put" else "unconfigured") for method in METHOD_ORDER)


def _active_methods() -> tuple[BackendAvailability, ...]:
    return (
        _row("model.request", backend_kind="caller_driver", evidence_tier="caller_observed"),
        _row("subagent.submit", backend_kind="native", evidence_tier="host_receipt_bound"),
        _row("subagent.result", backend_kind="native", evidence_tier="provider_attested"),
        _row("evidence.query", backend_kind="native", evidence_tier="unknown"),
        _row("artifact.put", backend_kind="reference"),
        _row("effect.propose", backend_kind="native", evidence_tier="host_receipt_bound"),
    )


def _planner(mode: str | None = None, *, ready: bool = False) -> PlannerReadback:
    if mode is None:
        return PlannerReadback(mode=None, ready=ready, factory_id=None, factory_digest=None, method_manifest_digest=None)
    return PlannerReadback(
        mode=mode,  # type: ignore[arg-type]
        ready=ready,
        factory_id="aar.factory.model-request.v1",
        factory_digest=BASE_DIGEST,
        method_manifest_digest=BASE_DIGEST,
    )


def _readback_base(*, state: str, reason_code: str = "none") -> dict[str, Any]:
    return {
        "schema_version": ACTIVATION_READBACK_SCHEMA_VERSION,
        "observed_at_unix_ms": 100,
        "state": state,
        "reason_code": reason_code,
        "runtime_generation": None,
        "supervisor_process_identity_digest": None,
        "candidate": None,
        "registry_schema_version": None,
        "registry_schema_digest": None,
        "migration_attestation_digest": None,
        "profile_id": None,
        "activation_generation": None,
        "intent_digest": None,
        "profile_digest": None,
        "previous_activation_authority_digest": None,
        "activation_authority_digest": None,
        "grant_set_digest": None,
        "capability_digest": None,
        "broker_catalog_digest": None,
        "tool_surface_digest": None,
        "methods": _inactive_methods(),
        "planner": _planner(),
        "route_catalog_digest": None,
        "route_profile_ids": (),
        "authority_store_id": None,
        "authority_history_tip_digest": None,
        "operator_epoch_kind": None,
        "operator_epoch": None,
        "latest_operator_receipt_digest": None,
        "evidence_sources": (),
    }


def _readback_unconfigured() -> ActivationReadback:
    return ActivationReadback.issue(**_readback_base(state="unconfigured"))


def _grant_set(
    *, intent: HostActivationIntent, profile: HostActivationProfile, authority: ActivationGenerationAuthority
) -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=7,
        activation_generation=intent.activation_generation,
        profile_id=profile.intent.profile_id,
        profile_digest=profile.profile_digest,
        activation_authority_digest=authority.authority_digest,
        capability_digest=BASE_DIGEST,
        route_catalog_digest=BASE_DIGEST,
        principal_ids=("aar-eval-runner",),
        session_binding_policy="bind_exact_request_session",
        capabilities=("rlm.workbench.execute", "rlm.workbench.read"),
        budget_ceiling=_budget(),
        max_ttl_ms=900_000,
    )


def _readback_active(
    *, intent: HostActivationIntent, profile: HostActivationProfile, authority: ActivationGenerationAuthority,
    grant_set: WorkbenchGrantSet,
) -> ActivationReadback:
    payload = _readback_base(state="active")
    payload.update(
        runtime_generation=7,
        supervisor_process_identity_digest=BASE_DIGEST,
        candidate=CandidateBinding(**_dump(_candidate())),
        registry_schema_version=6,
        registry_schema_digest=BASE_DIGEST,
        migration_attestation_digest=profile.migration_attestation_digest,
        profile_id=profile.intent.profile_id,
        activation_generation=intent.activation_generation,
        intent_digest=intent.intent_digest,
        profile_digest=profile.profile_digest,
        activation_authority_digest=authority.authority_digest,
        grant_set_digest=grant_set.grant_set_digest,
        capability_digest=BASE_DIGEST,
        broker_catalog_digest=BASE_DIGEST,
        tool_surface_digest=BASE_DIGEST,
        methods=_active_methods(),
        planner=_planner("caller_delegated_ticketed", ready=True),
        route_catalog_digest=BASE_DIGEST,
        route_profile_ids=("hermes-codex-luna-max",),
        authority_store_id=AUTHORITY_STORE_ID,
        authority_history_tip_digest=authority.authority_digest,
        evidence_sources=EVIDENCE_SOURCES,
    )
    return ActivationReadback.issue(**payload)


def _alias() -> ProviderAliasAttestation:
    return ProviderAliasAttestation.issue(
        schema_version=PROVIDER_ALIAS_ATTESTATION_SCHEMA_VERSION,
        launcher_alias="hermes-codex",
        canonical_provider_identity="openai-codex",
        wire_api="openai-codex-responses",
        prime_executable_digest=BASE_DIGEST,
        prime_config_digest=BASE_DIGEST,
        model_registry_digest=BASE_DIGEST,
        provider_entry_digest=BASE_DIGEST,
        credential_resolver_executable_digest=BASE_DIGEST,
        model="gpt-5.6-luna",
        requested_reasoning="max",
        created_at_unix_ms=100,
    )


def _classification(
    *, arm: str, run_id: str, attempt_id: str, provider_tier: str, model_tier: str,
    reasoning_tier: str, fallback_tier: str, cache_tier: str, effective_provider: str | None,
    effective_model: str | None, effective_reasoning: str | None, effective_fallback_policy: str | None,
    effective_cache_policy: str | None, route_qualification: str, usage_tier: str,
    attempt_visibility: str,
) -> EvaluationEvidenceClassification:
    return EvaluationEvidenceClassification.issue(
        schema_version=EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
        run_id=run_id,
        arm=arm,  # type: ignore[arg-type]
        attempt_id=attempt_id,
        requested_provider="openai-codex",
        requested_model="gpt-5.6-luna",
        requested_reasoning="max",
        requested_fallback_policy="none",
        requested_cache_policy="disabled",
        effective_provider=effective_provider,
        effective_model=effective_model,
        effective_reasoning=effective_reasoning,
        effective_fallback_policy=effective_fallback_policy,
        effective_cache_policy=effective_cache_policy,
        provider_tier=provider_tier,  # type: ignore[arg-type]
        model_tier=model_tier,  # type: ignore[arg-type]
        reasoning_tier=reasoning_tier,  # type: ignore[arg-type]
        fallback_tier=fallback_tier,  # type: ignore[arg-type]
        cache_tier=cache_tier,  # type: ignore[arg-type]
        route_qualification=route_qualification,  # type: ignore[arg-type]
        usage_tier=usage_tier,  # type: ignore[arg-type]
        attempt_visibility=attempt_visibility,  # type: ignore[arg-type]
        contradiction_components=(),
        source_receipt_digests=(BASE_DIGEST,),
    )


def _paired(alias: ProviderAliasAttestation, aar: EvaluationEvidenceClassification, prime: EvaluationEvidenceClassification) -> PairedEvaluationAdmission:
    return PairedEvaluationAdmission.compose(
        alias_attestation=alias,
        aar_classification=aar,
        prime_classification=prime,
        paired_run_id="paired-run",
        created_at_unix_ms=100,
        expires_at_unix_ms=900_100,
        candidate_digest=BASE_DIGEST,
        aar_profile_digest=BASE_DIGEST,
        prime_launch_digest=BASE_DIGEST,
        case_digest=BASE_DIGEST,
        fixture_digest=BASE_DIGEST,
        hidden_oracle_digest=BASE_DIGEST,
        artifact_contract_digest=BASE_DIGEST,
        tool_network_policy_digest=BASE_DIGEST,
        protocol_digest=BASE_DIGEST,
        budget_digest=BASE_DIGEST,
        stop_matrix_digest=BASE_DIGEST,
        arm_order=("aar", "prime"),
        operator_run_authority_digest=BASE_DIGEST,
    )


def _build_valid_documents() -> dict[str, dict[str, Any]]:
    intent = _intent()
    profile = _profile(intent)
    authority = _authority(intent=intent, profile=profile)
    plan = _plan(intent=intent, profile=profile)
    prepared_cutover = _prepared_cutover(plan)
    cutover_receipts = {
        outcome: _cutover_receipt(
            plan=plan, prepared=prepared_cutover, intent=intent, profile=profile,
            authority=authority, outcome=outcome,
        )
        for outcome in ("committed", "aborted_before_db_commit", "recovery_required")
    }
    cutover_terminals = {outcome: _terminal(receipt) for outcome, receipt in cutover_receipts.items()}
    prepared_restore = _prepared_restore(
        plan=plan, prepared=prepared_cutover, terminal=cutover_terminals["committed"]
    )
    restore_receipts = {
        outcome: _restore_receipt(
            plan=plan, prepared=prepared_cutover, terminal=cutover_terminals["committed"],
            restore_prepared=prepared_restore, authority=authority, outcome=outcome,
        )
        for outcome in ("restored_pre_frontier", "recovery_required")
    }
    restore_terminals = {outcome: _terminal(receipt) for outcome, receipt in restore_receipts.items()}
    grant_set = _grant_set(intent=intent, profile=profile, authority=authority)
    readback_active = _readback_active(
        intent=intent, profile=profile, authority=authority, grant_set=grant_set
    )
    alias = _alias()
    aar_classification = _classification(
        arm="aar", run_id="aar-run", attempt_id="aar-attempt", provider_tier="attested",
        model_tier="attested", reasoning_tier="attested", fallback_tier="attested", cache_tier="attested",
        effective_provider="openai-codex", effective_model="gpt-5.6-luna", effective_reasoning="max",
        effective_fallback_policy="none", effective_cache_policy="disabled",
        route_qualification="aar_live_qualified", usage_tier="request_receipt", attempt_visibility="complete",
    )
    prime_classification = _classification(
        arm="prime", run_id="prime-run", attempt_id="prime-attempt", provider_tier="observed",
        model_tier="observed", reasoning_tier="requested_only", fallback_tier="observed", cache_tier="observed",
        effective_provider="openai-codex", effective_model="gpt-5.6-luna", effective_reasoning=None,
        effective_fallback_policy="none", effective_cache_policy="disabled",
        route_qualification="prime_live_qualified", usage_tier="unavailable", attempt_visibility="unknown",
    )
    insufficient_classification = _classification(
        arm="aar", run_id="insufficient-run", attempt_id="insufficient-attempt", provider_tier="attested",
        model_tier="attested", reasoning_tier="requested_only", fallback_tier="attested", cache_tier="attested",
        effective_provider="openai-codex", effective_model="gpt-5.6-luna", effective_reasoning=None,
        effective_fallback_policy="none", effective_cache_policy="disabled",
        route_qualification="insufficient", usage_tier="unavailable", attempt_visibility="unknown",
    )
    admission = _paired(alias, aar_classification, prime_classification)
    models: dict[str, Any] = {
        "valid/method-adapter.json": intent.adapters[0],
        "valid/activation-intent.json": intent,
        "valid/final-profile.json": profile,
        "valid/activation-generation-authority-first.json": authority,
        "valid/cutover-plan.json": plan,
        "valid/operator-prepared-cutover.json": prepared_cutover,
        "valid/operator-prepared-restore.json": prepared_restore,
        "valid/cutover-receipt-committed.json": cutover_receipts["committed"],
        "valid/cutover-receipt-aborted.json": cutover_receipts["aborted_before_db_commit"],
        "valid/cutover-receipt-recovery-required.json": cutover_receipts["recovery_required"],
        "valid/restore-receipt-committed.json": restore_receipts["restored_pre_frontier"],
        "valid/restore-receipt-recovery-required.json": restore_receipts["recovery_required"],
        "valid/operator-terminal-cutover-committed.json": cutover_terminals["committed"],
        "valid/operator-terminal-cutover-aborted.json": cutover_terminals["aborted_before_db_commit"],
        "valid/operator-terminal-cutover-recovery-required.json": cutover_terminals["recovery_required"],
        "valid/operator-terminal-restore-committed.json": restore_terminals["restored_pre_frontier"],
        "valid/operator-terminal-restore-recovery-required.json": restore_terminals["recovery_required"],
        "valid/activation-readback-active.json": readback_active,
        "valid/activation-readback-unconfigured.json": _readback_unconfigured(),
        "valid/workbench-grant-set.json": grant_set,
        "valid/provider-alias-attestation.json": alias,
        "valid/evaluation-classification-aar.json": aar_classification,
        "valid/evaluation-classification-prime.json": prime_classification,
        "valid/evaluation-classification-insufficient.json": insufficient_classification,
        "valid/paired-admission.json": admission,
    }
    assert tuple(models) == VALID_PATHS
    return {path: _dump(model) for path, model in models.items()}


def _project_result(document: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    try:
        validate_provider_ready_document_bytes(canonical_json_bytes(document))
    except ProviderReadyContractError as error:
        return "fail", {
            "engine": "loader", "code": error.code, "location": None,
            "fragment": str(error), "targetIndex": None,
        }
    except ValidationError as error:
        target = error.errors()[0]
        return "fail", {
            "engine": "project", "code": target["type"], "location": list(target["loc"]),
            "fragment": target["msg"], "targetIndex": 0,
        }
    return "pass", None


def _independent_result(
    document: dict[str, Any], model_type_override: type[Any] | None = None
) -> tuple[str, dict[str, Any] | None]:
    schema_id = document.get("schema_version")
    model_type = model_type_override or PROVIDER_READY_SCHEMA_MODELS[schema_id]
    schema = model_type.model_json_schema(mode="validation")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(document),
        key=lambda item: (list(item.absolute_path), item.validator or "", item.message),
    )
    if not errors:
        return "pass", None
    target = errors[0]
    return "fail", {
        "engine": "independent", "code": target.validator,
        "location": list(target.absolute_path), "fragment": target.message, "targetIndex": 0,
    }


def _pointer_tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError(f"JSON pointer must start with '/': {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _pointer_get(document: Any, pointer: str) -> Any:
    current = document
    for token in _pointer_tokens(pointer):
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _pointer_set(document: Any, pointer: str, value: Any) -> None:
    tokens = _pointer_tokens(pointer)
    if not tokens:
        raise ValueError("root replacement is not supported")
    current = document
    for token in tokens[:-1]:
        current = current[int(token)] if isinstance(current, list) else current[token]
    final = tokens[-1]
    if isinstance(current, list):
        current[int(final)] = value
    else:
        current[final] = value


_GRAPH_JOINS = (
    ("adapter-intent-manifest", "adapter", "/manifest_digest", "intent", "/adapters/0/manifest_digest", "GRAPH_ADAPTER_INTENT_MISMATCH", "adapter manifest must equal intent model.request manifest"),
    ("intent-profile-digest", "intent", "/intent_digest", "profile", "/intent/intent_digest", "GRAPH_PROFILE_INTENT_MISMATCH", "profile embedded intent must equal standalone intent"),
    ("profile-authority-digest", "profile", "/profile_digest", "authority", "/profile_digest", "GRAPH_AUTHORITY_PROFILE_MISMATCH", "authority profile_digest must equal profile digest"),
    ("authority-generation", "intent", "/activation_generation", "authority", "/activation_generation", "GRAPH_AUTHORITY_GENERATION_MISMATCH", "authority generation must equal intent generation"),
    ("plan-intent", "plan", "/activation_intent_digest", "intent", "/intent_digest", "GRAPH_PLAN_INTENT_MISMATCH", "cutover plan must bind intent digest"),
    ("plan-owner-authority-store", "plan", "/authority_store_id", "authority", "/authority_store_id", "GRAPH_PLAN_AUTHORITY_MISMATCH", "cutover plan must bind authority store"),
    ("prepared-plan", "prepared", "/cutover/plan/plan_digest", "plan", "/plan_digest", "GRAPH_PREPARED_PLAN_MISMATCH", "prepared marker must bind cutover plan"),
    ("receipt-plan", "receipt", "/plan_digest", "plan", "/plan_digest", "GRAPH_RECEIPT_PLAN_MISMATCH", "receipt must bind cutover plan"),
    ("receipt-prepared", "receipt", "/prepared_marker_digest", "prepared", "/prepared_marker_digest", "GRAPH_RECEIPT_PREPARED_MISMATCH", "receipt must bind prepared marker"),
    ("terminal-receipt", "terminal", "/receipt/receipt_digest", "receipt", "/receipt_digest", "GRAPH_TERMINAL_RECEIPT_MISMATCH", "terminal marker must bind receipt"),
    ("readback-profile", "readback", "/profile_digest", "profile", "/profile_digest", "GRAPH_READBACK_PROFILE_MISMATCH", "active readback must bind profile"),
    ("readback-authority", "readback", "/activation_authority_digest", "authority", "/authority_digest", "GRAPH_READBACK_AUTHORITY_MISMATCH", "active readback must bind activation authority"),
    ("readback-grants", "readback", "/grant_set_digest", "grants", "/grant_set_digest", "GRAPH_READBACK_GRANT_MISMATCH", "active readback must bind grant set"),
    ("admission-alias", "admission", "/prime_alias_digest", "alias", "/alias_digest", "GRAPH_ADMISSION_ALIAS_MISMATCH", "admission must bind alias attestation"),
    ("admission-aar-classification", "admission", "/aar_classification_digest", "aarClassification", "/classification_digest", "GRAPH_ADMISSION_AAR_MISMATCH", "admission must bind AAR classification"),
    ("admission-prime-classification", "admission", "/prime_classification_digest", "primeClassification", "/classification_digest", "GRAPH_ADMISSION_PRIME_MISMATCH", "admission must bind Prime classification"),
)
_GRAPH_ROLES = {
    "adapter": "valid/method-adapter.json",
    "intent": "valid/activation-intent.json",
    "profile": "valid/final-profile.json",
    "authority": "valid/activation-generation-authority-first.json",
    "plan": "valid/cutover-plan.json",
    "prepared": "valid/operator-prepared-cutover.json",
    "receipt": "valid/cutover-receipt-committed.json",
    "terminal": "valid/operator-terminal-cutover-committed.json",
    "readback": "valid/activation-readback-active.json",
    "grants": "valid/workbench-grant-set.json",
    "alias": "valid/provider-alias-attestation.json",
    "aarClassification": "valid/evaluation-classification-aar.json",
    "primeClassification": "valid/evaluation-classification-prime.json",
    "admission": "valid/paired-admission.json",
}


def _doc_for(documents: Mapping[str, Any], path: str) -> dict[str, Any]:
    value = documents.get(path)
    if value is None:
        value = documents.get(f"tests/fixtures/provider-ready/{path}")
    if value is None:
        raise KeyError(path)
    if isinstance(value, bytes):
        value = load_provider_ready_json_bytes(value)
    if not isinstance(value, dict):
        raise TypeError(f"fixture document must be an object: {path}")
    return value


def _graph_errors(
    scenario: str,
    documents: Mapping[str, Any],
    replacements: Mapping[str, str] | None = None,
    join_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    roles = dict(_GRAPH_ROLES)
    if scenario == "alternative":
        roles["adapter"] = "valid/final-profile.json"
        roles["intent"] = "valid/final-profile.json"
    roles.update(replacements or {})
    errors: list[dict[str, Any]] = []
    for name, left_role, left_pointer, right_role, right_pointer, code, fragment in _GRAPH_JOINS:
        if join_names is not None and name not in join_names:
            continue
        left_document = _doc_for(documents, roles[left_role])
        right_document = _doc_for(documents, roles[right_role])
        if scenario == "alternative" and left_role == "adapter":
            left_document = _pointer_get(left_document, "/intent/adapters/0")
        if scenario == "alternative" and left_role == "intent":
            left_document = _pointer_get(left_document, "/intent")
        if scenario == "alternative" and right_role == "adapter":
            right_document = _pointer_get(right_document, "/intent/adapters/0")
        if scenario == "alternative" and right_role == "intent":
            right_document = _pointer_get(right_document, "/intent")
        left = _pointer_get(left_document, left_pointer)
        right = _pointer_get(right_document, right_pointer)
        if left != right:
            errors.append({
                "engine": "graph", "code": code,
                "location": ["$graph", scenario, name],
                "fragment": fragment, "targetIndex": 0,
            })
    return errors


def _semantic_records(documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    alternative_intent = _intent(
        profile_id="hermes-service-luna-max-v2",
        activation_generation=2,
        mode="service_managed",
        previous_activation_authority_digest=BASE_DIGEST,
    )

    def add(
        *, path: str, base_path: str, pointer: str, operation: str, value: Any,
        mutated: dict[str, Any], redigest_chain: list[str], expected_fragment: str | None,
        expected_engine: str = "project", independent_expected: str = "pass",
        project_expected: str = "fail", graph_expected: str = "not_applicable",
        graph_scenario: str | None = None, graph_join_names: list[str] | None = None,
        graph_replacements: dict[str, str] | None = None, graph_dependencies: list[str] | None = None,
    ) -> None:
        project, project_error = _project_result(mutated)
        independent, independent_error = _independent_result(
            mutated, PROVIDER_READY_SCHEMA_MODELS[documents[base_path]["schema_version"]]
        )
        assert project == project_expected, (path, project, project_error)
        assert independent == independent_expected, (path, independent, independent_error)
        if project_expected == "fail":
            assert project_error is not None
            if expected_engine == "project":
                assert project_error["engine"] == "project"
                assert "digest" not in project_error["fragment"].lower()
            if expected_engine == "loader":
                assert project_error["engine"] == "loader"
            if expected_fragment is not None:
                assert expected_fragment in project_error["fragment"], (path, project_error)
            normalized = project_error
        else:
            assert project_error is None, (path, project_error)
            normalized = None
        graph_normalized = None
        if graph_scenario is not None:
            all_documents = dict(documents)
            all_documents[path] = mutated
            graph_errors = _graph_errors(
                graph_scenario,
                all_documents,
                graph_replacements,
                set(graph_join_names or ()),
            )
            assert graph_expected == ("fail" if graph_errors else "pass"), (path, graph_errors)
            if graph_expected == "fail":
                assert len(graph_errors) == 1, (path, graph_errors)
                graph_normalized = graph_errors[0]
        error = graph_normalized or normalized
        records.append({
            "path": path,
            "base_path": base_path,
            "schema_id": mutated.get("schema_version"),
            "kind": "semantic_invalid",
            "json_pointer": pointer,
            "operation": operation,
            "value": value,
            "redigest_chain": redigest_chain,
            "document": mutated,
            "document_digest": canonical_sha256(mutated),
            "project_expected": project_expected,
            "independent_expected": independent,
            "graph_expected": graph_expected,
            "composite_expected": "fail" if "fail" in {project_expected, independent, graph_expected} else "pass",
            "graph_scenario": graph_scenario,
            "graph_dependencies": graph_dependencies or [],
            "graph_replacements": graph_replacements or {},
            "expected_error": error,
            "raw_bytes": pretty_json_bytes(mutated),
        })

    unknown = deepcopy(documents["valid/method-adapter.json"])
    unknown["unexpected"] = True
    add(path="invalid/unknown-field.json", base_path="valid/method-adapter.json", pointer="/unexpected", operation="add", value=True, mutated=_redigest(unknown, "manifest_digest"), redigest_chain=["/manifest_digest"], expected_fragment="Extra inputs are not permitted", independent_expected="fail")

    wrong_schema = deepcopy(documents["valid/method-adapter.json"])
    wrong_schema["schema_version"] = "aar.provider-ready-unknown.v1"
    add(path="invalid/wrong-schema-version.json", base_path="valid/method-adapter.json", pointer="/schema_version", operation="replace", value="aar.provider-ready-unknown.v1", mutated=_redigest(wrong_schema, "manifest_digest"), redigest_chain=["/manifest_digest"], expected_fragment="SCHEMA_VERSION_UNKNOWN", expected_engine="loader", independent_expected="fail")

    strict_bool = deepcopy(documents["valid/method-adapter.json"])
    strict_bool["reference_only"] = "false"
    add(path="invalid/strict-bool.json", base_path="valid/method-adapter.json", pointer="/reference_only", operation="replace", value="false", mutated=_redigest(strict_bool, "manifest_digest"), redigest_chain=["/manifest_digest"], expected_fragment="valid boolean", independent_expected="fail")

    method_truth = deepcopy(documents["valid/method-adapter.json"])
    method_truth["backend_kind"] = "reference"
    add(path="invalid/method-adapter-reference-truth.json", base_path="valid/method-adapter.json", pointer="/backend_kind", operation="replace", value="reference", mutated=_redigest(method_truth, "manifest_digest"), redigest_chain=["/manifest_digest"], expected_fragment="reference backend requires reference_only=true")

    artifact_driver = deepcopy(documents["valid/activation-intent.json"])
    artifact_adapter = artifact_driver["adapters"][4]
    artifact_adapter["backend_kind"] = "caller_driver"
    artifact_driver["adapters"][4] = _redigest(artifact_adapter, "manifest_digest")
    add(path="invalid/activation-intent-artifact-put-caller-driver.json", base_path="valid/activation-intent.json", pointer="/adapters/4/backend_kind", operation="replace", value="caller_driver", mutated=_redigest(artifact_driver, "intent_digest"), redigest_chain=["/adapters/4/manifest_digest", "/intent_digest"], expected_fragment="artifact.put cannot use caller_driver backend")

    profile_mismatch = deepcopy(documents["valid/final-profile.json"])
    profile_mismatch["intent"] = _dump(alternative_intent)
    add(path="invalid/profile-intent-digest-mismatch.json", base_path="valid/final-profile.json", pointer="/intent", operation="replace", value=_dump(alternative_intent), mutated=_redigest(profile_mismatch, "profile_digest"), redigest_chain=["/intent/intent_digest", "/profile_digest"], expected_fragment=None, project_expected="pass", graph_expected="fail", graph_scenario="primary", graph_join_names=["intent-profile-digest"], graph_replacements={"profile": "invalid/profile-intent-digest-mismatch.json", "intent": "valid/activation-intent.json"}, graph_dependencies=["valid/activation-intent.json", "invalid/profile-intent-digest-mismatch.json"])

    generation_mismatch = deepcopy(documents["valid/activation-generation-authority-first.json"])
    generation_mismatch["activation_generation"] = 2
    add(path="invalid/authority-generation-prior-mismatch.json", base_path="valid/activation-generation-authority-first.json", pointer="/activation_generation", operation="replace", value=2, mutated=_redigest(generation_mismatch, "authority_digest"), redigest_chain=["/authority_digest"], expected_fragment=None, project_expected="pass", graph_expected="fail", graph_scenario="primary", graph_join_names=["authority-generation"], graph_replacements={"authority": "invalid/authority-generation-prior-mismatch.json", "intent": "valid/activation-intent.json"}, graph_dependencies=["valid/activation-intent.json", "invalid/authority-generation-prior-mismatch.json"])

    plan_owner = deepcopy(documents["valid/cutover-plan.json"])
    plan_owner["owner"]["authority_store_id"] = "local-file-authority-v1:other"
    add(path="invalid/plan-owner-authority-mismatch.json", base_path="valid/cutover-plan.json", pointer="/owner/authority_store_id", operation="replace", value="local-file-authority-v1:other", mutated=_redigest(plan_owner, "plan_digest"), redigest_chain=["/plan_digest"], expected_fragment="plan authority_store_id")

    prepared_union = deepcopy(documents["valid/operator-prepared-cutover.json"])
    prepared_union["kind"] = "restore"
    add(path="invalid/prepared-union-mismatch.json", base_path="valid/operator-prepared-cutover.json", pointer="/kind", operation="replace", value="restore", mutated=_redigest(prepared_union, "prepared_marker_digest"), redigest_chain=["/prepared_marker_digest"], expected_fragment="restore prepared marker requires restore only")

    cutover_mapping = deepcopy(documents["valid/cutover-receipt-aborted.json"])
    cutover_mapping["outcome"] = "committed"
    add(path="invalid/cutover-outcome-mapping.json", base_path="valid/cutover-receipt-aborted.json", pointer="/outcome", operation="replace", value="committed", mutated=_redigest(cutover_mapping, "receipt_digest"), redigest_chain=["/receipt_digest"], expected_fragment="committed receipt requires database_commit_state=committed")

    frontier = deepcopy(documents["valid/restore-receipt-committed.json"])
    frontier["frontier_proof"]["no_committed_v6_attestation"] = False
    add(path="invalid/restore-frontier-proof-false.json", base_path="valid/restore-receipt-committed.json", pointer="/frontier_proof/no_committed_v6_attestation", operation="replace", value=False, mutated=_redigest(frontier, "receipt_digest"), redigest_chain=["/receipt_digest"], expected_fragment="Input should be True", independent_expected="fail")

    terminal_swap = deepcopy(documents["valid/operator-terminal-cutover-committed.json"])
    terminal_swap["kind"] = "cutover_aborted"
    add(path="invalid/terminal-receipt-kind-swap.json", base_path="valid/operator-terminal-cutover-committed.json", pointer="/kind", operation="replace", value="cutover_aborted", mutated=_redigest(terminal_swap, "marker_digest"), redigest_chain=["/marker_digest"], expected_fragment="terminal kind does not match receipt outcome")

    readback_null = deepcopy(documents["valid/activation-readback-active.json"])
    readback_null["runtime_generation"] = None
    add(path="invalid/readback-active-null-binding.json", base_path="valid/activation-readback-active.json", pointer="/runtime_generation", operation="replace", value=None, mutated=_redigest(readback_null, "readback_digest"), redigest_chain=["/readback_digest"], expected_fragment="active state requires runtime_generation to be non-null")

    grant_unsorted = deepcopy(documents["valid/workbench-grant-set.json"])
    grant_unsorted["principal_ids"] = ["z-runner", "a-runner"]
    add(path="invalid/grant-set-unsorted-principals.json", base_path="valid/workbench-grant-set.json", pointer="/principal_ids", operation="replace", value=["z-runner", "a-runner"], mutated=_redigest(grant_unsorted, "grant_set_digest"), redigest_chain=["/grant_set_digest"], expected_fragment="principal_ids must be sorted")

    alias_tamper = deepcopy(documents["valid/provider-alias-attestation.json"])
    alias_tamper["model"] = "gpt-5.6-luna-legacy"
    add(path="invalid/alias-constant-tamper.json", base_path="valid/provider-alias-attestation.json", pointer="/model", operation="replace", value="gpt-5.6-luna-legacy", mutated=_redigest(alias_tamper, "alias_digest"), redigest_chain=["/alias_digest"], expected_fragment="gpt-5.6-luna", independent_expected="fail")

    evaluation_mismatch = deepcopy(documents["valid/evaluation-classification-aar.json"])
    evaluation_mismatch["effective_model"] = "gpt-5.5-luna"
    add(path="invalid/evaluation-tier-effective-mismatch.json", base_path="valid/evaluation-classification-aar.json", pointer="/effective_model", operation="replace", value="gpt-5.5-luna", mutated=_redigest(evaluation_mismatch, "classification_digest"), redigest_chain=["/classification_digest"], expected_fragment="model_tier=attested")

    expiry = deepcopy(documents["valid/paired-admission.json"])
    expiry["expires_at_unix_ms"] = 900_101
    add(path="invalid/paired-expiry-overflow.json", base_path="valid/paired-admission.json", pointer="/expires_at_unix_ms", operation="replace", value=900_101, mutated=_redigest(expiry, "admission_digest"), redigest_chain=["/admission_digest"], expected_fragment="admission expiry cannot exceed creation by 900000 ms")
    assert len(records) == 17
    return records


def _loader_records() -> list[dict[str, Any]]:
    cases = (
        ("invalid/raw-duplicate-root.json", b'{"schema_version":"aar.method-adapter-manifest.v1","schema_version":"aar.method-adapter-manifest.v1"}', "DUPLICATE_KEY"),
        ("invalid/raw-duplicate-nested.json", b'{"schema_version":"aar.method-adapter-manifest.v1","nested":{"same":1,"same":1}}', "DUPLICATE_KEY"),
        ("invalid/raw-float.json", b'{"schema_version":"unknown","value":1.5}', "FLOAT_NOT_ALLOWED"),
        ("invalid/raw-nonfinite.json", b'{"schema_version":"unknown","value":NaN}', "NONFINITE_NOT_ALLOWED"),
        ("invalid/raw-bom.json", b"\xef\xbb\xbf{}", "UTF8_BOM"),
        ("invalid/raw-invalid-utf8.json", b'{"value":"\xff"}', "UTF8_DECODE"),
        ("invalid/raw-trailing-data.json", b"{} {}", "JSON_TRAILING_DATA"),
        ("invalid/raw-nonobject.json", b"[]", "NON_OBJECT_ROOT"),
    )
    records = []
    for path, raw, code in cases:
        try:
            load_provider_ready_json_bytes(raw)
        except ProviderReadyContractError as error:
            assert error.code == code
            normalized = {"engine": "loader", "code": error.code, "location": None, "fragment": str(error), "targetIndex": None}
        else:
            raise AssertionError(f"loader fixture unexpectedly passed: {path}")
        records.append({
            "path": path, "kind": "loader_invalid", "schema_id": None,
            "project_expected": "fail", "independent_expected": "not_applicable",
            "graph_expected": "not_applicable", "composite_expected": "fail",
            "graph_scenario": None, "graph_dependencies": [], "graph_replacements": {},
            "expected_error": normalized, "document_digest": None,
            "raw_bytes": raw, "raw_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        })
    return records


def _record_entry(record: Mapping[str, Any]) -> dict[str, Any]:
    error = record.get("expected_error")
    return {
        "path": record["path"],
        "kind": record["kind"],
        "schema_id": record.get("schema_id"),
        "project_expected": record["project_expected"],
        "independent_expected": record["independent_expected"],
        "graph_expected": record["graph_expected"],
        "composite_expected": record["composite_expected"],
        "graph_scenario": record.get("graph_scenario"),
        "graph_dependencies": list(record.get("graph_dependencies", [])),
        "graph_replacements": dict(record.get("graph_replacements", {})),
        "expected_error_engine": error["engine"] if error else None,
        "expected_error_code": error["code"] if error else None,
        "expected_error_location": error["location"] if error else None,
        "expected_error_fragment": error["fragment"] if error else None,
        "document_digest": record.get("document_digest"),
        "raw_bytes_sha256": record["raw_bytes_sha256"] if "raw_bytes_sha256" in record else hashlib.sha256(record["raw_bytes"]).hexdigest(),
    }


def _all_records(documents: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    records: list[dict[str, Any]] = []
    for path in VALID_PATHS:
        document = documents[path]
        raw = pretty_json_bytes(document)
        records.append({
            "path": path, "kind": "valid", "schema_id": document["schema_version"],
            "project_expected": "pass", "independent_expected": "pass", "graph_expected": "not_applicable",
            "composite_expected": "pass", "graph_scenario": None, "graph_dependencies": [], "graph_replacements": {},
            "expected_error": None, "document": document, "document_digest": canonical_sha256(document), "raw_bytes": raw,
            "raw_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        })
    for path, base_path, digest_field in STALE_BASES:
        stale = deepcopy(documents[base_path])
        stale[digest_field] = canonical_sha256({"staleRoot": path})
        status, error = _project_result(stale)
        independent, _ = _independent_result(stale, PROVIDER_READY_SCHEMA_MODELS[stale["schema_version"]])
        assert status == "fail" and error is not None and independent == "pass"
        raw = pretty_json_bytes(stale)
        records.append({
            "path": path, "kind": "digest_invalid", "schema_id": stale["schema_version"],
            "project_expected": "fail", "independent_expected": "pass", "graph_expected": "not_applicable",
            "composite_expected": "fail", "graph_scenario": None, "graph_dependencies": [], "graph_replacements": {},
            "expected_error": error, "document": stale, "document_digest": canonical_sha256(stale), "raw_bytes": raw,
            "raw_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        })
    semantic = _semantic_records(documents)
    records.extend(semantic)
    loader = _loader_records()
    records.extend(loader)
    records.sort(key=lambda record: record["path"])
    raw_documents = {record["path"]: record["raw_bytes"] for record in records}
    assert len(records) == 64
    return records, raw_documents


def provider_ready_schema_bundle() -> dict[str, Any]:
    schemas = {
        schema_id: model_type.model_json_schema(mode="validation")
        for schema_id, model_type in sorted(PROVIDER_READY_SCHEMA_MODELS.items())
    }
    schema_digests = {schema_id: canonical_sha256(schema) for schema_id, schema in schemas.items()}
    core = {
        "schema_version": SCHEMA_BUNDLE_SCHEMA_VERSION,
        "schemas": schemas,
        "schema_digests": schema_digests,
    }
    return {**core, "bundle_digest": canonical_sha256(core)}


def provider_ready_fixture_documents() -> dict[str, dict[str, Any]]:
    """Return every parsed valid and semantic/digest-invalid fixture document."""
    documents = _build_valid_documents()
    records, _ = _all_records(documents)
    return {
        record["path"]: deepcopy(record["document"])
        for record in records
        if record["kind"] != "loader_invalid"
        for _ in ({"document": record.get("document")} if record.get("document") is not None else ())
    }


def _manifest_and_assets() -> tuple[dict[str, Any], dict[str, bytes]]:
    documents = _build_valid_documents()
    records, raw_documents = _all_records(documents)
    bundle = provider_ready_schema_bundle()
    entries = [_record_entry(record) for record in records]
    manifest_core = {
        "schema_version": FIXTURE_MANIFEST_SCHEMA_VERSION,
        "schema_bundle_digest": bundle["bundle_digest"],
        "fixtures": entries,
    }
    manifest = {**manifest_core, "fixture_set_digest": canonical_sha256(manifest_core)}
    assets: dict[str, bytes] = {
        "schemas/aar-provider-ready-schemas-v1.json": pretty_json_bytes(bundle),
        "tests/fixtures/provider-ready/manifest.json": pretty_json_bytes(manifest),
    }
    assets.update({f"tests/fixtures/provider-ready/{path}": raw for path, raw in raw_documents.items()})
    return manifest, assets


def provider_ready_asset_bytes() -> dict[str, bytes]:
    """Return the exact schema and provider-ready subtree bytes without writing."""
    _, assets = _manifest_and_assets()
    return dict(sorted(assets.items()))


def _relative_path(path: str) -> tuple[str, ...]:
    if not path or "\\" in path:
        raise ValueError(f"unsafe relative path: {path!r}")
    candidate = Path(path)
    if candidate.is_absolute() or ".." in candidate.parts or "." in candidate.parts:
        raise ValueError(f"unsafe relative path: {path!r}")
    return candidate.parts


def _casefold_collision(parent: Path, name: str) -> Path | None:
    if not parent.exists():
        return None
    folded = name.casefold()
    for child in parent.iterdir():
        if child.name.casefold() == folded and child.name != name:
            return child
    return None


def _preflight(root: Path, assets: Mapping[str, bytes]) -> None:
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise ValueError("root must be an existing non-symlink directory")
    targets = (Path("schemas/aar-provider-ready-schemas-v1.json"), Path("tests/fixtures/provider-ready"))
    for target in targets:
        current = root
        for part in target.parts[:-1]:
            alias = _casefold_collision(current, part)
            if alias is not None:
                raise ValueError(f"case-fold path alias is not allowed: {alias}")
            current = current / part
            if current.is_symlink() or (current.exists() and not current.is_dir()):
                raise ValueError(f"non-directory or symlink ancestor: {current}")
        alias = _casefold_collision(current, target.parts[-1])
        if alias is not None:
            raise ValueError(f"case-fold path alias is not allowed: {alias}")
        final = current / target.parts[-1]
        if final.exists() or final.is_symlink():
            raise ValueError(f"generation target already exists: {final}")
    expected = {_relative_path(path) for path in assets}
    if any(path[:2] == ("tests", "fixtures") and path[2] != "provider-ready" for path in expected):
        raise ValueError("asset escapes provider-ready subtree")


def _remove_owned(path: Path) -> None:
    if path.is_symlink() or not path.exists():
        if path.is_symlink():
            path.unlink()
        return
    if path.is_dir():
        for child in path.iterdir():
            _remove_owned(child)
        path.rmdir()
    else:
        path.unlink()


def _ensure_parent(path: Path, root: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    current = path
    while current != root and not current.exists():
        missing.append(current)
        current = current.parent
    if current.is_symlink() or not current.is_dir():
        raise ValueError(f"unsafe parent: {current}")
    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def generate_provider_ready_contract(root: str | Path) -> None:
    """Install the two absent provider-ready targets using contained staging."""
    root_path = Path(root)
    assets = provider_ready_asset_bytes()
    _preflight(root_path, assets)
    stage: Path | None = None
    created_dirs: list[Path] = []
    installed: list[Path] = []
    try:
        stage = Path(tempfile.mkdtemp(prefix=".provider-ready-stage-", dir=root_path))
        stage_schema = stage / "schema.json"
        stage_fixture = stage / "provider-ready"
        stage_fixture.mkdir()
        for relative, raw in assets.items():
            if relative.startswith("schemas/"):
                destination = stage_schema
            else:
                destination = stage_fixture / Path(relative).relative_to("tests/fixtures/provider-ready")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        schema_target = root_path / "schemas/aar-provider-ready-schemas-v1.json"
        fixture_target = root_path / "tests/fixtures/provider-ready"
        _ensure_parent(schema_target.parent, root_path, created_dirs)
        _ensure_parent(fixture_target.parent, root_path, created_dirs)
        stage_schema.rename(schema_target)
        installed.append(schema_target)
        stage_fixture.rename(fixture_target)
        installed.append(fixture_target)
    except Exception:
        for target in reversed(installed):
            _remove_owned(target)
        for directory in reversed(created_dirs):
            if directory.exists() and not directory.is_symlink():
                with suppress(OSError):
                    directory.rmdir()
        raise
    finally:
        if stage is not None and stage.exists():
            _remove_owned(stage)


def _actual_asset_bytes(root: Path) -> dict[str, bytes]:
    actual: dict[str, bytes] = {}
    schema = root / "schemas/aar-provider-ready-schemas-v1.json"
    fixture = root / "tests/fixtures/provider-ready"
    if schema.is_symlink() or not schema.is_file():
        raise ValueError(f"missing or unsafe schema bundle: {schema}")
    if fixture.is_symlink() or not fixture.is_dir():
        raise ValueError(f"missing or unsafe fixture subtree: {fixture}")
    actual["schemas/aar-provider-ready-schemas-v1.json"] = schema.read_bytes()
    directories: set[str] = set()
    for path in fixture.rglob("*"):
        relative = path.relative_to(fixture)
        if path.is_symlink():
            raise ValueError(f"unsafe symlink fixture entry: {path}")
        if path.is_dir():
            directories.add(relative.as_posix())
            continue
        if not path.is_file():
            raise ValueError(f"unsafe or non-file fixture entry: {path}")
        actual[f"tests/fixtures/provider-ready/{relative.as_posix()}"] = path.read_bytes()
    if directories != {"valid", "invalid"}:
        raise ValueError("provider-ready fixture directories are not exact")
    return actual


def _assert_exact_assets(root: Path) -> dict[str, bytes]:
    expected = provider_ready_asset_bytes()
    actual = _actual_asset_bytes(root)
    if set(actual) != set(expected):
        raise ValueError("provider-ready asset file set is not exact")
    for path, raw in expected.items():
        if actual[path] != raw:
            raise ValueError(f"provider-ready asset bytes differ: {path}")
    return actual


def verify_provider_ready_assets(root: str | Path) -> bool:
    """Compare exact generated bytes and file set without writing."""
    _assert_exact_assets(Path(root))
    return True


def _validate_manifest(root: Path, actual: Mapping[str, bytes]) -> None:
    bundle = json.loads(actual["schemas/aar-provider-ready-schemas-v1.json"])
    if bundle != provider_ready_schema_bundle():
        raise ValueError("schema bundle object differs from pure projection")
    for schema_id, schema in bundle["schemas"].items():
        Draft202012Validator.check_schema(schema)
        if bundle["schema_digests"][schema_id] != canonical_sha256(schema):
            raise ValueError(f"schema digest mismatch: {schema_id}")
    bundle_core = {key: bundle[key] for key in ("schema_version", "schemas", "schema_digests")}
    if bundle["bundle_digest"] != canonical_sha256(bundle_core):
        raise ValueError("schema bundle digest mismatch")
    manifest = json.loads(actual["tests/fixtures/provider-ready/manifest.json"])
    core = {key: manifest[key] for key in ("schema_version", "schema_bundle_digest", "fixtures")}
    if manifest["fixture_set_digest"] != canonical_sha256(core):
        raise ValueError("fixture manifest self digest mismatch")
    if manifest["schema_bundle_digest"] != bundle["bundle_digest"]:
        raise ValueError("fixture manifest/schema bundle join mismatch")
    paths = [entry["path"] for entry in manifest["fixtures"]]
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise ValueError("fixture manifest paths must be sorted and unique")
    documents: dict[str, dict[str, Any]] = {
        entry["path"]: load_provider_ready_json_bytes(
            actual[f"tests/fixtures/provider-ready/{entry['path']}"]
        )
        for entry in manifest["fixtures"]
        if entry["kind"] != "loader_invalid"
    }
    for entry in manifest["fixtures"]:
        path = entry["path"]
        raw = actual[f"tests/fixtures/provider-ready/{path}"]
        if hashlib.sha256(raw).hexdigest() != entry["raw_bytes_sha256"]:
            raise ValueError(f"raw fixture hash mismatch: {path}")
        if entry["kind"] == "loader_invalid":
            try:
                load_provider_ready_json_bytes(raw)
            except ProviderReadyContractError as error:
                if entry["expected_error_code"] != error.code:
                    raise ValueError(f"loader fixture code mismatch: {path}") from error
            else:
                raise ValueError(f"loader fixture unexpectedly passed: {path}")
            continue
        payload = load_provider_ready_json_bytes(raw)
        documents[path] = payload
        if entry["document_digest"] != canonical_sha256(payload):
            raise ValueError(f"document digest mismatch: {path}")
        observed: dict[str, Any] | None = None
        try:
            validate_provider_ready_document_bytes(raw, expected_schema_id=entry["schema_id"])
        except ProviderReadyContractError as error:
            observed = {"engine": "loader", "code": error.code, "location": None, "fragment": str(error)}
        except ValidationError as error:
            target = error.errors()[0]
            observed = {
                "engine": "project", "code": target["type"], "location": list(target["loc"]),
                "fragment": target["msg"],
            }
        expected_project = entry["project_expected"]
        if (observed is None) != (expected_project == "pass"):
            raise ValueError(f"project outcome mismatch: {path}")
        if observed is not None:
            if observed["engine"] != entry["expected_error_engine"]:
                raise ValueError(f"project error engine mismatch: {path}")
            if observed["code"] != entry["expected_error_code"]:
                raise ValueError(f"project error code mismatch: {path}")
            if observed["location"] != entry["expected_error_location"]:
                raise ValueError(f"project error location mismatch: {path}")
            if entry["expected_error_fragment"] not in observed["fragment"]:
                raise ValueError(f"project error fragment mismatch: {path}")
        model_type = PROVIDER_READY_SCHEMA_MODELS.get(entry["schema_id"])
        if model_type is None:
            if path != "invalid/wrong-schema-version.json":
                raise ValueError(f"unknown schema id without a base projection: {path}")
            model_type = MethodAdapterManifest
        independent, _ = _independent_result(payload, model_type)
        if independent != entry["independent_expected"]:
            raise ValueError(f"independent outcome mismatch: {path}")
        if entry["graph_scenario"] is not None:
            graph_results = _graph_errors(
                entry["graph_scenario"],
                documents,
                entry["graph_replacements"],
                {entry["expected_error_location"][2]}
                if entry["graph_expected"] == "fail" and entry["expected_error_location"]
                else None,
            )
            expected_graph = entry["graph_expected"]
            if (not graph_results) != (expected_graph != "fail"):
                raise ValueError(f"graph outcome mismatch: {path}")
            if expected_graph == "fail":
                expected_location = entry["expected_error_location"]
                if len(graph_results) != 1 or graph_results[0]["location"] != expected_location:
                    raise ValueError(f"graph error mismatch: {path}")
    if _graph_errors("primary", documents) or _graph_errors("alternative", documents):
        raise ValueError("valid provider-ready graph does not validate")


def validate_provider_ready_contract(root: str | Path) -> bool:
    """Validate exact files, schemas, project documents, manifest, and graph."""
    root_path = Path(root)
    actual = _assert_exact_assets(root_path)
    _validate_manifest(root_path, actual)
    return True


def validate_provider_ready_fixture_graph(documents: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return normalized errors from the two explicit pure equality graph projections."""
    primary = _graph_errors("primary", documents)
    alternative = _graph_errors("alternative", documents)
    return primary + alternative


__all__ = [
    "generate_provider_ready_contract",
    "provider_ready_asset_bytes",
    "provider_ready_fixture_documents",
    "provider_ready_schema_bundle",
    "validate_provider_ready_contract",
    "validate_provider_ready_fixture_graph",
    "verify_provider_ready_assets",
]
