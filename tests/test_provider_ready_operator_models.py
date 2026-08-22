from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from typing import Any, TypeVar

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_operator_models import (
    ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
    CUTOVER_PLAN_SCHEMA_VERSION,
    CUTOVER_RECEIPT_SCHEMA_VERSION,
    OPERATOR_AUTHORITY_SCHEMA_MODELS,
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
from aar.schemas import StrictModel

DIGEST = canonical_sha256({"fixture": "operator-authority"})
MAX_COUNTER = 9_223_372_036_854_775_807
AUTHORITY_STORE_ID = "local-file-authority-v1:runtime-primary"


def _file(*, artifact_id: str = "snapshot-001", size_bytes: int = 4096) -> FileArtifact:
    return FileArtifact(artifact_id=artifact_id, digest=DIGEST, size_bytes=size_bytes)


def _absent_sidecar() -> SidecarObservation:
    return SidecarObservation(state="absent", file_identity=None, size_bytes=None, digest=None)


def _present_sidecar(name: str = "runtime-wal") -> SidecarObservation:
    return SidecarObservation(
        state="present",
        file_identity=name,
        size_bytes=128,
        digest=DIGEST,
    )


def _checks() -> DatabaseChecks:
    return DatabaseChecks(integrity_result="ok", foreign_key_violation_count=0)


def _candidate() -> CandidateBinding:
    return CandidateBinding(
        package_version="0.6.0a0",
        source_commit="0" * 40,
        wheel_digest=DIGEST,
        contract_manifest_digest=DIGEST,
        skill_digest=DIGEST,
    )


def _owner() -> EpochOwnerBinding:
    return EpochOwnerBinding(
        authority_store_id=AUTHORITY_STORE_ID,
        operator_identity_digest=DIGEST,
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


def _snapshot_plan() -> SnapshotPlan:
    return SnapshotPlan(
        snapshot_id="snapshot-001",
        destination="/runtime/snapshots/snapshot-001.db",
        backup_mode="sqlite_backup",
    )


def _plan(
    *,
    allowed_recovery_actions: tuple[str, ...] = ("abort", "apply", "status"),
    wal: SidecarObservation | None = None,
    shm: SidecarObservation | None = None,
    created_at_unix_ms: int = 100,
    expires_at_unix_ms: int = 200,
) -> CutoverPlan:
    return CutoverPlan.issue(
        schema_version=CUTOVER_PLAN_SCHEMA_VERSION,
        cutover_epoch="cutover-001",
        runtime_home_digest=DIGEST,
        database_identity="database-primary",
        database_file=_file(artifact_id="database-primary", size_bytes=8192),
        owner=_owner(),
        source_registry_version=5,
        source_registry_schema_digest=DIGEST,
        canonical_v5_row_set_digest=DIGEST,
        wal=_absent_sidecar() if wal is None else wal,
        shm=_absent_sidecar() if shm is None else shm,
        database_checks=_checks(),
        nonterminal_counts=_counts(),
        snapshot=_snapshot_plan(),
        candidate=_candidate(),
        migration_sql_digest=DIGEST,
        activation_intent_digest=DIGEST,
        final_profile_output="/runtime/profiles/final.json",
        profile_id="hermes-caller-luna-max-v1",
        proposed_activation_generation=1,
        previous_activation_authority_digest=None,
        authority_store_id=AUTHORITY_STORE_ID,
        allowed_recovery_actions=allowed_recovery_actions,  # type: ignore[arg-type]
        created_at_unix_ms=created_at_unix_ms,
        expires_at_unix_ms=expires_at_unix_ms,
    )


def _cutover_preparation(plan: CutoverPlan | None = None) -> CutoverPreparation:
    plan = _plan() if plan is None else plan
    return CutoverPreparation(
        plan=plan,
        plan_digest=plan.plan_digest,
        snapshot=_file(artifact_id=plan.snapshot.snapshot_id),
        pre_migration_database_digest=plan.database_file.digest,
        wal=plan.wal,
        shm=plan.shm,
    )


def _restore_preparation() -> RestorePreparation:
    return RestorePreparation(
        cutover_plan_digest=DIGEST,
        cutover_prepared_marker_digest=DIGEST,
        cutover_terminal_marker_digest=DIGEST,
        snapshot=_file(),
        runtime_home_digest=DIGEST,
        target_database_identity="database-primary",
        pre_restore_database=_file(artifact_id="pre-restore-db"),
        pre_restore_wal=_absent_sidecar(),
        pre_restore_shm=_absent_sidecar(),
        no_committed_v6_attestation=True,
        no_activation_history_record=True,
        no_v6_runtime_or_workbench_write=True,
    )


def _cutover_prepared_marker(
    *,
    plan: CutoverPlan | None = None, prepared_at_unix_ms: int = 150
) -> OperatorPreparedMarker:
    plan = _plan() if plan is None else plan
    return OperatorPreparedMarker.issue(
        schema_version=OPERATOR_PREPARED_MARKER_SCHEMA_VERSION,
        kind="cutover",
        epoch=plan.cutover_epoch,
        operator_identity_digest=canonical_sha256({"operator": "apply"}),
        prepared_at_unix_ms=prepared_at_unix_ms,
        cutover=_cutover_preparation(plan),
        restore=None,
    )


def _restore_prepared_marker() -> OperatorPreparedMarker:
    return OperatorPreparedMarker.issue(
        schema_version=OPERATOR_PREPARED_MARKER_SCHEMA_VERSION,
        kind="restore",
        epoch="restore-001",
        operator_identity_digest=canonical_sha256({"operator": "restore"}),
        prepared_at_unix_ms=150,
        cutover=None,
        restore=_restore_preparation(),
    )


def _cutover_receipt(
    *,
    outcome: str = "committed",
    database_commit_state: str | None = None,
    committed: bool | None = None,
) -> CutoverReceipt:
    if committed is not None:
        outcome = "committed" if committed else "aborted_before_db_commit"
    if database_commit_state is None:
        database_commit_state = "committed" if outcome == "committed" else "not_committed"
    is_committed = database_commit_state == "committed"
    return CutoverReceipt.issue(
        schema_version=CUTOVER_RECEIPT_SCHEMA_VERSION,
        cutover_epoch="cutover-001",
        plan_digest=DIGEST,
        outcome=outcome,  # type: ignore[arg-type]
        database_commit_state=database_commit_state,  # type: ignore[arg-type]
        prepared_marker_digest=DIGEST,
        snapshot=_file(),
        pre_migration_database_digest=DIGEST,
        canonical_v5_row_set_digest=DIGEST,
        migration_sql_digest=DIGEST,
        migration_attestation_digest=DIGEST if is_committed else None,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=1,
        previous_activation_authority_digest=None,
        activation_authority_digest=DIGEST if is_committed else None,
        post_migration_database_digest=DIGEST if is_committed else None,
        database_checks=_checks() if is_committed else None,
        candidate=_candidate(),
        intent_digest=DIGEST,
        generated_profile_digest=DIGEST if is_committed else None,
        generated_profile_output="/runtime/profiles/final.json",
        started_at_unix_ms=100,
        db_committed_at_unix_ms=150 if is_committed else None,
        completed_at_unix_ms=175,
    )


def _restore_receipt(
    *, outcome: str = "restored_pre_frontier", replacement_commit_state: str | None = None
) -> RestoreReceipt:
    if replacement_commit_state is None:
        replacement_commit_state = "committed" if outcome == "restored_pre_frontier" else "unknown"
    committed = replacement_commit_state == "committed"
    return RestoreReceipt.issue(
        schema_version=RESTORE_RECEIPT_SCHEMA_VERSION,
        restore_epoch="restore-001",
        cutover_plan_digest=DIGEST,
        outcome=outcome,  # type: ignore[arg-type]
        replacement_commit_state=replacement_commit_state,  # type: ignore[arg-type]
        snapshot=_file(),
        runtime_home_digest=DIGEST,
        target_database_identity="database-primary",
        pre_restore_database=_file(artifact_id="pre-restore-db"),
        pre_restore_wal=_absent_sidecar(),
        pre_restore_shm=_absent_sidecar(),
        restored_database_digest=DIGEST if committed else None,
        sidecar_disposition="unchanged",
        database_checks=_checks() if committed else None,
        cutover_prepared_marker_digest=DIGEST,
        cutover_terminal_marker_digest=DIGEST,
        restore_prepared_marker_digest=DIGEST,
        activation_authority_digest_before_restore=DIGEST,
        activation_authority_digest_after_restore=DIGEST,
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


def _terminal(
    receipt: CutoverReceipt | RestoreReceipt | None = None,
) -> OperatorTerminalMarker:
    receipt = _cutover_receipt() if receipt is None else receipt
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
    kind = (
        "restore_committed"
        if receipt.outcome == "restored_pre_frontier"
        else "restore_recovery_required"
    )
    return OperatorTerminalMarker.issue(
        schema_version=OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION,
        kind=kind,  # type: ignore[arg-type]
        epoch=receipt.restore_epoch,
        receipt_schema_version=RESTORE_RECEIPT_SCHEMA_VERSION,
        receipt=receipt,
        published_at_unix_ms=200,
    )


def _recompute_root(payload: dict[str, Any], digest_field: str) -> dict[str, Any]:
    amended = dict(payload)
    amended.pop(digest_field, None)
    amended[digest_field] = canonical_sha256(amended)
    return amended


def _schema_node(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        reference = node["$ref"]
        assert isinstance(reference, str)
        assert reference.startswith("#/$defs/")
        definitions = root["$defs"]
        assert isinstance(definitions, dict)
        node = definitions[reference.removeprefix("#/$defs/")]
        assert isinstance(node, dict)
    return node


def _schema_property(root: dict[str, Any], *path: str) -> dict[str, Any]:
    node = root
    for part in path:
        node = _schema_node(root, node)
        properties = node["properties"]
        assert isinstance(properties, dict)
        node = properties[part]
        assert isinstance(node, dict)
    return _schema_node(root, node)


def _schema_objects(root: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    seen: set[int] = set()

    def visit(node: object) -> None:
        if not isinstance(node, dict) or id(node) in seen:
            return
        seen.add(id(node))
        if node.get("type") == "object":
            found.append(node)
        for child in node.values():
            visit(child)

    visit(root)
    return found


def _model_payload(model: StrictModel) -> dict[str, Any]:
    payload = model.model_dump(mode="json")
    assert isinstance(payload, dict)
    return payload


ModelT = TypeVar("ModelT", bound=StrictModel)


def _validate_json(model_type: type[ModelT], payload: dict[str, Any]) -> ModelT:
    return model_type.model_validate_json(json.dumps(payload), strict=True)


def test_operator_model_module_exists_with_required_surface() -> None:
    module_spec = importlib.util.find_spec("aar.provider_ready_operator_models")
    assert module_spec is not None, "aar.provider_ready_operator_models module is missing"
    module = importlib.import_module("aar.provider_ready_operator_models")
    required = {
        "ActivationGenerationAuthority",
        "CutoverPlan",
        "OperatorPreparedMarker",
        "CutoverReceipt",
        "RestoreReceipt",
        "OperatorTerminalMarker",
    }
    assert required <= set(vars(module))


def test_all_nested_and_top_level_models_round_trip_and_registry_is_exact() -> None:
    models: tuple[StrictModel, ...] = (
        _file(),
        _absent_sidecar(),
        _present_sidecar(),
        _checks(),
        _candidate(),
        _owner(),
        _counts(),
        _snapshot_plan(),
        _plan(),
        _cutover_preparation(),
        RestoreFrontierProof(
            no_committed_v6_attestation=True,
            no_activation_history_record=True,
            no_v6_runtime_or_workbench_write=True,
        ),
        _restore_preparation(),
        _cutover_prepared_marker(),
        _cutover_receipt(),
        _restore_receipt(),
        _terminal(),
    )
    for model in models:
        assert type(model).model_validate_json(model.model_dump_json(), strict=True) == model

    assert {
        ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION: ActivationGenerationAuthority,
        CUTOVER_PLAN_SCHEMA_VERSION: CutoverPlan,
        OPERATOR_PREPARED_MARKER_SCHEMA_VERSION: OperatorPreparedMarker,
        CUTOVER_RECEIPT_SCHEMA_VERSION: CutoverReceipt,
        RESTORE_RECEIPT_SCHEMA_VERSION: RestoreReceipt,
        OPERATOR_TERMINAL_MARKER_SCHEMA_VERSION: OperatorTerminalMarker,
    } == OPERATOR_AUTHORITY_SCHEMA_MODELS


def test_every_required_wire_field_is_strict_and_unknown_fields_are_rejected() -> None:
    examples: tuple[tuple[type[StrictModel], StrictModel], ...] = (
        (FileArtifact, _file()),
        (SidecarObservation, _absent_sidecar()),
        (DatabaseChecks, _checks()),
        (CandidateBinding, _candidate()),
        (EpochOwnerBinding, _owner()),
        (NonterminalCounts, _counts()),
        (SnapshotPlan, _snapshot_plan()),
        (CutoverPlan, _plan()),
        (CutoverPreparation, _cutover_preparation()),
        (
            RestoreFrontierProof,
            RestoreFrontierProof(
                no_committed_v6_attestation=True,
                no_activation_history_record=True,
                no_v6_runtime_or_workbench_write=True,
            ),
        ),
        (RestorePreparation, _restore_preparation()),
        (OperatorPreparedMarker, _cutover_prepared_marker()),
        (CutoverReceipt, _cutover_receipt()),
        (RestoreReceipt, _restore_receipt()),
        (OperatorTerminalMarker, _terminal()),
        (
            ActivationGenerationAuthority,
            ActivationGenerationAuthority.issue(
                schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
                authority_store_id=AUTHORITY_STORE_ID,
                profile_id="hermes-caller-luna-max-v1",
                activation_generation=1,
                intent_digest=DIGEST,
                profile_digest=DIGEST,
                migration_attestation_digest=DIGEST,
                previous_activation_authority_digest=None,
            ),
        ),
    )
    for model_type, model in examples:
        for field_info in model_type.model_fields.values():
            assert field_info.is_required(), (model_type.__name__, field_info)
            assert field_info.default is None or str(field_info.default).endswith(
                "PydanticUndefined"
            )
        payload = _model_payload(model)
        payload["unexpected"] = True
        with pytest.raises(ValidationError):
            _validate_json(model_type, payload)
        for object_schema in _schema_objects(model_type.model_json_schema()):
            assert object_schema["additionalProperties"] is False
            assert set(object_schema["required"]) == set(object_schema["properties"])
            for property_schema in object_schema["properties"].values():
                assert "default" not in property_schema


def test_operator_path_scalars_sidecars_and_zero_counts_are_exact() -> None:
    with pytest.raises(ValidationError):
        FileArtifact(artifact_id="snapshot-001", digest=DIGEST, size_bytes=True)
    for path in ("", "a\x00b", "x" * 4097):
        with pytest.raises(ValidationError):
            SnapshotPlan(snapshot_id="snapshot-001", destination=path, backup_mode="sqlite_backup")

    assert _absent_sidecar().model_dump(mode="json") == {
        "state": "absent",
        "file_identity": None,
        "size_bytes": None,
        "digest": None,
    }
    assert _present_sidecar().file_identity == "runtime-wal"
    for values in (
        {"file_identity": "runtime-wal", "size_bytes": None, "digest": DIGEST},
        {"file_identity": None, "size_bytes": 1, "digest": DIGEST},
        {"file_identity": "runtime-wal", "size_bytes": 1, "digest": None},
    ):
        with pytest.raises(ValidationError):
            SidecarObservation(state="present", **values)
    for values in (
        {"file_identity": "runtime-wal", "size_bytes": None, "digest": None},
        {"file_identity": None, "size_bytes": 1, "digest": None},
        {"file_identity": None, "size_bytes": None, "digest": DIGEST},
    ):
        with pytest.raises(ValidationError):
            SidecarObservation(state="absent", **values)

    with pytest.raises(ValidationError):
        NonterminalCounts(operations=1, attempts=0, workbench_jobs=0, caller_tickets=0, workers=0)
    assert _counts().operations == 0


def test_candidate_binding_is_byte_equivalent_to_a1a_candidate() -> None:
    from aar.provider_ready_models import ProviderReadyCandidate

    candidate = _candidate()
    predecessor = ProviderReadyCandidate.model_validate(
        candidate.model_dump(mode="json"), strict=True
    )
    assert set(CandidateBinding.model_fields) == set(ProviderReadyCandidate.model_fields)
    assert candidate.model_dump(mode="json") == predecessor.model_dump(mode="json")


def test_allowed_recovery_actions_schema_emits_exact_bounds_and_enum() -> None:
    schema = CutoverPlan.model_json_schema()
    actions = _schema_property(schema, "allowed_recovery_actions")
    assert actions["minItems"] == 1
    assert actions["maxItems"] == 4
    item = _schema_node(schema, actions["items"])
    assert item["enum"] == ["abort", "apply", "reconcile", "status"]


def test_plan_canonical_collections_owner_binding_and_time_rules() -> None:
    assert _plan(allowed_recovery_actions=("abort",)).allowed_recovery_actions == ("abort",)
    assert _plan(
        allowed_recovery_actions=("status", "abort", "apply")
    ).allowed_recovery_actions == ("abort", "apply", "status")
    plan = _plan()
    for actions in (
        (),
        ("abort", "abort"),
        ("status", "abort"),
        ("abort", "apply", "reconcile", "status", "abort"),
    ):
        payload = _model_payload(plan)
        payload["allowed_recovery_actions"] = list(actions)
        payload = _recompute_root(payload, "plan_digest")
        with pytest.raises(ValidationError, match="allowed_recovery_actions"):
            _validate_json(CutoverPlan, payload)

    payload = _model_payload(plan)
    payload["authority_store_id"] = "local-file-authority-v1:other"
    payload = _recompute_root(payload, "plan_digest")
    with pytest.raises(ValidationError, match="owner authority_store_id"):
        _validate_json(CutoverPlan, payload)

    for created, expires in ((100, 100), (200, 100)):
        payload = _model_payload(plan)
        payload.update(created_at_unix_ms=created, expires_at_unix_ms=expires)
        payload = _recompute_root(payload, "plan_digest")
        with pytest.raises(ValidationError, match="creation time"):
            _validate_json(CutoverPlan, payload)


def test_activation_authority_has_acyclic_self_digest_and_nullable_prior_tip() -> None:
    absent = ActivationGenerationAuthority.issue(
        schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
        authority_store_id=AUTHORITY_STORE_ID,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=1,
        intent_digest=DIGEST,
        profile_digest=DIGEST,
        migration_attestation_digest=DIGEST,
        previous_activation_authority_digest=None,
    )
    prior = ActivationGenerationAuthority.issue(
        schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
        authority_store_id=AUTHORITY_STORE_ID,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=2,
        intent_digest=DIGEST,
        profile_digest=DIGEST,
        migration_attestation_digest=DIGEST,
        previous_activation_authority_digest=absent.authority_digest,
    )
    assert absent.previous_activation_authority_digest is None
    assert prior.previous_activation_authority_digest == absent.authority_digest
    assert absent.authority_digest != prior.authority_digest
    payload = _model_payload(absent)
    observed = payload.pop("authority_digest")
    assert observed == canonical_sha256(payload)
    payload["authority_digest"] = canonical_sha256({"wrong": True})
    with pytest.raises(ValidationError, match="authority_digest"):
        _validate_json(ActivationGenerationAuthority, payload)


def test_prepared_union_and_cutover_preparation_bindings_are_exhaustive() -> None:
    plan = _plan(created_at_unix_ms=100, expires_at_unix_ms=300)
    marker = _cutover_prepared_marker(plan=plan, prepared_at_unix_ms=200)
    assert marker.cutover is not None and marker.restore is None
    assert marker.epoch == plan.cutover_epoch
    restore_marker = _restore_prepared_marker()
    assert restore_marker.cutover is None and restore_marker.restore is not None

    prep_payload = _recompute_root(_model_payload(marker), "prepared_marker_digest")
    prep_payload["cutover"]["plan_digest"] = canonical_sha256({"changed": True})
    prep_payload = _recompute_root(prep_payload, "prepared_marker_digest")
    with pytest.raises(ValidationError, match="plan_digest"):
        _validate_json(OperatorPreparedMarker, prep_payload)

    marker_payload = _model_payload(marker)
    marker_payload["epoch"] = "other-epoch"
    marker_payload = _recompute_root(marker_payload, "prepared_marker_digest")
    with pytest.raises(ValidationError, match="epoch"):
        _validate_json(OperatorPreparedMarker, marker_payload)

    marker_payload = _model_payload(marker)
    marker_payload["prepared_at_unix_ms"] = 300
    marker_payload = _recompute_root(marker_payload, "prepared_marker_digest")
    with pytest.raises(ValidationError, match="validity window"):
        _validate_json(OperatorPreparedMarker, marker_payload)

    marker_payload = _model_payload(marker)
    marker_payload["restore"] = _model_payload(restore_marker.restore)
    marker_payload = _recompute_root(marker_payload, "prepared_marker_digest")
    with pytest.raises(ValidationError, match="cutover only"):
        _validate_json(OperatorPreparedMarker, marker_payload)

    preparation = _cutover_preparation(plan)
    for field_name, changed in (
        ("plan_digest", canonical_sha256({"changed": "plan"})),
        ("pre_migration_database_digest", canonical_sha256({"changed": "database"})),
    ):
        payload = _model_payload(preparation)
        payload[field_name] = changed
        with pytest.raises(ValidationError, match="match"):
            _validate_json(CutoverPreparation, payload)
    payload = _model_payload(preparation)
    payload["snapshot"]["artifact_id"] = "different-snapshot"
    with pytest.raises(ValidationError, match="snapshot_id"):
        _validate_json(CutoverPreparation, payload)


def test_restore_preparation_frontier_is_flat_and_receipt_proof_is_nested() -> None:
    preparation = _restore_preparation()
    assert "frontier_proof" not in preparation.model_dump(mode="json")
    assert set(RestorePreparation.model_fields) == {
        "cutover_plan_digest",
        "cutover_prepared_marker_digest",
        "cutover_terminal_marker_digest",
        "snapshot",
        "runtime_home_digest",
        "target_database_identity",
        "pre_restore_database",
        "pre_restore_wal",
        "pre_restore_shm",
        "no_committed_v6_attestation",
        "no_activation_history_record",
        "no_v6_runtime_or_workbench_write",
    }
    assert set(RestoreFrontierProof.model_fields) == {
        "no_committed_v6_attestation",
        "no_activation_history_record",
        "no_v6_runtime_or_workbench_write",
    }
    for field_name in (
        "no_committed_v6_attestation",
        "no_activation_history_record",
        "no_v6_runtime_or_workbench_write",
    ):
        payload = _model_payload(preparation)
        payload[field_name] = False
        with pytest.raises(ValidationError):
            _validate_json(RestorePreparation, payload)


def test_cutover_receipt_outcome_matrix_requires_digest_recomputed_negatives() -> None:
    committed = _cutover_receipt()
    payload = _model_payload(committed)
    payload["database_commit_state"] = "not_committed"
    payload["migration_attestation_digest"] = None
    payload["activation_authority_digest"] = None
    payload["post_migration_database_digest"] = None
    payload["database_checks"] = None
    payload["generated_profile_digest"] = None
    payload["db_committed_at_unix_ms"] = None
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="committed receipt"):
        _validate_json(CutoverReceipt, payload)

    aborted = _cutover_receipt(committed=False)
    payload = _model_payload(aborted)
    payload["migration_attestation_digest"] = DIGEST
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="post-commit observations"):
        _validate_json(CutoverReceipt, payload)

    recovery = _cutover_receipt(outcome="recovery_required", database_commit_state="unknown")
    assert recovery.database_commit_state == "unknown"
    payload = _model_payload(recovery)
    payload["database_commit_state"] = "not_committed"
    payload["post_migration_database_digest"] = DIGEST
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="not_committed recovery"):
        _validate_json(CutoverReceipt, payload)

    payload = _model_payload(committed)
    payload["started_at_unix_ms"] = 176
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="completion time"):
        _validate_json(CutoverReceipt, payload)

    stale = _model_payload(committed)
    stale["receipt_digest"] = canonical_sha256({"stale": True})
    with pytest.raises(ValidationError, match="receipt_digest"):
        _validate_json(CutoverReceipt, stale)


def test_restore_receipt_outcome_frontier_and_authority_matrix() -> None:
    restored = _restore_receipt()
    assert restored.replacement_commit_state == "committed"
    payload = _model_payload(restored)
    payload["replacement_commit_state"] = "not_committed"
    payload["restored_database_digest"] = None
    payload["database_checks"] = None
    payload["replacement_committed_at_unix_ms"] = None
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="restored_pre_frontier"):
        _validate_json(RestoreReceipt, payload)

    recovery = _restore_receipt(outcome="recovery_required")
    assert recovery.replacement_commit_state == "unknown"
    payload = _model_payload(recovery)
    payload["replacement_commit_state"] = "not_committed"
    payload["restored_database_digest"] = DIGEST
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="not_committed restore"):
        _validate_json(RestoreReceipt, payload)

    payload = _model_payload(restored)
    payload["activation_authority_digest_after_restore"] = canonical_sha256({"different": True})
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="equal activation authority"):
        _validate_json(RestoreReceipt, payload)

    payload = _model_payload(recovery)
    payload["activation_authority_digest_after_restore"] = canonical_sha256({"different": True})
    payload = _recompute_root(payload, "receipt_digest")
    with pytest.raises(ValidationError, match="rollback"):
        _validate_json(RestoreReceipt, payload)


def test_terminal_marker_maps_every_receipt_outcome_and_binds_epoch_time_type() -> None:
    cases = (
        (_cutover_receipt(), "cutover_committed"),
        (_cutover_receipt(committed=False), "cutover_aborted"),
        (
            _cutover_receipt(
                outcome="recovery_required", database_commit_state="unknown"
            ),
            "cutover_recovery_required",
        ),
        (_restore_receipt(), "restore_committed"),
        (_restore_receipt(outcome="recovery_required"), "restore_recovery_required"),
    )
    for receipt, kind in cases:
        marker = _terminal(receipt)
        assert marker.kind == kind
        assert marker.epoch == (
            receipt.cutover_epoch if isinstance(receipt, CutoverReceipt) else receipt.restore_epoch
        )
        assert marker.published_at_unix_ms >= receipt.completed_at_unix_ms

    marker = _terminal()
    payload = _model_payload(marker)
    payload["kind"] = "cutover_aborted"
    payload = _recompute_root(payload, "marker_digest")
    with pytest.raises(ValidationError, match="terminal kind"):
        _validate_json(OperatorTerminalMarker, payload)

    payload = _model_payload(marker)
    payload["epoch"] = "other-epoch"
    payload = _recompute_root(payload, "marker_digest")
    with pytest.raises(ValidationError, match="terminal epoch"):
        _validate_json(OperatorTerminalMarker, payload)

    payload = _model_payload(marker)
    payload["published_at_unix_ms"] = 174
    payload = _recompute_root(payload, "marker_digest")
    with pytest.raises(ValidationError, match="publication"):
        _validate_json(OperatorTerminalMarker, payload)

    payload = _model_payload(marker)
    payload["receipt_schema_version"] = RESTORE_RECEIPT_SCHEMA_VERSION
    payload = _recompute_root(payload, "marker_digest")
    with pytest.raises(ValidationError, match="restore receipt_schema_version"):
        _validate_json(OperatorTerminalMarker, payload)

    restore_marker = _terminal(_restore_receipt())
    payload = _model_payload(restore_marker)
    payload["receipt_schema_version"] = CUTOVER_RECEIPT_SCHEMA_VERSION
    payload = _recompute_root(payload, "marker_digest")
    with pytest.raises(ValidationError, match="cutover receipt_schema_version"):
        _validate_json(OperatorTerminalMarker, payload)

    stale = _model_payload(marker)
    stale["marker_digest"] = canonical_sha256({"stale": True})
    with pytest.raises(ValidationError, match="marker_digest"):
        _validate_json(OperatorTerminalMarker, stale)


def test_self_digest_oracles_are_independent_and_nested_changes_propagate() -> None:
    authority = ActivationGenerationAuthority.issue(
        schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
        authority_store_id=AUTHORITY_STORE_ID,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=1,
        intent_digest=DIGEST,
        profile_digest=DIGEST,
        migration_attestation_digest=DIGEST,
        previous_activation_authority_digest=None,
    )
    documents = (
        (authority, "authority_digest"),
        (_plan(), "plan_digest"),
        (_cutover_prepared_marker(), "prepared_marker_digest"),
        (_cutover_receipt(), "receipt_digest"),
        (_restore_receipt(), "receipt_digest"),
        (_terminal(), "marker_digest"),
    )
    for document, digest_field in documents:
        payload = _model_payload(document)
        observed = payload.pop(digest_field)
        assert "schema_version" in payload
        assert observed == canonical_sha256(payload)

    original_plan = _plan()
    changed_plan_payload = _model_payload(original_plan)
    changed_plan_payload["profile_id"] = "different-profile"
    changed_plan_payload = _recompute_root(changed_plan_payload, "plan_digest")
    changed_plan = _validate_json(CutoverPlan, changed_plan_payload)
    assert changed_plan.plan_digest != original_plan.plan_digest
    original_marker = _cutover_prepared_marker(plan=original_plan)
    changed_marker = _cutover_prepared_marker(plan=changed_plan)
    assert changed_marker.prepared_marker_digest != original_marker.prepared_marker_digest
    original_receipt = _cutover_receipt()
    changed_receipt_payload = _model_payload(original_receipt)
    changed_receipt_payload["plan_digest"] = changed_plan.plan_digest
    changed_receipt_payload = _recompute_root(changed_receipt_payload, "receipt_digest")
    changed_receipt = _validate_json(CutoverReceipt, changed_receipt_payload)
    assert changed_receipt.receipt_digest != original_receipt.receipt_digest
    original_terminal = _terminal(original_receipt)
    changed_terminal_payload = _model_payload(original_terminal)
    changed_terminal_payload["receipt"] = _model_payload(changed_receipt)
    changed_terminal_payload = _recompute_root(changed_terminal_payload, "marker_digest")
    changed_terminal = _validate_json(OperatorTerminalMarker, changed_terminal_payload)
    assert changed_terminal.marker_digest != original_terminal.marker_digest


def test_schema_literals_shape_and_inert_import_boundary_are_frozen() -> None:
    module = importlib.import_module("aar.provider_ready_operator_models")
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    imported_names = {
        alias.name
        for node in imports
        for alias in node.names
        if alias.name.startswith("aar.") or alias.name in {"subprocess", "socket", "requests"}
    }
    assert imported_names <= {
        "aar.canonical",
        "aar.provider_ready_models",
        "aar.schemas",
    }
    for forbidden in (
        "aar.runtime",
        "aar.providers",
        "aar.mcp",
        "sqlite3",
        "subprocess",
        "socket",
        "requests",
        "os.environ",
        "time.",
        "open(",
    ):
        assert forbidden not in source
    assert set(ActivationGenerationAuthority.model_fields) == {
        "schema_version",
        "authority_store_id",
        "profile_id",
        "activation_generation",
        "intent_digest",
        "profile_digest",
        "migration_attestation_digest",
        "previous_activation_authority_digest",
        "authority_digest",
    }
    assert "receipt" not in CutoverReceipt.model_fields
    assert "terminal_marker_digest" not in CutoverReceipt.model_fields
    assert "receipt" not in RestoreReceipt.model_fields
    assert "terminal_marker_digest" not in RestoreReceipt.model_fields
    assert "receipt" not in OperatorPreparedMarker.model_fields
