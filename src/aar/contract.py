"""Generate and verify the canonical AR-0A schema and fixture package."""

from __future__ import annotations

import argparse
import ast
import copy
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from aar.asset_models import (
    ASSET_SCHEMA_MODELS,
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    AgentFingerprint,
    Episode,
)
from aar.broker_models import (
    BROKER_SCHEMA_MODELS,
    BrokerCatalog,
    BrokerMethodSummary,
    BrokerUsage,
    EffectiveModelRoute,
    ModelResponse,
    ModelRouteBinding,
    ModelRouteCatalog,
    ModelRouteProfile,
    ModelRouteReceipt,
    ModelUsageRecord,
)
from aar.canonical import canonical_json_bytes, canonical_sha256, pretty_json_bytes
from aar.continuity_models import (
    CONTINUITY_SCHEMA_MODELS,
    OperationAttemptRecordV1,
    OperationAttemptRefV1,
    OperationCheckpointBindingV1,
    OperationContinuitySnapshotV1,
    OperationControlStateV1,
    OperationEventEnvelopeV1,
    OperationEventPageV1,
    OperationLeaseRecordV1,
    OperationRecoveryDecisionV1,
    OperationRecoveryPolicyBindingV1,
    OperationRecoveryPolicyV1,
    OperationRlmStepBoundaryV1,
)
from aar.rlm_models import RLM_SCHEMA_MODELS, RlmJobSpec
from aar.schemas import (
    SCHEMA_MODELS,
    AccessMode,
    ArtifactIdRef,
    ArtifactReference,
    Budget,
    CapabilityDescriptor,
    CapabilityLimit,
    CapabilitySet,
    FailureCategory,
    FailureDetail,
    FailureEnvelope,
    Grant,
    GrantConstraint,
    HostRef,
    LaneRef,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PrincipalRef,
    ReconciliationReport,
    RequestEnvelope,
    SecurityBoundary,
    SessionRef,
    WorkspaceRef,
)
from aar.versions import FROZEN_COMPATIBILITY_PACKAGE_VERSION, SCHEMA_VERSIONS

FORBIDDEN_IMPORT_ROOTS = frozenset({"ahc", "codex", "hermes"})
CONTRACT_MODELS = {
    **SCHEMA_MODELS,
    **BROKER_SCHEMA_MODELS,
    **RLM_SCHEMA_MODELS,
    **ASSET_SCHEMA_MODELS,
    **CONTINUITY_SCHEMA_MODELS,
}


def schema_bundle() -> dict[str, Any]:
    schemas = {
        name: model.model_json_schema(mode="validation")
        for name, model in sorted(CONTRACT_MODELS.items())
    }
    core = {
        "package_version": FROZEN_COMPATIBILITY_PACKAGE_VERSION,
        "schema_versions": SCHEMA_VERSIONS,
        "schemas": schemas,
    }
    return {**core, "bundle_digest": canonical_sha256(core)}


def _valid_documents() -> dict[str, tuple[str, dict[str, Any]]]:
    principal = PrincipalRef(value="principal-demo")
    operation = OperationRef(value="operation-demo")
    capabilities = CapabilitySet.issue(
        (
            CapabilityDescriptor(name="artifact.read", access=AccessMode.READ),
            CapabilityDescriptor(
                name="workspace.execute",
                access=AccessMode.WRITE,
                limits=(CapabilityLimit(name="output_bytes", value=65_536),),
            ),
        )
    )
    grant = Grant(
        grant_id="grant-workspace-execute",
        capability="workspace.execute",
        issued_to=principal,
        expires_at_unix_ms=4_102_444_800_000,
        constraints=(GrantConstraint(name="network", value=False),),
    )
    envelope = RequestEnvelope(
        request_id="request-demo",
        idempotency_key="idem-demo-0001",
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="execute"),
        session=SessionRef(value="session-demo"),
        workspace=WorkspaceRef(value="workspace-demo"),
        runtime_generation=1,
        workspace_generation=1,
        expected_workspace_revision=0,
        capability_digest=capabilities.digest,
        deadline_unix_ms=4_102_444_790_000,
        grants=(grant,),
        budget=Budget(
            wall_time_ms=30_000,
            model_requests=2,
            input_tokens=8_192,
            output_tokens=4_096,
            child_operations=1,
            artifact_bytes=1_048_576,
        ),
        trace_id="trace-demo",
        input_digest=canonical_sha256({"code": "answer = 42"}),
    )
    failure = FailureEnvelope(
        category=FailureCategory.STALE,
        code="STALE_WORKSPACE_GENERATION",
        message="workspace generation does not match",
        retryable=False,
        certainty=OutcomeCertainty.CERTAIN,
        operation=operation,
        details=(FailureDetail(name="observed_generation", value=2),),
    )
    reconcile = ReconciliationReport(
        operation=operation,
        state=OperationState.INDETERMINATE,
        certainty=OutcomeCertainty.INDETERMINATE,
        runtime_generation=1,
        workspace_generation=1,
        observed_revision=0,
        reconciliation_required=True,
    )
    artifact = ArtifactReference(
        artifact=ArtifactIdRef(value="artifact-demo"),
        digest=canonical_sha256({"answer": 42}),
        media_type="application/json",
        size_bytes=13,
        created_by=operation,
        redacted=False,
    )
    boundary = SecurityBoundary(
        runtime_responsibilities=(
            "artifact.produce",
            "operation.reconcile",
            "workspace.execute",
        ),
        host_authority=(
            "admission.decide",
            "effect.authorize",
            "final.deliver",
        ),
        forbidden_in_runtime=(
            "credential.provider",
            "effect.direct",
            "final.deliver",
        ),
    )
    broker_catalog = BrokerCatalog(
        methods=(
            BrokerMethodSummary(
                name="evidence.query",
                capability="evidence.query",
                access=AccessMode.READ,
                description="Query host-owned evidence without provider credentials.",
            ),
            BrokerMethodSummary(
                name="model.request",
                capability="model.request",
                access=AccessMode.WRITE,
                description="Request a model result through the host broker.",
            ),
        )
    )
    model_route_profile = ModelRouteProfile(
        profile_id="reference-fake-v1",
        provider_driver="reference-fake-driver-v1",
        provider="reference",
        model="deterministic-reference",
        max_output_tokens=256,
    )
    model_route_catalog = ModelRouteCatalog.issue((model_route_profile,))
    model_route_binding = ModelRouteBinding.issue(
        model_route_catalog,
        model_route_profile,
    )
    effective_model_route = EffectiveModelRoute(
        provider_driver=model_route_binding.provider_driver,
        provider=model_route_binding.provider,
        model=model_route_binding.model,
        reasoning_effort=model_route_binding.reasoning_effort,
    )
    model_route_receipt = ModelRouteReceipt.issue(
        requested=model_route_binding,
        effective=effective_model_route,
        finish_reason="stop",
        provider_response_id="reference-response-demo",
    )
    model_usage = ModelUsageRecord(
        accounting_source="reference",
        input_tokens=4,
        output_tokens=2,
        total_tokens=6,
    )
    model_response = ModelResponse(
        output_text="deterministic:demo",
        route_receipt=model_route_receipt,
        usage=model_usage,
    )
    rlm_job = RlmJobSpec(
        query="Summarize the retained evidence.",
        strategy="evidence_synthesis",
        max_steps=2,
    )
    fingerprint = AdaptiveAssetDocument.issue(
        AgentFingerprint(
            runtime_digest=canonical_sha256({"runtime": "portable-reference"}),
            capability_digest=capabilities.digest,
            policy_digest=canonical_sha256({"policy": "deny-by-default"}),
        )
    )
    episode = AdaptiveAssetDocument.issue(
        Episode(
            operation=operation,
            agent=fingerprint.manifest.asset,
            request_digest=envelope.input_digest,
        )
    )
    asset_bundle = AdaptiveAssetBundle.issue(documents=(fingerprint, episode))
    attempt = OperationAttemptRefV1(
        operation=operation,
        attempt_no=1,
        attempt_id="attempt-demo-1",
    )
    attempt_record = OperationAttemptRecordV1(
        ref=attempt,
        runtime_generation=1,
        dispatcher_generation=1,
        state="running",
        certainty=OutcomeCertainty.CERTAIN,
        created_at_unix_ms=4_102_444_700_000,
        started_at_unix_ms=4_102_444_700_100,
    )
    lease = OperationLeaseRecordV1(
        attempt=attempt,
        runtime_generation=1,
        dispatcher_generation=1,
        lease_epoch=1,
        owner_digest=canonical_sha256({"owner": "reference-dispatcher"}),
        acquired_at_unix_ms=4_102_444_700_100,
        heartbeat_at_unix_ms=4_102_444_700_200,
        expires_at_unix_ms=4_102_444_730_200,
    )
    control = OperationControlStateV1(
        operation=operation,
        control_revision=0,
        cancellation_requested=False,
    )
    policy = OperationRecoveryPolicyV1(
        policy_version=1,
        policy_id="rlm.step-boundary.v1",
        operation_kind="rlm.execute",
        allowed_decisions=(
            "needs_user",
            "quarantine",
            "reconcile_effect",
            "start_successor",
            "terminal_from_receipt",
        ),
        max_successor_attempts=3,
        safe_replay_no_effect=False,
        checkpoint_required=False,
        effect_reconcile_required=True,
    )
    policy_binding = OperationRecoveryPolicyBindingV1.issue(
        operation=operation,
        operation_kind="rlm.execute",
        policy=policy,
        environment_digest=canonical_sha256(
            {"environment": "portable-reference", "strategy": rlm_job.strategy}
        ),
        created_at_unix_ms=4_102_444_700_250,
    )
    rlm_boundary = OperationRlmStepBoundaryV1.issue(
        operation=operation,
        prior_attempt=attempt,
        runtime_generation=1,
        dispatcher_generation=1,
        lease_epoch=1,
        policy_digest=policy_binding.policy_digest,
        input_digest=envelope.input_digest,
        strategy=rlm_job.strategy,
        strategy_digest=canonical_sha256(rlm_job),
        capability_digest=envelope.capability_digest,
        environment_digest=policy_binding.environment_digest,
        deadline_unix_ms=envelope.deadline_unix_ms,
        committed_step_count=0,
        last_step_digest=None,
        steps_digest=canonical_sha256(()),
        broker_trace_digest=canonical_sha256(()),
        usage_digest=canonical_sha256(BrokerUsage()),
        created_at_unix_ms=4_102_444_700_275,
    )
    checkpoint = OperationCheckpointBindingV1(
        operation=operation,
        attempt=attempt,
        checkpoint=artifact,
        environment_digest=canonical_sha256({"environment": "portable-reference"}),
        created_at_unix_ms=4_102_444_700_300,
    )
    decision = OperationRecoveryDecisionV1(
        operation=operation,
        decision_no=1,
        policy_version=1,
        prior_attempt=attempt,
        decision="needs_user",
        reason_code="effect_receipt_missing",
        input_digest=envelope.input_digest,
        policy_digest=policy_binding.policy_digest,
        created_at_unix_ms=4_102_444_700_400,
    )
    event_payload = {"kind": "execution_started", "attempt_no": 1}
    continuity_event = OperationEventEnvelopeV1(
        sequence=1,
        operation=operation,
        attempt=attempt,
        event_kind="execution_started",
        state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=1,
        at_unix_ms=4_102_444_700_100,
        payload=event_payload,
        payload_digest=canonical_sha256(event_payload),
    )
    continuity_snapshot = OperationContinuitySnapshotV1(
        operation=operation,
        operation_state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=1,
        current_attempt=attempt,
        last_attempt=attempt,
        control=control,
        dispatcher_state="running",
        last_event_sequence=1,
        reconciliation_required=False,
    )
    event_page = OperationEventPageV1(
        operation=operation,
        after_sequence=0,
        events=(continuity_event,),
        next_sequence=1,
        has_more=False,
        terminal_snapshot=None,
    )
    return {
        "valid/adaptive-asset-bundle.json": (
            "adaptive_asset_bundle",
            asset_bundle.model_dump(mode="json"),
        ),
        "valid/operation-attempt-record.json": (
            "operation_attempt_record",
            attempt_record.model_dump(mode="json"),
        ),
        "valid/operation-lease-record.json": (
            "operation_lease_record",
            lease.model_dump(mode="json"),
        ),
        "valid/operation-control-state.json": (
            "operation_control_state",
            control.model_dump(mode="json"),
        ),
        "valid/operation-recovery-policy.json": (
            "operation_recovery_policy",
            policy.model_dump(mode="json"),
        ),
        "valid/operation-recovery-policy-binding.json": (
            "operation_recovery_policy_binding",
            policy_binding.model_dump(mode="json"),
        ),
        "valid/operation-rlm-step-boundary.json": (
            "operation_rlm_step_boundary",
            rlm_boundary.model_dump(mode="json"),
        ),
        "valid/operation-checkpoint-binding.json": (
            "operation_checkpoint_binding",
            checkpoint.model_dump(mode="json"),
        ),
        "valid/operation-recovery-decision.json": (
            "operation_recovery_decision",
            decision.model_dump(mode="json"),
        ),
        "valid/operation-event-envelope.json": (
            "operation_event_envelope",
            continuity_event.model_dump(mode="json"),
        ),
        "valid/operation-continuity-snapshot.json": (
            "operation_continuity_snapshot",
            continuity_snapshot.model_dump(mode="json"),
        ),
        "valid/operation-event-page.json": (
            "operation_event_page",
            event_page.model_dump(mode="json"),
        ),
        "valid/artifact-reference.json": (
            "artifact_reference",
            artifact.model_dump(mode="json"),
        ),
        "valid/capability-set.json": ("capability_set", capabilities.model_dump(mode="json")),
        "valid/broker-catalog.json": (
            "broker_catalog",
            broker_catalog.model_dump(mode="json"),
        ),
        "valid/model-response.json": (
            "model_response",
            model_response.model_dump(mode="json"),
        ),
        "valid/model-route-binding.json": (
            "model_route_binding",
            model_route_binding.model_dump(mode="json"),
        ),
        "valid/model-route-catalog.json": (
            "model_route_catalog",
            model_route_catalog.model_dump(mode="json"),
        ),
        "valid/model-route-receipt.json": (
            "model_route_receipt",
            model_route_receipt.model_dump(mode="json"),
        ),
        "valid/model-usage-record.json": (
            "model_usage_record",
            model_usage.model_dump(mode="json"),
        ),
        "valid/failure-envelope.json": ("failure_envelope", failure.model_dump(mode="json")),
        "valid/reconciliation-report.json": (
            "reconciliation_report",
            reconcile.model_dump(mode="json"),
        ),
        "valid/request-envelope.json": ("request_envelope", envelope.model_dump(mode="json")),
        "valid/rlm-job-spec.json": ("rlm_job_spec", rlm_job.model_dump(mode="json")),
        "valid/security-boundary.json": ("security_boundary", boundary.model_dump(mode="json")),
    }


def fixture_documents() -> dict[str, tuple[str, bool, dict[str, Any]]]:
    valid = _valid_documents()
    request = copy.deepcopy(valid["valid/request-envelope.json"][1])
    capabilities = copy.deepcopy(valid["valid/capability-set.json"][1])
    rlm_job = copy.deepcopy(valid["valid/rlm-job-spec.json"][1])
    asset_bundle = copy.deepcopy(valid["valid/adaptive-asset-bundle.json"][1])
    model_catalog = copy.deepcopy(valid["valid/model-route-catalog.json"][1])
    model_binding = copy.deepcopy(valid["valid/model-route-binding.json"][1])
    model_receipt = copy.deepcopy(valid["valid/model-route-receipt.json"][1])
    model_usage = copy.deepcopy(valid["valid/model-usage-record.json"][1])

    invalid: dict[str, tuple[str, dict[str, Any]]] = {}

    case = copy.deepcopy(request)
    case["host"]["value"] = "../host"
    invalid["invalid/request-identity-path.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["schema_version"] = "aar.envelope.v999"
    invalid["invalid/request-version.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["input_digest"] = "sha256:BAD"
    invalid["invalid/request-digest.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["runtime_generation"] = 0
    invalid["invalid/request-generation-zero.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["expected_workspace_revision"] = -1
    invalid["invalid/request-revision-negative.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["grants"][0]["issued_to"]["value"] = "another-principal"
    invalid["invalid/request-grant-principal.json"] = ("request_envelope", case)

    case = copy.deepcopy(request)
    case["budget"]["wall_time_ms"] = -1
    invalid["invalid/request-budget-negative.json"] = ("request_envelope", case)

    case = copy.deepcopy(capabilities)
    case["digest"] = f"sha256:{'0' * 64}"
    invalid["invalid/capability-digest-mismatch.json"] = ("capability_set", case)

    case = copy.deepcopy(rlm_job)
    case["max_steps"] = 1
    invalid["invalid/rlm-strategy-step-bound.json"] = ("rlm_job_spec", case)

    case = copy.deepcopy(rlm_job)
    case["strategy"] = "implicit_backend"
    invalid["invalid/rlm-unknown-strategy.json"] = ("rlm_job_spec", case)

    case = copy.deepcopy(asset_bundle)
    case["documents"][0]["body"]["runtime_digest"] = canonical_sha256({"runtime": "tampered"})
    invalid["invalid/asset-body-digest-mismatch.json"] = (
        "adaptive_asset_bundle",
        case,
    )

    case = copy.deepcopy(asset_bundle)
    case["bundle_digest"] = f"sha256:{'0' * 64}"
    invalid["invalid/asset-bundle-digest-mismatch.json"] = (
        "adaptive_asset_bundle",
        case,
    )

    case = copy.deepcopy(valid["valid/operation-recovery-policy-binding.json"][1])
    case["policy_digest"] = f"sha256:{'0' * 64}"
    invalid["invalid/operation-recovery-policy-binding-digest-mismatch.json"] = (
        "operation_recovery_policy_binding",
        case,
    )

    case = copy.deepcopy(valid["valid/operation-rlm-step-boundary.json"][1])
    case["boundary_digest"] = f"sha256:{'0' * 64}"
    invalid["invalid/operation-rlm-step-boundary-digest-mismatch.json"] = (
        "operation_rlm_step_boundary",
        case,
    )

    case = copy.deepcopy(valid["valid/operation-event-envelope.json"][1])
    case["payload"]["attempt_no"] = 2
    invalid["invalid/operation-event-payload-digest-mismatch.json"] = (
        "operation_event_envelope",
        case,
    )

    case = copy.deepcopy(valid["valid/operation-lease-record.json"][1])
    case["expires_at_unix_ms"] = case["heartbeat_at_unix_ms"] - 1
    invalid["invalid/operation-lease-time-order.json"] = (
        "operation_lease_record",
        case,
    )

    case = copy.deepcopy(valid["valid/operation-event-page.json"][1])
    case["next_sequence"] = 0
    invalid["invalid/operation-event-page-cursor.json"] = (
        "operation_event_page",
        case,
    )

    case = copy.deepcopy(request)
    case["unexpected"] = True
    invalid["invalid/request-extra-field.json"] = ("request_envelope", case)

    case = copy.deepcopy(model_catalog)
    case["catalog_digest"] = f"sha256:{'0' * 64}"
    invalid["invalid/model-route-catalog-digest-mismatch.json"] = (
        "model_route_catalog",
        case,
    )

    case = copy.deepcopy(model_catalog)
    case["profiles"].append(copy.deepcopy(case["profiles"][0]))
    invalid["invalid/model-route-catalog-duplicate-profile.json"] = (
        "model_route_catalog",
        case,
    )

    case = copy.deepcopy(model_binding)
    case["model"] = "tampered-model"
    invalid["invalid/model-route-binding-profile-digest-mismatch.json"] = (
        "model_route_binding",
        case,
    )

    case = copy.deepcopy(model_usage)
    case["total_tokens"] += 1
    invalid["invalid/model-usage-total-mismatch.json"] = (
        "model_usage_record",
        case,
    )

    case = copy.deepcopy(model_receipt)
    case["effective"]["model"] = "drifted-model"
    receipt_payload = {
        "requested": case["requested"],
        "effective": case["effective"],
        "finish_reason": case["finish_reason"],
        "provider_response_id": case["provider_response_id"],
        "fallback_chain": case["fallback_chain"],
        "lookup_supported": case["lookup_supported"],
    }
    case["receipt_digest"] = canonical_sha256(receipt_payload)
    invalid["invalid/model-route-receipt-effective-drift.json"] = (
        "model_route_receipt",
        case,
    )

    case = copy.deepcopy(model_receipt)
    case["fallback_chain"] = [copy.deepcopy(case["effective"])]
    receipt_payload = {
        "requested": case["requested"],
        "effective": case["effective"],
        "finish_reason": case["finish_reason"],
        "provider_response_id": case["provider_response_id"],
        "fallback_chain": case["fallback_chain"],
        "lookup_supported": case["lookup_supported"],
    }
    case["receipt_digest"] = canonical_sha256(receipt_payload)
    invalid["invalid/model-route-receipt-forbidden-fallback.json"] = (
        "model_route_receipt",
        case,
    )

    documents = {
        path: (model_name, True, document) for path, (model_name, document) in valid.items()
    }
    documents.update(
        {path: (model_name, False, document) for path, (model_name, document) in invalid.items()}
    )
    return dict(sorted(documents.items()))


def ahc_integration_fixture() -> dict[str, Any]:
    principal = PrincipalRef(value="principal-ahc-fixture")
    capabilities = CapabilitySet.issue(
        (
            CapabilityDescriptor(name="evidence.query", access=AccessMode.READ),
            CapabilityDescriptor(name="model.request", access=AccessMode.WRITE),
            CapabilityDescriptor(name="operation.cancel", access=AccessMode.WRITE),
            CapabilityDescriptor(name="rlm.execute", access=AccessMode.WRITE),
            CapabilityDescriptor(name="rlm.reconcile", access=AccessMode.WRITE),
            CapabilityDescriptor(name="rlm.status", access=AccessMode.READ),
        )
    )
    spec = RlmJobSpec(
        query="Summarize the evidence retained by the host.",
        strategy="evidence_synthesis",
        max_steps=2,
    )
    deadline = 4_102_444_790_000
    grants = tuple(
        Grant(
            grant_id=grant_id,
            capability=capability,
            issued_to=principal,
            expires_at_unix_ms=deadline,
        )
        for grant_id, capability in (
            ("grant-evidence-query", "evidence.query"),
            ("grant-model-request", "model.request"),
            ("grant-rlm-execute", "rlm.execute"),
        )
    )
    envelope = RequestEnvelope(
        request_id="request-ahc-rlm-fixture",
        idempotency_key="idempotency-ahc-rlm-fixture",
        host=HostRef(value="ahc-host"),
        principal=principal,
        lane=LaneRef(value="rlm"),
        session=SessionRef(value="session-ahc-fixture"),
        runtime_generation=1,
        capability_digest=capabilities.digest,
        deadline_unix_ms=deadline,
        grants=grants,
        budget=Budget(
            wall_time_ms=30_000,
            model_requests=1,
            input_tokens=8_192,
            output_tokens=1_024,
        ),
        trace_id="trace-ahc-rlm-fixture",
        input_digest=canonical_sha256(spec),
    )
    return {
        "contract_version": "aar.ahc-integration.v1",
        "capabilities": capabilities.model_dump(mode="json"),
        "request_envelope": envelope.model_dump(mode="json"),
        "rlm_job": spec.model_dump(mode="json"),
        "broker_methods": [
            {"method": "evidence.query", "required_capability": "evidence.query"},
            {"method": "model.request", "required_capability": "model.request"},
        ],
        "authority": {
            "outer_operation_is_authoritative": True,
            "retained_child_requires_explicit_result": True,
            "effect_execution_available": False,
            "provider_credentials_available": False,
            "final_delivery_available": False,
        },
        "native_adapter_may_strengthen": [
            "cancellation_propagation",
            "restart_reconciliation",
            "session_binding",
        ],
        "native_adapter_must_preserve": [
            "artifact_identity",
            "broker_grants",
            "budget_accounting",
            "operation_certainty",
            "terminal_receipt",
        ],
    }


def ahc_continuity_fixture() -> dict[str, Any]:
    """Optional AAR/AHC continuity profile without importing AHC-owned types."""

    operation = OperationRef(value="operation-ahc-continuity")
    attempt = OperationAttemptRefV1(
        operation=operation,
        attempt_no=1,
        attempt_id="attempt-ahc-continuity-1",
    )
    owner_digest = canonical_sha256({"owner": "ahc-adapter-fixture"})
    event_payload = {
        "logical_operation": operation.value,
        "attempt_no": attempt.attempt_no,
        "lease_epoch": 1,
    }
    control = OperationControlStateV1(
        operation=operation,
        control_revision=0,
        cancellation_requested=False,
    )
    event = OperationEventEnvelopeV1(
        sequence=1,
        operation=operation,
        attempt=attempt,
        event_kind="attempt_claimed",
        state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=1,
        at_unix_ms=4_102_444_700_100,
        payload=event_payload,
        payload_digest=canonical_sha256(event_payload),
    )
    snapshot = OperationContinuitySnapshotV1(
        operation=operation,
        operation_state=OperationState.RUNNING,
        certainty=OutcomeCertainty.CERTAIN,
        record_revision=1,
        current_attempt=attempt,
        last_attempt=attempt,
        control=control,
        dispatcher_state="running",
        last_event_sequence=1,
        reconciliation_required=False,
    )
    return {
        "contract_version": "aar.ahc-continuity.v1",
        "profile": "operation.continuity.v1",
        "attempt": OperationAttemptRecordV1(
            ref=attempt,
            runtime_generation=1,
            dispatcher_generation=1,
            state="running",
            certainty=OutcomeCertainty.CERTAIN,
            created_at_unix_ms=4_102_444_700_000,
            started_at_unix_ms=4_102_444_700_100,
        ).model_dump(mode="json"),
        "lease": OperationLeaseRecordV1(
            attempt=attempt,
            runtime_generation=1,
            dispatcher_generation=1,
            lease_epoch=1,
            owner_digest=owner_digest,
            acquired_at_unix_ms=4_102_444_700_100,
            heartbeat_at_unix_ms=4_102_444_700_200,
            expires_at_unix_ms=4_102_444_730_200,
        ).model_dump(mode="json"),
        "control": control.model_dump(mode="json"),
        "snapshot": snapshot.model_dump(mode="json"),
        "event_page": OperationEventPageV1(
            operation=operation,
            after_sequence=0,
            events=(event,),
            next_sequence=1,
            has_more=False,
            terminal_snapshot=None,
        ).model_dump(mode="json"),
        "semantic_mapping": {
            "operation": "durable logical intent; never a delivery receipt",
            "attempt": "one execution owner generation",
            "lease": "backend-neutral owner fence; AHC retains host authority",
            "event_cursor": "ordered evidence projection; arrival order is not authority",
            "terminal_result": "child execution evidence only",
        },
        "authority": {
            "aar_owns_ahc_task_state": False,
            "aar_owns_delivery": False,
            "operation_id_is_bearer_authority": False,
            "shared_ig_gate_added": False,
        },
    }


def generate_contract(root: Path) -> list[Path]:
    written: list[Path] = []
    schema_path = root / "schemas" / "aar-schemas-v1.json"
    schema_path.parent.mkdir(parents=True, exist_ok=True)
    schema_path.write_bytes(pretty_json_bytes(schema_bundle()))
    written.append(schema_path)

    integration_document = ahc_integration_fixture()
    integration_relative = Path("integration/ahc/rlm-contract-v1.json")
    integration_path = root / integration_relative
    integration_path.parent.mkdir(parents=True, exist_ok=True)
    integration_path.write_bytes(pretty_json_bytes(integration_document))
    written.append(integration_path)

    continuity_integration_document = ahc_continuity_fixture()
    continuity_integration_relative = Path("integration/ahc/continuity-contract-v1.json")
    continuity_integration_path = root / continuity_integration_relative
    continuity_integration_path.write_bytes(pretty_json_bytes(continuity_integration_document))
    written.append(continuity_integration_path)

    entries: list[dict[str, Any]] = []
    for relative_path, (model_name, valid, document) in fixture_documents().items():
        target = root / "tests" / "fixtures" / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(pretty_json_bytes(document))
        written.append(target)
        entries.append(
            {
                "path": relative_path.replace("\\", "/"),
                "model": model_name,
                "valid": valid,
                "document_digest": canonical_sha256(document),
            }
        )

    manifest_core = {
        "schema_versions": SCHEMA_VERSIONS,
        "fixtures": entries,
        "integrations": [
            {
                "path": integration_relative.as_posix(),
                "document_digest": canonical_sha256(integration_document),
            },
            {
                "path": continuity_integration_relative.as_posix(),
                "document_digest": canonical_sha256(continuity_integration_document),
            },
        ],
    }
    manifest = {**manifest_core, "fixture_set_digest": canonical_sha256(manifest_core)}
    manifest_path = root / "tests" / "fixtures" / "manifest.json"
    manifest_path.write_bytes(pretty_json_bytes(manifest))
    written.append(manifest_path)
    return sorted(written)


def validate_fixtures(root: Path) -> list[str]:
    errors: list[str] = []
    fixture_root = root / "tests" / "fixtures"
    manifest = json.loads((fixture_root / "manifest.json").read_text(encoding="utf-8"))
    manifest_core = {
        "schema_versions": manifest["schema_versions"],
        "fixtures": manifest["fixtures"],
        "integrations": manifest["integrations"],
    }
    if manifest["fixture_set_digest"] != canonical_sha256(manifest_core):
        errors.append("fixture manifest digest mismatch")

    expected_integrations = {
        "integration/ahc/rlm-contract-v1.json": ahc_integration_fixture(),
        "integration/ahc/continuity-contract-v1.json": ahc_continuity_fixture(),
    }
    for entry in manifest["integrations"]:
        document = json.loads((root / entry["path"]).read_text(encoding="utf-8"))
        if entry["document_digest"] != canonical_sha256(document):
            errors.append(f"integration fixture digest mismatch: {entry['path']}")
        expected = expected_integrations.get(entry["path"])
        if expected is None or document != expected:
            errors.append(f"integration fixture is stale: {entry['path']}")

    for entry in manifest["fixtures"]:
        document = json.loads((fixture_root / entry["path"]).read_text(encoding="utf-8"))
        if entry["document_digest"] != canonical_sha256(document):
            errors.append(f"fixture digest mismatch: {entry['path']}")
        model = CONTRACT_MODELS[entry["model"]]
        try:
            parsed = model.model_validate_json(canonical_json_bytes(document), strict=True)
        except ValidationError:
            if entry["valid"]:
                errors.append(f"valid fixture rejected: {entry['path']}")
        else:
            if not entry["valid"]:
                errors.append(f"invalid fixture accepted: {entry['path']}")
            elif canonical_json_bytes(parsed) != canonical_json_bytes(document):
                errors.append(f"valid fixture changed on round trip: {entry['path']}")
    return errors


def forbidden_imports(source_root: Path) -> list[str]:
    violations: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: Iterable[str]
            if isinstance(node, ast.Import):
                names = (alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = (node.module,)
            else:
                continue
            for name in names:
                root = name.split(".", maxsplit=1)[0].lower()
                if root in FORBIDDEN_IMPORT_ROOTS:
                    violations.append(f"{path}:{node.lineno}: forbidden import {name}")
    return violations


def verify_contract(root: Path) -> list[str]:
    errors = validate_fixtures(root)
    checked_bundle = json.loads(
        (root / "schemas" / "aar-schemas-v1.json").read_text(encoding="utf-8")
    )
    if checked_bundle != schema_bundle():
        errors.append("checked-in schema bundle is stale")
    errors.extend(forbidden_imports(root / "src" / "aar"))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "verify"))
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.command == "generate":
        for path in generate_contract(root):
            print(path.relative_to(root))
        return 0
    errors = verify_contract(root)
    if errors:
        for error in errors:
            print(error)
        return 1
    print("AR-0A contract verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
