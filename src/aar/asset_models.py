"""Transport-neutral contracts for immutable adaptive assets and event disclosure."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from aar.canonical import canonical_sha256
from aar.schemas import (
    CapabilityName,
    Digest,
    OperationRef,
    OperationState,
    OutcomeCertainty,
    StrictModel,
)
from aar.versions import ADAPTIVE_ASSET_SCHEMA_VERSION

ASSET_BUNDLE_SCHEMA_VERSION = "aar.asset-bundle.v1"
ASSET_EVENT_SCHEMA_VERSION = "aar.asset-event.v1"

AssetKind = Literal[
    "agent_fingerprint",
    "episode",
    "evaluation",
    "outcome",
    "proposal",
]
AssetEventKind = Literal["selection", "disclosure", "use", "outcome", "attribution"]
EvaluationDecision = Literal["pass", "fail", "abstain"]


class AdaptiveAssetRef(StrictModel):
    schema_version: Literal["aar.adaptive-asset.v1"] = ADAPTIVE_ASSET_SCHEMA_VERSION
    kind: AssetKind
    digest: Digest


class AgentFingerprint(StrictModel):
    asset_kind: Literal["agent_fingerprint"] = "agent_fingerprint"
    schema_version: Literal["aar.agent-fingerprint.v1"] = "aar.agent-fingerprint.v1"
    runtime_digest: Digest
    capability_digest: Digest
    policy_digest: Digest
    tool_surface_digest: Digest | None = None


class Episode(StrictModel):
    asset_kind: Literal["episode"] = "episode"
    schema_version: Literal["aar.episode.v1"] = "aar.episode.v1"
    operation: OperationRef
    agent: AdaptiveAssetRef
    request_digest: Digest
    selected_assets: tuple[AdaptiveAssetRef, ...] = ()
    used_assets: tuple[AdaptiveAssetRef, ...] = ()
    result_digest: Digest | None = None

    @model_validator(mode="after")
    def references_are_canonical(self) -> Self:
        if self.agent.kind != "agent_fingerprint":
            raise ValueError("episode agent must reference an agent_fingerprint asset")
        _require_sorted_unique_refs(self.selected_assets, "selected_assets")
        _require_sorted_unique_refs(self.used_assets, "used_assets")
        selected = {_ref_key(item) for item in self.selected_assets}
        used = {_ref_key(item) for item in self.used_assets}
        if not used.issubset(selected):
            raise ValueError("used_assets must be a subset of selected_assets")
        return self


class OutcomeMetric(StrictModel):
    name: CapabilityName
    value_milli: Annotated[int, Field(ge=-1_000_000_000, le=1_000_000_000, strict=True)]
    unit: Annotated[str, Field(min_length=1, max_length=64, strict=True)]


class Outcome(StrictModel):
    asset_kind: Literal["outcome"] = "outcome"
    schema_version: Literal["aar.outcome.v1"] = "aar.outcome.v1"
    episode: AdaptiveAssetRef
    state: OperationState
    certainty: OutcomeCertainty
    metrics: tuple[OutcomeMetric, ...] = ()
    evidence: tuple[AdaptiveAssetRef, ...] = ()

    @model_validator(mode="after")
    def outcome_is_explicit(self) -> Self:
        if self.episode.kind != "episode":
            raise ValueError("outcome must reference an episode asset")
        names = [item.name for item in self.metrics]
        if names != sorted(names) or len(names) != len(set(names)):
            raise ValueError("outcome metrics must be sorted by unique name")
        _require_sorted_unique_refs(self.evidence, "evidence")
        if self.state is OperationState.INDETERMINATE:
            if self.certainty is not OutcomeCertainty.INDETERMINATE:
                raise ValueError("indeterminate outcome requires indeterminate certainty")
        elif self.certainty is OutcomeCertainty.INDETERMINATE:
            raise ValueError("indeterminate certainty requires indeterminate outcome state")
        return self


class Evaluation(StrictModel):
    asset_kind: Literal["evaluation"] = "evaluation"
    schema_version: Literal["aar.evaluation.v1"] = "aar.evaluation.v1"
    evaluator: AdaptiveAssetRef
    subjects: Annotated[tuple[AdaptiveAssetRef, ...], Field(min_length=1)]
    rubric_digest: Digest
    decision: EvaluationDecision
    score_milli: Annotated[int | None, Field(ge=0, le=1_000, strict=True)] = None
    uncertainty: Annotated[str | None, Field(min_length=1, max_length=512, strict=True)] = None

    @model_validator(mode="after")
    def evaluation_is_bounded(self) -> Self:
        if self.evaluator.kind != "agent_fingerprint":
            raise ValueError("evaluation evaluator must reference an agent_fingerprint asset")
        _require_sorted_unique_refs(self.subjects, "subjects")
        if self.decision == "abstain":
            if self.score_milli is not None or self.uncertainty is None:
                raise ValueError("abstention requires uncertainty and forbids a score")
        elif self.uncertainty is not None:
            raise ValueError("non-abstaining evaluation cannot carry uncertainty")
        return self


class Proposal(StrictModel):
    asset_kind: Literal["proposal"] = "proposal"
    schema_version: Literal["aar.proposal.v1"] = "aar.proposal.v1"
    target: Annotated[
        str,
        Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._~/-]{0,255}$", strict=True),
    ]
    based_on: Annotated[tuple[AdaptiveAssetRef, ...], Field(min_length=1)]
    candidate_digest: Digest
    rollback_digest: Digest | None = None
    rationale_digest: Digest
    confidence_milli: Annotated[int, Field(ge=0, le=1_000, strict=True)]

    @model_validator(mode="after")
    def provenance_is_canonical(self) -> Self:
        _require_sorted_unique_refs(self.based_on, "based_on")
        return self


AdaptiveAssetBody = Annotated[
    AgentFingerprint | Episode | Evaluation | Outcome | Proposal,
    Field(discriminator="asset_kind"),
]


def _ref_key(reference: AdaptiveAssetRef) -> tuple[str, str]:
    return reference.kind, reference.digest


def _require_sorted_unique_refs(
    references: tuple[AdaptiveAssetRef, ...], field_name: str
) -> None:
    keys = [_ref_key(item) for item in references]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise ValueError(f"{field_name} must be sorted and unique")


def body_dependencies(body: AdaptiveAssetBody) -> tuple[AdaptiveAssetRef, ...]:
    references: set[AdaptiveAssetRef] = set()
    if isinstance(body, Episode):
        references.update((body.agent, *body.selected_assets, *body.used_assets))
    elif isinstance(body, Outcome):
        references.update((body.episode, *body.evidence))
    elif isinstance(body, Evaluation):
        references.update((body.evaluator, *body.subjects))
    elif isinstance(body, Proposal):
        references.update(body.based_on)
    return tuple(sorted(references, key=_ref_key))


class AdaptiveAssetManifest(StrictModel):
    schema_version: Literal["aar.adaptive-asset.v1"] = ADAPTIVE_ASSET_SCHEMA_VERSION
    asset: AdaptiveAssetRef
    body_schema_version: str
    body_digest: Digest
    previous: AdaptiveAssetRef | None = None
    dependencies: tuple[AdaptiveAssetRef, ...] = ()
    manifest_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        body: AdaptiveAssetBody,
        previous: AdaptiveAssetRef | None = None,
    ) -> AdaptiveAssetManifest:
        kind: AssetKind = body.asset_kind
        dependencies = body_dependencies(body)
        body_digest = canonical_sha256(body)
        core = {
            "schema_version": ADAPTIVE_ASSET_SCHEMA_VERSION,
            "kind": kind,
            "body_schema_version": body.schema_version,
            "body_digest": body_digest,
            "previous": previous,
            "dependencies": dependencies,
        }
        digest = canonical_sha256(core)
        return cls(
            asset=AdaptiveAssetRef(kind=kind, digest=digest),
            body_schema_version=body.schema_version,
            body_digest=body_digest,
            previous=previous,
            dependencies=dependencies,
            manifest_digest=digest,
        )

    @model_validator(mode="after")
    def manifest_is_content_bound(self) -> Self:
        _require_sorted_unique_refs(self.dependencies, "dependencies")
        if self.asset.digest != self.manifest_digest:
            raise ValueError("asset reference must bind the manifest digest")
        if self.previous is not None and self.previous.kind != self.asset.kind:
            raise ValueError("previous revision must have the same asset kind")
        core = {
            "schema_version": self.schema_version,
            "kind": self.asset.kind,
            "body_schema_version": self.body_schema_version,
            "body_digest": self.body_digest,
            "previous": self.previous,
            "dependencies": self.dependencies,
        }
        if self.manifest_digest != canonical_sha256(core):
            raise ValueError("manifest digest does not match canonical manifest bytes")
        return self


class AdaptiveAssetDocument(StrictModel):
    manifest: AdaptiveAssetManifest
    body: AdaptiveAssetBody

    @classmethod
    def issue(
        cls,
        body: AdaptiveAssetBody,
        *,
        previous: AdaptiveAssetRef | None = None,
    ) -> AdaptiveAssetDocument:
        return cls(
            manifest=AdaptiveAssetManifest.issue(body=body, previous=previous),
            body=body,
        )

    @model_validator(mode="after")
    def document_matches_manifest(self) -> Self:
        if self.manifest.asset.kind != self.body.asset_kind:
            raise ValueError("manifest kind does not match asset body kind")
        if self.manifest.body_schema_version != self.body.schema_version:
            raise ValueError("manifest body schema does not match asset body schema")
        if self.manifest.body_digest != canonical_sha256(self.body):
            raise ValueError("manifest body digest does not match canonical body bytes")
        if self.manifest.dependencies != body_dependencies(self.body):
            raise ValueError("manifest dependencies do not match typed body references")
        return self


class AssetAttribution(StrictModel):
    source: AdaptiveAssetRef
    target: AdaptiveAssetRef
    relation: CapabilityName
    weight_milli: Annotated[int, Field(ge=0, le=1_000, strict=True)]


class AdaptiveAssetEvent(StrictModel):
    schema_version: Literal["aar.asset-event.v1"] = ASSET_EVENT_SCHEMA_VERSION
    kind: AssetEventKind
    operation: OperationRef
    agent: AdaptiveAssetRef | None = None
    episode: AdaptiveAssetRef | None = None
    assets: tuple[AdaptiveAssetRef, ...] = ()
    outcome: AdaptiveAssetRef | None = None
    attributions: tuple[AssetAttribution, ...] = ()
    details_digest: Digest
    event_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        kind: AssetEventKind,
        operation: OperationRef,
        details_digest: Digest,
        agent: AdaptiveAssetRef | None = None,
        episode: AdaptiveAssetRef | None = None,
        assets: tuple[AdaptiveAssetRef, ...] = (),
        outcome: AdaptiveAssetRef | None = None,
        attributions: tuple[AssetAttribution, ...] = (),
    ) -> AdaptiveAssetEvent:
        core = {
            "schema_version": ASSET_EVENT_SCHEMA_VERSION,
            "kind": kind,
            "operation": operation,
            "agent": agent,
            "episode": episode,
            "assets": assets,
            "outcome": outcome,
            "attributions": attributions,
            "details_digest": details_digest,
        }
        return cls(
            kind=kind,
            operation=operation,
            agent=agent,
            episode=episode,
            assets=assets,
            outcome=outcome,
            attributions=attributions,
            details_digest=details_digest,
            event_digest=canonical_sha256(core),
        )

    @model_validator(mode="after")
    def event_is_explicit_and_content_bound(self) -> Self:
        _require_sorted_unique_refs(self.assets, "assets")
        attribution_keys = [
            (*_ref_key(item.source), *_ref_key(item.target), item.relation)
            for item in self.attributions
        ]
        if attribution_keys != sorted(attribution_keys) or len(attribution_keys) != len(
            set(attribution_keys)
        ):
            raise ValueError("attributions must be sorted and unique")
        if self.agent is not None and self.agent.kind != "agent_fingerprint":
            raise ValueError("event agent must reference an agent_fingerprint asset")
        if self.episode is not None and self.episode.kind != "episode":
            raise ValueError("event episode must reference an episode asset")
        if self.kind in {"selection", "disclosure", "use"} and not self.assets:
            raise ValueError(f"{self.kind} event requires at least one asset")
        if self.kind == "outcome":
            if self.episode is None or self.outcome is None or self.outcome.kind != "outcome":
                raise ValueError("outcome event requires episode and outcome references")
        elif self.outcome is not None:
            raise ValueError("only outcome events may carry an outcome reference")
        if self.kind == "attribution" and not self.attributions:
            raise ValueError("attribution event requires attribution entries")
        core = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "operation": self.operation,
            "agent": self.agent,
            "episode": self.episode,
            "assets": self.assets,
            "outcome": self.outcome,
            "attributions": self.attributions,
            "details_digest": self.details_digest,
        }
        if self.event_digest != canonical_sha256(core):
            raise ValueError("event digest does not match canonical event bytes")
        return self


class OutcomeObservation(StrictModel):
    status: Literal["unknown", "observed"]
    episode: AdaptiveAssetRef
    outcome: AdaptiveAssetRef | None = None

    @model_validator(mode="after")
    def unknown_is_not_negative(self) -> Self:
        if self.episode.kind != "episode":
            raise ValueError("outcome observation must reference an episode")
        if self.status == "unknown" and self.outcome is not None:
            raise ValueError("unknown outcome cannot carry an outcome reference")
        if self.status == "observed" and (
            self.outcome is None or self.outcome.kind != "outcome"
        ):
            raise ValueError("observed outcome requires an outcome asset reference")
        return self


class AdaptiveAssetBundle(StrictModel):
    schema_version: Literal["aar.asset-bundle.v1"] = ASSET_BUNDLE_SCHEMA_VERSION
    documents: tuple[AdaptiveAssetDocument, ...]
    events: tuple[AdaptiveAssetEvent, ...] = ()
    bundle_digest: Digest

    @classmethod
    def issue(
        cls,
        *,
        documents: tuple[AdaptiveAssetDocument, ...],
        events: tuple[AdaptiveAssetEvent, ...] = (),
    ) -> AdaptiveAssetBundle:
        ordered_documents = tuple(
            sorted(documents, key=lambda item: item.manifest.asset.digest)
        )
        ordered_events = tuple(sorted(events, key=lambda item: item.event_digest))
        core = {
            "schema_version": ASSET_BUNDLE_SCHEMA_VERSION,
            "documents": ordered_documents,
            "events": ordered_events,
        }
        return cls(
            documents=ordered_documents,
            events=ordered_events,
            bundle_digest=canonical_sha256(core),
        )

    @model_validator(mode="after")
    def bundle_is_canonical(self) -> Self:
        document_digests = [item.manifest.asset.digest for item in self.documents]
        event_digests = [item.event_digest for item in self.events]
        if document_digests != sorted(document_digests) or len(document_digests) != len(
            set(document_digests)
        ):
            raise ValueError("bundle documents must be sorted by unique digest")
        if event_digests != sorted(event_digests) or len(event_digests) != len(
            set(event_digests)
        ):
            raise ValueError("bundle events must be sorted by unique digest")
        core = {
            "schema_version": self.schema_version,
            "documents": self.documents,
            "events": self.events,
        }
        if self.bundle_digest != canonical_sha256(core):
            raise ValueError("bundle digest does not match canonical bundle bytes")
        return self


class AssetImportResult(StrictModel):
    bundle_digest: Digest
    asset_count: int
    event_count: int
    active_serving_mutated: Literal[False] = False


class MaterializerDescriptor(StrictModel):
    name: CapabilityName
    version: str
    capability_digest: Digest
    prepares_rollback: bool
    activation_available: Literal[False] = False


class MaterializationRequest(StrictModel):
    proposal: AdaptiveAssetRef
    expected_serving_digest: Digest
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128, strict=True)]

    @model_validator(mode="after")
    def proposal_kind_is_explicit(self) -> Self:
        if self.proposal.kind != "proposal":
            raise ValueError("materialization request requires a proposal asset")
        return self


class MaterializationPreview(StrictModel):
    request_digest: Digest
    candidate_digest: Digest
    rollback_digest: Digest | None
    activation_required: Literal[True] = True
    external_effect_performed: Literal[False] = False


ASSET_SCHEMA_MODELS: dict[str, type[StrictModel]] = {
    "adaptive_asset_bundle": AdaptiveAssetBundle,
    "adaptive_asset_document": AdaptiveAssetDocument,
    "adaptive_asset_event": AdaptiveAssetEvent,
    "adaptive_asset_manifest": AdaptiveAssetManifest,
    "adaptive_asset_ref": AdaptiveAssetRef,
    "agent_fingerprint": AgentFingerprint,
    "asset_import_result": AssetImportResult,
    "episode": Episode,
    "evaluation": Evaluation,
    "materialization_preview": MaterializationPreview,
    "materialization_request": MaterializationRequest,
    "materializer_descriptor": MaterializerDescriptor,
    "outcome": Outcome,
    "outcome_observation": OutcomeObservation,
    "proposal": Proposal,
}
