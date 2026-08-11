"""Immutable adaptive-asset store, deterministic bundles, and migration seams."""

from __future__ import annotations

import copy
import json
import sqlite3
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Protocol

from aar.asset_models import (
    ADAPTIVE_ASSET_SCHEMA_VERSION,
    AdaptiveAssetBundle,
    AdaptiveAssetDocument,
    AdaptiveAssetEvent,
    AdaptiveAssetRef,
    AssetImportResult,
    MaterializationPreview,
    MaterializationRequest,
    MaterializerDescriptor,
    Outcome,
    OutcomeObservation,
    Proposal,
)
from aar.canonical import canonical_json_bytes, canonical_sha256


class AssetError(RuntimeError):
    """Base class for adaptive-asset operations that fail closed."""


class AssetConflict(AssetError):
    """A content address or unique semantic slot binds different bytes."""


class AssetReferenceMissing(AssetError):
    """An asset document or event references unavailable immutable content."""


class AssetMigrationUnavailable(AssetError):
    """No explicit migration maps an input schema to the current contract."""


class AssetMigrationNonDeterministic(AssetError):
    """A migration returned different canonical bytes for identical input."""


class SimulatedAssetProcessLoss(BaseException):
    """Test-only abrupt loss after an atomic import but before outer completion."""


class Materializer(Protocol):
    """Prepare-only materializer seam; activation remains host-authorized and absent."""

    def describe(self) -> MaterializerDescriptor: ...

    def prepare(
        self,
        request: MaterializationRequest,
        proposal: Proposal,
    ) -> MaterializationPreview: ...


class DeterministicPrepareOnlyMaterializer:
    """Concrete effect-free implementation of the public prepare-only seam."""

    def __init__(
        self,
        *,
        name: str = "aar.prepare-only",
        version: str = "1",
    ) -> None:
        capability_digest = canonical_sha256(
            {
                "contract": "aar.materializer.prepare-only.v1",
                "name": name,
                "version": version,
                "prepares_rollback": True,
                "activation_available": False,
            }
        )
        self._descriptor = MaterializerDescriptor(
            name=name,
            version=version,
            capability_digest=capability_digest,
            prepares_rollback=True,
        )

    def describe(self) -> MaterializerDescriptor:
        return self._descriptor

    def prepare(
        self,
        request: MaterializationRequest,
        proposal: Proposal,
    ) -> MaterializationPreview:
        proposal_reference = AdaptiveAssetDocument.issue(proposal).manifest.asset
        if request.proposal != proposal_reference:
            raise AssetConflict(
                "materialization request does not bind the supplied proposal bytes"
            )
        return MaterializationPreview(
            request_digest=canonical_sha256(request),
            candidate_digest=proposal.candidate_digest,
            rollback_digest=proposal.rollback_digest,
        )


AssetMigration = Callable[[dict[str, Any]], Mapping[str, Any]]


class AssetMigrationRegistry:
    """Pure, detached migrations from explicitly named legacy document schemas."""

    def __init__(self) -> None:
        self._migrations: dict[str, AssetMigration] = {}

    def register(self, source_schema_version: str, migration: AssetMigration) -> None:
        if source_schema_version == ADAPTIVE_ASSET_SCHEMA_VERSION:
            raise ValueError("current asset schema cannot be registered as a migration source")
        if source_schema_version in self._migrations:
            raise ValueError(f"migration already registered for {source_schema_version}")
        self._migrations[source_schema_version] = migration

    def migrate(self, payload: bytes | str) -> AdaptiveAssetDocument:
        raw = json.loads(payload)
        if not isinstance(raw, dict):
            raise AssetMigrationUnavailable("asset migration input must be a JSON object")
        manifest = raw.get("manifest")
        if isinstance(manifest, dict) and manifest.get("schema_version") == (
            ADAPTIVE_ASSET_SCHEMA_VERSION
        ):
            return AdaptiveAssetDocument.model_validate_json(
                canonical_json_bytes(raw), strict=True
            )
        source = raw.get("schema_version")
        if not isinstance(source, str) or source not in self._migrations:
            raise AssetMigrationUnavailable(f"no migration registered for {source!r}")
        migration = self._migrations[source]
        first = migration(copy.deepcopy(raw))
        second = migration(copy.deepcopy(raw))
        if canonical_json_bytes(first) != canonical_json_bytes(second):
            raise AssetMigrationNonDeterministic(
                f"migration from {source} returned different canonical bytes"
            )
        return AdaptiveAssetDocument.model_validate_json(
            canonical_json_bytes(first), strict=True
        )


def _reference_key(reference: AdaptiveAssetRef) -> tuple[str, str]:
    return reference.kind, reference.digest


def _document_references(document: AdaptiveAssetDocument) -> tuple[AdaptiveAssetRef, ...]:
    references = set(document.manifest.dependencies)
    if document.manifest.previous is not None:
        references.add(document.manifest.previous)
    return tuple(sorted(references, key=_reference_key))


def _event_references(event: AdaptiveAssetEvent) -> tuple[AdaptiveAssetRef, ...]:
    references = set(event.assets)
    if event.agent is not None:
        references.add(event.agent)
    if event.episode is not None:
        references.add(event.episode)
    if event.outcome is not None:
        references.add(event.outcome)
    for attribution in event.attributions:
        references.add(attribution.source)
        references.add(attribution.target)
    return tuple(sorted(references, key=_reference_key))


class AdaptiveAssetStore:
    """SQLite-backed immutable catalog with no serving activation surface."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.resolve()
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(
            self.database_path, isolation_level="IMMEDIATE"
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS asset_bodies (
                body_digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                schema_version TEXT NOT NULL,
                body_json TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS asset_manifests (
                manifest_digest TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                body_digest TEXT NOT NULL,
                document_json TEXT NOT NULL,
                FOREIGN KEY(body_digest) REFERENCES asset_bodies(body_digest)
            );

            CREATE TABLE IF NOT EXISTS asset_events (
                event_digest TEXT PRIMARY KEY,
                event_kind TEXT NOT NULL,
                episode_digest TEXT,
                event_json TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS one_outcome_event_per_episode
            ON asset_events(episode_digest)
            WHERE event_kind = 'outcome';
            """
        )

    def put(self, document: AdaptiveAssetDocument) -> AdaptiveAssetRef:
        self.import_bundle(AdaptiveAssetBundle.issue(documents=(document,)))
        return document.manifest.asset

    def get(self, reference: AdaptiveAssetRef) -> AdaptiveAssetDocument:
        row = self._connection.execute(
            """
            SELECT kind, document_json FROM asset_manifests
            WHERE manifest_digest = ?
            """,
            (reference.digest,),
        ).fetchone()
        if row is None:
            raise KeyError(reference.digest)
        if row["kind"] != reference.kind:
            raise AssetConflict("asset digest exists under a different kind")
        return AdaptiveAssetDocument.model_validate_json(
            str(row["document_json"]), strict=True
        )

    def exists(self, reference: AdaptiveAssetRef) -> bool:
        row = self._connection.execute(
            "SELECT kind FROM asset_manifests WHERE manifest_digest = ?",
            (reference.digest,),
        ).fetchone()
        return row is not None and row["kind"] == reference.kind

    def append_event(self, event: AdaptiveAssetEvent) -> None:
        self.import_bundle(AdaptiveAssetBundle.issue(documents=(), events=(event,)))

    def outcome(self, episode: AdaptiveAssetRef) -> OutcomeObservation:
        if episode.kind != "episode":
            raise ValueError("outcome lookup requires an episode asset")
        if not self.exists(episode):
            raise AssetReferenceMissing(f"episode asset is unavailable: {episode.digest}")
        row = self._connection.execute(
            """
            SELECT event_json FROM asset_events
            WHERE event_kind = 'outcome' AND episode_digest = ?
            """,
            (episode.digest,),
        ).fetchone()
        if row is None:
            return OutcomeObservation(status="unknown", episode=episode)
        event = AdaptiveAssetEvent.model_validate_json(str(row["event_json"]), strict=True)
        assert event.outcome is not None
        return OutcomeObservation(status="observed", episode=episode, outcome=event.outcome)

    def export_bundle(
        self,
        roots: tuple[AdaptiveAssetRef, ...],
        *,
        include_events: bool = True,
    ) -> AdaptiveAssetBundle:
        documents: dict[str, AdaptiveAssetDocument] = {}
        pending = list(sorted(roots, key=_reference_key, reverse=True))
        while pending:
            reference = pending.pop()
            if reference.digest in documents:
                if documents[reference.digest].manifest.asset.kind != reference.kind:
                    raise AssetConflict("one digest was requested under different asset kinds")
                continue
            document = self.get(reference)
            documents[reference.digest] = document
            pending.extend(
                sorted(_document_references(document), key=_reference_key, reverse=True)
            )
        events: tuple[AdaptiveAssetEvent, ...] = ()
        if include_events:
            available = set(documents)
            candidates = tuple(
                AdaptiveAssetEvent.model_validate_json(str(row["event_json"]), strict=True)
                for row in self._connection.execute(
                    "SELECT event_json FROM asset_events ORDER BY event_digest"
                ).fetchall()
            )
            events = tuple(
                event
                for event in candidates
                if all(ref.digest in available for ref in _event_references(event))
            )
        return AdaptiveAssetBundle.issue(
            documents=tuple(documents.values()),
            events=events,
        )

    def import_bundle(self, bundle: AdaptiveAssetBundle) -> AssetImportResult:
        documents_by_digest = {
            item.manifest.asset.digest: item for item in bundle.documents
        }
        with self._lock, self._connection:
            available: dict[str, str] = {
                str(row["manifest_digest"]): str(row["kind"])
                for row in self._connection.execute(
                    "SELECT manifest_digest, kind FROM asset_manifests"
                ).fetchall()
            }
            for digest, document in documents_by_digest.items():
                kind = document.manifest.asset.kind
                existing_kind = available.get(digest)
                if existing_kind is not None and existing_kind != kind:
                    raise AssetConflict("asset digest exists under a different kind")
                available[digest] = kind

            for document in bundle.documents:
                for reference in _document_references(document):
                    if available.get(reference.digest) != reference.kind:
                        raise AssetReferenceMissing(
                            "asset dependency is unavailable or kind-conflicting: "
                            f"{reference.kind}:{reference.digest}"
                        )

            for event in bundle.events:
                for reference in _event_references(event):
                    if available.get(reference.digest) != reference.kind:
                        raise AssetReferenceMissing(
                            "event reference is unavailable or kind-conflicting: "
                            f"{reference.kind}:{reference.digest}"
                        )
                self._validate_outcome_event(event, documents_by_digest)

            for document in bundle.documents:
                self._insert_document(document)

            for event in bundle.events:
                self._insert_event(event)

        return AssetImportResult(
            bundle_digest=bundle.bundle_digest,
            asset_count=len(bundle.documents),
            event_count=len(bundle.events),
        )

    def counts(self) -> tuple[int, int]:
        assets = self._connection.execute(
            "SELECT COUNT(*) AS count FROM asset_manifests"
        ).fetchone()
        events = self._connection.execute(
            "SELECT COUNT(*) AS count FROM asset_events"
        ).fetchone()
        assert assets is not None and events is not None
        return int(assets["count"]), int(events["count"])

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def _insert_document(self, document: AdaptiveAssetDocument) -> bool:
        body_json = canonical_json_bytes(document.body).decode()
        document_json = canonical_json_bytes(document).decode()
        body = self._connection.execute(
            "SELECT kind, schema_version, body_json FROM asset_bodies WHERE body_digest = ?",
            (document.manifest.body_digest,),
        ).fetchone()
        if body is not None and (
            body["kind"] != document.body.asset_kind
            or body["schema_version"] != document.body.schema_version
            or body["body_json"] != body_json
        ):
            raise AssetConflict("asset body digest binds different canonical bytes")
        manifest = self._connection.execute(
            "SELECT kind, body_digest, document_json FROM asset_manifests "
            "WHERE manifest_digest = ?",
            (document.manifest.manifest_digest,),
        ).fetchone()
        if manifest is not None:
            if (
                manifest["kind"] != document.manifest.asset.kind
                or manifest["body_digest"] != document.manifest.body_digest
                or manifest["document_json"] != document_json
            ):
                raise AssetConflict("asset manifest digest binds different canonical bytes")
            return False
        if body is None:
            self._connection.execute(
                """
                INSERT INTO asset_bodies(body_digest, kind, schema_version, body_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    document.manifest.body_digest,
                    document.body.asset_kind,
                    document.body.schema_version,
                    body_json,
                ),
            )
        self._connection.execute(
            """
            INSERT INTO asset_manifests(manifest_digest, kind, body_digest, document_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                document.manifest.manifest_digest,
                document.manifest.asset.kind,
                document.manifest.body_digest,
                document_json,
            ),
        )
        return True

    def _insert_event(self, event: AdaptiveAssetEvent) -> bool:
        event_json = canonical_json_bytes(event).decode()
        existing = self._connection.execute(
            "SELECT event_json FROM asset_events WHERE event_digest = ?",
            (event.event_digest,),
        ).fetchone()
        if existing is not None:
            if existing["event_json"] != event_json:
                raise AssetConflict("asset event digest binds different canonical bytes")
            return False
        if event.kind == "outcome":
            assert event.episode is not None
            prior = self._connection.execute(
                """
                SELECT event_digest FROM asset_events
                WHERE event_kind = 'outcome' AND episode_digest = ?
                """,
                (event.episode.digest,),
            ).fetchone()
            if prior is not None:
                raise AssetConflict("episode already has a different explicit outcome event")
        self._connection.execute(
            """
            INSERT INTO asset_events(event_digest, event_kind, episode_digest, event_json)
            VALUES (?, ?, ?, ?)
            """,
            (
                event.event_digest,
                event.kind,
                None if event.episode is None else event.episode.digest,
                event_json,
            ),
        )
        return True

    def _validate_outcome_event(
        self,
        event: AdaptiveAssetEvent,
        incoming: dict[str, AdaptiveAssetDocument],
    ) -> None:
        if event.kind != "outcome":
            return
        assert event.episode is not None and event.outcome is not None
        outcome_document = incoming.get(event.outcome.digest)
        if outcome_document is None:
            outcome_document = self.get(event.outcome)
        body = outcome_document.body
        if not isinstance(body, Outcome):
            raise AssetConflict("outcome event reference does not resolve to an outcome body")
        if body.episode != event.episode:
            raise AssetConflict("outcome event episode disagrees with outcome asset")
