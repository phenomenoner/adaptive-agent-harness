from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from aar.asset_models import (
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    AdaptiveAssetEvent,
    AgentFingerprint,
    AssetAttribution,
    AssetImportResult,
    Episode,
    Evaluation,
    MaterializationPreview,
    MaterializationRequest,
    MaterializerDescriptor,
    Outcome,
    OutcomeMetric,
    Proposal,
)
from aar.assets import (
    AdaptiveAssetStore,
    AssetConflict,
    AssetMigrationNonDeterministic,
    AssetMigrationRegistry,
    AssetReferenceMissing,
    SimulatedAssetProcessLoss,
)
from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.runtime.reference_host import ReferenceHost
from aar.schemas import (
    Budget,
    Grant,
    HostRef,
    LaneRef,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    PrincipalRef,
    RequestEnvelope,
    SessionRef,
)

NOW_MS = 1_700_000_000_000


def fingerprint_document() -> AdaptiveAssetDocument:
    return AdaptiveAssetDocument.issue(
        AgentFingerprint(
            runtime_digest=canonical_sha256({"runtime": "aar"}),
            capability_digest=canonical_sha256({"capabilities": ["asset.read"]}),
            policy_digest=canonical_sha256({"policy": "bounded"}),
            tool_surface_digest=canonical_sha256({"tools": ["aar_asset_get"]}),
        )
    )


def asset_chain() -> tuple[AdaptiveAssetDocument, ...]:
    fingerprint = fingerprint_document()
    episode = AdaptiveAssetDocument.issue(
        Episode(
            operation=OperationRef(value="operation-asset-test"),
            agent=fingerprint.manifest.asset,
            request_digest=canonical_sha256({"request": "portable assets"}),
            result_digest=canonical_sha256({"result": "bounded"}),
        )
    )
    outcome = AdaptiveAssetDocument.issue(
        Outcome(
            episode=episode.manifest.asset,
            state=OperationState.SUCCEEDED,
            certainty=OutcomeCertainty.CERTAIN,
            metrics=(OutcomeMetric(name="quality", value_milli=900, unit="milli"),),
        )
    )
    evaluation = AdaptiveAssetDocument.issue(
        Evaluation(
            evaluator=fingerprint.manifest.asset,
            subjects=(outcome.manifest.asset,),
            rubric_digest=canonical_sha256({"rubric": "quality-v1"}),
            decision="pass",
            score_milli=900,
        )
    )
    proposal = AdaptiveAssetDocument.issue(
        Proposal(
            target="agent.prompt.system",
            based_on=(evaluation.manifest.asset,),
            candidate_digest=canonical_sha256({"candidate": "v2"}),
            rollback_digest=canonical_sha256({"candidate": "v1"}),
            rationale_digest=canonical_sha256({"rationale": "evaluation"}),
            confidence_milli=800,
        )
    )
    return fingerprint, episode, outcome, evaluation, proposal


def import_envelope(
    host: ReferenceHost,
    bundle,
    *,
    idempotency_key: str = "asset-import-0001",
) -> RequestEnvelope:
    principal = PrincipalRef(value="principal-assets")
    deadline = NOW_MS + 10_000
    return RequestEnvelope(
        request_id=f"request-{idempotency_key}",
        idempotency_key=idempotency_key,
        host=HostRef(value="reference-host"),
        principal=principal,
        lane=LaneRef(value="asset-import"),
        session=SessionRef(value="session-assets"),
        runtime_generation=host.runtime_generation,
        capability_digest=host.capabilities.digest,
        deadline_unix_ms=deadline,
        grants=(
            Grant(
                grant_id="grant-asset-import",
                capability="asset.import",
                issued_to=principal,
                expires_at_unix_ms=deadline,
            ),
        ),
        budget=Budget(wall_time_ms=10_000),
        trace_id=f"trace-{idempotency_key}",
        input_digest=canonical_sha256(bundle),
    )


def test_manifest_and_body_are_content_bound() -> None:
    document = fingerprint_document()
    assert document.manifest.asset.digest == document.manifest.manifest_digest
    assert document.manifest.body_digest == canonical_sha256(document.body)
    assert AdaptiveAssetDocument.issue(document.body) == document

    tampered = document.model_dump(mode="json")
    tampered["body"]["runtime_digest"] = canonical_sha256({"runtime": "tampered"})
    with pytest.raises(ValidationError, match="body digest"):
        AdaptiveAssetDocument.model_validate_json(
            canonical_json_bytes(tampered), strict=True
        )


def test_export_import_round_trip_is_deterministic_and_closed(
    tmp_path: Path,
) -> None:
    source = AdaptiveAssetStore(tmp_path / "source.sqlite3")
    target = AdaptiveAssetStore(tmp_path / "target.sqlite3")
    try:
        chain = asset_chain()
        for document in chain:
            source.put(document)
        fingerprint, episode, outcome, evaluation, proposal = chain
        events = (
            AdaptiveAssetEvent.issue(
                kind="selection",
                operation=episode.body.operation,
                agent=fingerprint.manifest.asset,
                episode=episode.manifest.asset,
                assets=(proposal.manifest.asset,),
                details_digest=canonical_sha256({"selector": "test"}),
            ),
            AdaptiveAssetEvent.issue(
                kind="disclosure",
                operation=episode.body.operation,
                agent=fingerprint.manifest.asset,
                episode=episode.manifest.asset,
                assets=(proposal.manifest.asset,),
                details_digest=canonical_sha256({"audience": "test"}),
            ),
            AdaptiveAssetEvent.issue(
                kind="use",
                operation=episode.body.operation,
                agent=fingerprint.manifest.asset,
                episode=episode.manifest.asset,
                assets=(proposal.manifest.asset,),
                details_digest=canonical_sha256({"consumer": "test"}),
            ),
            AdaptiveAssetEvent.issue(
                kind="outcome",
                operation=episode.body.operation,
                agent=fingerprint.manifest.asset,
                episode=episode.manifest.asset,
                outcome=outcome.manifest.asset,
                details_digest=canonical_sha256({"observer": "test"}),
            ),
            AdaptiveAssetEvent.issue(
                kind="attribution",
                operation=episode.body.operation,
                agent=fingerprint.manifest.asset,
                episode=episode.manifest.asset,
                attributions=(
                    AssetAttribution(
                        source=evaluation.manifest.asset,
                        target=proposal.manifest.asset,
                        relation="derived.from",
                        weight_milli=1_000,
                    ),
                ),
                details_digest=canonical_sha256({"attributor": "test"}),
            ),
        )
        for event in events:
            source.append_event(event)

        first = source.export_bundle((proposal.manifest.asset,))
        second = source.export_bundle((proposal.manifest.asset,))
        assert canonical_json_bytes(first) == canonical_json_bytes(second)
        assert len(first.documents) == 5
        assert {event.kind for event in first.events} == {
            "selection",
            "disclosure",
            "use",
            "outcome",
            "attribution",
        }

        serving_state = {"active_digest": canonical_sha256({"serving": "unchanged"})}
        before = serving_state.copy()
        imported = target.import_bundle(first)
        assert imported.asset_count == 5
        assert imported.event_count == 5
        assert not imported.active_serving_mutated
        assert serving_state == before
        assert not hasattr(target, "activate")
        assert canonical_json_bytes(target.export_bundle((proposal.manifest.asset,))) == (
            canonical_json_bytes(first)
        )

        repeated = target.import_bundle(first)
        assert repeated == imported
    finally:
        source.close()
        target.close()


def test_missing_reference_fails_before_any_import_mutation(tmp_path: Path) -> None:
    store = AdaptiveAssetStore(tmp_path / "missing.sqlite3")
    try:
        fingerprint, episode, *_rest = asset_chain()
        assert episode.manifest.dependencies == (fingerprint.manifest.asset,)
        with pytest.raises(AssetReferenceMissing, match="dependency"):
            store.put(episode)
        assert store.counts() == (0, 0)
    finally:
        store.close()


def test_missing_outcome_is_unknown_and_conflicting_outcomes_fail_closed(
    tmp_path: Path,
) -> None:
    store = AdaptiveAssetStore(tmp_path / "outcome.sqlite3")
    try:
        fingerprint, episode, outcome, *_rest = asset_chain()
        store.put(fingerprint)
        store.put(episode)
        assert store.outcome(episode.manifest.asset).status == "unknown"

        store.put(outcome)
        first_event = AdaptiveAssetEvent.issue(
            kind="outcome",
            operation=episode.body.operation,
            episode=episode.manifest.asset,
            outcome=outcome.manifest.asset,
            details_digest=canonical_sha256({"observer": "one"}),
        )
        store.append_event(first_event)
        assert store.outcome(episode.manifest.asset).outcome == outcome.manifest.asset

        conflicting = AdaptiveAssetDocument.issue(
            Outcome(
                episode=episode.manifest.asset,
                state=OperationState.FAILED,
                certainty=OutcomeCertainty.CERTAIN,
            )
        )
        store.put(conflicting)
        second_event = AdaptiveAssetEvent.issue(
            kind="outcome",
            operation=episode.body.operation,
            episode=episode.manifest.asset,
            outcome=conflicting.manifest.asset,
            details_digest=canonical_sha256({"observer": "two"}),
        )
        with pytest.raises(AssetConflict, match="different explicit outcome"):
            store.append_event(second_event)
        assert store.outcome(episode.manifest.asset).outcome == outcome.manifest.asset
    finally:
        store.close()


def test_detached_schema_migration_is_explicit_and_deterministic() -> None:
    current = fingerprint_document()
    legacy = canonical_json_bytes(
        {"schema_version": "example.asset.v0", "fingerprint": "legacy"}
    )
    migrations = AssetMigrationRegistry()
    migrations.register(
        "example.asset.v0",
        lambda _raw: current.model_dump(mode="json"),
    )
    assert migrations.migrate(legacy) == current

    toggle = False

    def unstable(_raw):
        nonlocal toggle
        toggle = not toggle
        selected = current if toggle else asset_chain()[1]
        return selected.model_dump(mode="json")

    unstable_migrations = AssetMigrationRegistry()
    unstable_migrations.register("example.asset.v0", unstable)
    with pytest.raises(AssetMigrationNonDeterministic):
        unstable_migrations.migrate(legacy)


def test_materializer_contract_is_prepare_only() -> None:
    proposal = asset_chain()[-1]
    request = MaterializationRequest(
        proposal=proposal.manifest.asset,
        expected_serving_digest=canonical_sha256({"serving": "v1"}),
        idempotency_key="materialize-0001",
    )
    descriptor = MaterializerDescriptor(
        name="asset.materializer.preview",
        version="1.0.0",
        capability_digest=canonical_sha256({"prepare": True, "activate": False}),
        prepares_rollback=True,
    )
    preview = MaterializationPreview(
        request_digest=canonical_sha256(request),
        candidate_digest=proposal.body.candidate_digest,
        rollback_digest=proposal.body.rollback_digest,
    )
    assert not descriptor.activation_available
    assert preview.activation_required
    assert not preview.external_effect_performed


def test_reference_host_import_is_an_idempotent_outer_operation(tmp_path: Path) -> None:
    host = ReferenceHost(
        tmp_path / "asset-host.sqlite3",
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
    )
    try:
        bundle = host.adaptive_assets.export_bundle(())
        envelope = import_envelope(host, bundle)
        first = host.execute_asset_import(envelope, bundle)
        second = host.execute_asset_import(envelope, bundle)
        assert first.state is OperationState.SUCCEEDED
        assert second == first
        result = AssetImportResult.model_validate_json(first.result_json or "null", strict=True)
        assert result.bundle_digest == bundle.bundle_digest
        assert not result.active_serving_mutated
    finally:
        host.close()


def test_restart_reconciles_atomic_asset_import_without_serving_mutation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "asset-restart.sqlite3"
    chain = asset_chain()
    bundle = AdaptiveAssetBundle.issue(documents=chain)
    first = ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
    )
    envelope = import_envelope(first, bundle, idempotency_key="asset-import-restart")
    accepted = first.submit_asset_import(envelope, bundle)
    with pytest.raises(SimulatedAssetProcessLoss):
        first.run_asset_import(
            accepted.operation,
            failpoint="process_loss_after_atomic_import",
        )
    assert first.adaptive_assets.counts() == (5, 0)
    first.close()

    second = ReferenceHost(
        database,
        now_ms=lambda: NOW_MS,
        programmable_backend="plain",
    )
    try:
        assert second.status(accepted.operation).state is OperationState.INDETERMINATE
        report = second.reconcile_asset_import(accepted.operation)
        assert report.state is OperationState.SUCCEEDED
        result = AssetImportResult.model_validate_json(
            second.status(accepted.operation).result_json or "null",
            strict=True,
        )
        assert result.bundle_digest == bundle.bundle_digest
        assert second.adaptive_assets.counts() == (5, 0)
        assert not result.active_serving_mutated
    finally:
        second.close()
