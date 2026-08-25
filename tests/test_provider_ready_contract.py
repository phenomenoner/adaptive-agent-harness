from __future__ import annotations

import ast
import importlib
import importlib.util
import inspect
import json
from pathlib import PurePath

import pytest
from pydantic import ValidationError

from aar.canonical import canonical_sha256
from aar.provider_ready_contract import (
    PROVIDER_READY_SCHEMA_MODELS,
    PROVIDER_READY_SCHEMA_ORDER,
    ProviderReadyBomError,
    ProviderReadyContractError,
    ProviderReadyDuplicateKeyError,
    ProviderReadyFloatError,
    ProviderReadyInputTypeError,
    ProviderReadyJsonSyntaxError,
    ProviderReadyJsonTrailingDataError,
    ProviderReadyNonfiniteError,
    ProviderReadyNonObjectError,
    ProviderReadySchemaMismatchError,
    ProviderReadySchemaMissingError,
    ProviderReadySchemaTypeError,
    ProviderReadySchemaUnknownError,
    ProviderReadyUtf8Error,
    load_provider_ready_json_bytes,
    validate_provider_ready_document_bytes,
)
from aar.provider_ready_evaluation_models import (
    EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
    PROVIDER_READY_EVALUATION_SCHEMA_MODELS,
    EvaluationEvidenceClassification,
)
from aar.provider_ready_models import (
    METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
    GrantBudgetCeiling,
    MethodAdapterManifest,
)
from aar.provider_ready_models import PROVIDER_READY_SCHEMA_MODELS as A1A_SCHEMA_MODELS
from aar.provider_ready_operator_models import (
    ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
    CUTOVER_PLAN_SCHEMA_VERSION,
    PROVIDER_READY_OPERATOR_SCHEMA_MODELS,
    ActivationGenerationAuthority,
    CandidateBinding,
    CutoverPlan,
    DatabaseChecks,
    EpochOwnerBinding,
    FileArtifact,
    NonterminalCounts,
    SidecarObservation,
    SnapshotPlan,
)
from aar.provider_ready_runtime_models import (
    PROVIDER_READY_RUNTIME_SCHEMA_MODELS,
    WORKBENCH_GRANT_SET_SCHEMA_VERSION,
    WorkbenchGrantSet,
)

DIGEST = canonical_sha256({"fixture": "a1c-r"})
AUTHORITY_STORE_ID = "local-file-authority-v1:runtime-primary"

EXPECTED_ERROR_CODES = {
    "ProviderReadyInputTypeError": "INPUT_TYPE",
    "ProviderReadyBomError": "UTF8_BOM",
    "ProviderReadyUtf8Error": "UTF8_DECODE",
    "ProviderReadyDuplicateKeyError": "DUPLICATE_KEY",
    "ProviderReadyFloatError": "FLOAT_NOT_ALLOWED",
    "ProviderReadyNonfiniteError": "NONFINITE_NOT_ALLOWED",
    "ProviderReadyJsonSyntaxError": "JSON_SYNTAX",
    "ProviderReadyJsonTrailingDataError": "JSON_TRAILING_DATA",
    "ProviderReadyNonObjectError": "NON_OBJECT_ROOT",
    "ProviderReadySchemaMissingError": "SCHEMA_VERSION_MISSING",
    "ProviderReadySchemaTypeError": "SCHEMA_VERSION_TYPE",
    "ProviderReadySchemaUnknownError": "SCHEMA_VERSION_UNKNOWN",
    "ProviderReadySchemaMismatchError": "SCHEMA_VERSION_MISMATCH",
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _redigest(payload: dict[str, object], digest_field: str) -> dict[str, object]:
    amended = dict(payload)
    amended.pop(digest_field, None)
    amended[digest_field] = canonical_sha256(amended)
    return amended


def _manifest(
    *,
    backend_kind: str = "native",
    reference_only: bool = False,
    method: str = "model.request",
) -> MethodAdapterManifest:
    suffix = method.replace(".", "-")
    return MethodAdapterManifest.issue(
        schema_version=METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION,
        method=method,  # type: ignore[arg-type]
        contract_id=f"aar.broker-contract.{suffix}.v2",
        request_schema_digest=DIGEST,
        response_schema_digest=DIGEST,
        backend_kind=backend_kind,  # type: ignore[arg-type]
        factory_id=f"aar.factory.{suffix}.v1",
        factory_digest=DIGEST,
        adapter_id=f"adapter-{suffix}",
        adapter_generation_policy="runtime_generation",
        reference_only=reference_only,
        evidence_tier="unknown" if reference_only else "host_receipt_bound",
        lookup_supported=True,
        cancel_supported=False,
    )


def _authority() -> ActivationGenerationAuthority:
    return ActivationGenerationAuthority.issue(
        schema_version=ACTIVATION_GENERATION_AUTHORITY_SCHEMA_VERSION,
        authority_store_id=AUTHORITY_STORE_ID,
        profile_id="hermes-caller-luna-max-v1",
        activation_generation=1,
        intent_digest=DIGEST,
        profile_digest=DIGEST,
        migration_attestation_digest=DIGEST,
        previous_activation_authority_digest=None,
    )


def _plan() -> CutoverPlan:
    owner = EpochOwnerBinding(
        authority_store_id=AUTHORITY_STORE_ID,
        operator_identity_digest=DIGEST,
        runtime_owner_state="absent",
        exclusive_lock_state="available",
    )
    database_file = FileArtifact(
        artifact_id="database-primary", digest=DIGEST, size_bytes=8192
    )
    return CutoverPlan.issue(
        schema_version=CUTOVER_PLAN_SCHEMA_VERSION,
        cutover_epoch="cutover-001",
        runtime_home_digest=DIGEST,
        database_identity="database-primary",
        database_file=database_file,
        owner=owner,
        source_registry_version=5,
        source_registry_schema_digest=DIGEST,
        canonical_v5_row_set_digest=DIGEST,
        wal=SidecarObservation(state="absent", file_identity=None, size_bytes=None, digest=None),
        shm=SidecarObservation(state="absent", file_identity=None, size_bytes=None, digest=None),
        database_checks=DatabaseChecks(integrity_result="ok", foreign_key_violation_count=0),
        nonterminal_counts=NonterminalCounts(
            operations=0,
            attempts=0,
            workbench_jobs=0,
            caller_tickets=0,
            workers=0,
        ),
        snapshot=SnapshotPlan(
            snapshot_id="snapshot-001",
            destination="/runtime/snapshots/snapshot-001.db",
            backup_mode="sqlite_backup",
        ),
        candidate=CandidateBinding(
            package_version="0.6.0a0",
            source_commit="0" * 40,
            wheel_digest=DIGEST,
            contract_manifest_digest=DIGEST,
            skill_digest=DIGEST,
        ),
        migration_sql_digest=DIGEST,
        activation_intent_digest=DIGEST,
        final_profile_output="/runtime/profiles/final.json",
        profile_id="hermes-caller-luna-max-v1",
        proposed_activation_generation=1,
        previous_activation_authority_digest=None,
        authority_store_id=AUTHORITY_STORE_ID,
        allowed_recovery_actions=("abort", "apply", "status"),
        created_at_unix_ms=100,
        expires_at_unix_ms=200,
    )


def _grant_set() -> WorkbenchGrantSet:
    return WorkbenchGrantSet.issue(
        schema_version=WORKBENCH_GRANT_SET_SCHEMA_VERSION,
        runtime_generation=1,
        activation_generation=1,
        profile_id="hermes-caller-luna-max-v1",
        profile_digest=DIGEST,
        activation_authority_digest=DIGEST,
        capability_digest=DIGEST,
        route_catalog_digest=DIGEST,
        principal_ids=("aar-eval-runner",),
        session_binding_policy="bind_exact_request_session",
        capabilities=("model.request",),
        budget_ceiling=GrantBudgetCeiling(
            wall_time_ms=1_000,
            model_requests=1,
            input_tokens=0,
            output_tokens=0,
            child_operations=0,
            artifact_bytes=0,
        ),
        max_ttl_ms=1_000,
    )


def _classification() -> EvaluationEvidenceClassification:
    return EvaluationEvidenceClassification.issue(
        schema_version=EVALUATION_EVIDENCE_CLASSIFICATION_SCHEMA_VERSION,
        run_id="aar-run-001",
        arm="aar",
        attempt_id="attempt-001",
        requested_provider="openai-codex",
        requested_model="gpt-5.6-luna",
        requested_reasoning="max",
        requested_fallback_policy="none",
        requested_cache_policy="disabled",
        effective_provider="openai-codex",
        effective_model="gpt-5.6-luna",
        effective_reasoning="max",
        effective_fallback_policy="none",
        effective_cache_policy="disabled",
        provider_tier="attested",
        model_tier="attested",
        reasoning_tier="attested",
        fallback_tier="attested",
        cache_tier="attested",
        route_qualification="aar_live_qualified",
        usage_tier="request_receipt",
        attempt_visibility="complete",
        contradiction_components=(),
        source_receipt_digests=(DIGEST,),
    )


def test_provider_ready_contract_module_exists_with_required_surface() -> None:
    module_spec = importlib.util.find_spec("aar.provider_ready_contract")
    assert module_spec is not None
    module = importlib.import_module("aar.provider_ready_contract")
    required = {
        "PROVIDER_READY_SCHEMA_MODELS",
        "PROVIDER_READY_SCHEMA_ORDER",
        "ProviderReadyContractError",
        "load_provider_ready_json_bytes",
        "reject_duplicate_object_keys",
        "validate_provider_ready_document_bytes",
    }
    assert required <= set(vars(module))


def test_canonical_registry_is_exact_four_source_union_and_sorted() -> None:
    source_registries = (
        A1A_SCHEMA_MODELS,
        PROVIDER_READY_OPERATOR_SCHEMA_MODELS,
        PROVIDER_READY_RUNTIME_SCHEMA_MODELS,
        PROVIDER_READY_EVALUATION_SCHEMA_MODELS,
    )
    expected = {
        **A1A_SCHEMA_MODELS,
        **PROVIDER_READY_OPERATOR_SCHEMA_MODELS,
        **PROVIDER_READY_RUNTIME_SCHEMA_MODELS,
        **PROVIDER_READY_EVALUATION_SCHEMA_MODELS,
    }
    assert len(source_registries) == 4
    assert tuple(len(registry) for registry in source_registries) == (3, 6, 2, 3)
    assert len(PROVIDER_READY_SCHEMA_MODELS) == 14
    assert expected == PROVIDER_READY_SCHEMA_MODELS
    assert tuple(sorted(PROVIDER_READY_SCHEMA_MODELS)) == PROVIDER_READY_SCHEMA_ORDER
    assert all(PROVIDER_READY_SCHEMA_MODELS[key] is model for key, model in expected.items())
    assert "IssuedWorkbenchGrant" not in PROVIDER_READY_SCHEMA_MODELS
    assert "CandidateBinding" not in PROVIDER_READY_SCHEMA_MODELS


def test_duplicate_source_key_uses_the_production_merge_helper() -> None:
    module = importlib.import_module("aar.provider_ready_contract")
    with pytest.raises(ValueError, match="duplicate provider-ready schema key: collision"):
        module._merge_schema_registries(  # type: ignore[attr-defined]
            {"collision": MethodAdapterManifest},
            {"collision": CutoverPlan},
        )


def test_model_modules_do_not_import_the_consumer() -> None:
    for module_name in (
        "aar.provider_ready_models",
        "aar.provider_ready_operator_models",
        "aar.provider_ready_runtime_models",
        "aar.provider_ready_evaluation_models",
    ):
        source = inspect.getsource(importlib.import_module(module_name))
        assert "provider_ready_contract" not in source


def test_error_subclasses_have_exact_codes_and_stable_prefixes() -> None:
    module = importlib.import_module("aar.provider_ready_contract")
    assert ProviderReadyContractError.__mro__[1] is ValueError
    for class_name, code in EXPECTED_ERROR_CODES.items():
        error_type = getattr(module, class_name)
        error = error_type("stable detail")
        assert isinstance(error, ProviderReadyContractError)
        assert error.code == code
        assert str(error).startswith(f"{code}:")


def _assert_loader_error(
    data: object,
    error_type: type[ProviderReadyContractError],
) -> ProviderReadyContractError:
    with pytest.raises(error_type) as caught:
        load_provider_ready_json_bytes(data)  # type: ignore[arg-type]
    error = caught.value
    assert error.code == error_type.code
    assert str(error).startswith(f"{error_type.code}:")
    return error


def _assert_dispatch_error(
    data: bytes,
    error_type: type[ProviderReadyContractError],
    *,
    expected_schema_id: str | None = None,
) -> ProviderReadyContractError:
    with pytest.raises(error_type) as caught:
        validate_provider_ready_document_bytes(data, expected_schema_id=expected_schema_id)
    error = caught.value
    assert error.code == error_type.code
    assert str(error).startswith(f"{error_type.code}:")
    return error


def test_loader_rejects_duplicate_root_and_nested_keys_even_when_values_match() -> None:
    _assert_loader_error(
        b'{"schema_version":"aar.method-adapter-manifest.v1",'
        b'"schema_version":"aar.method-adapter-manifest.v1"}',
        ProviderReadyDuplicateKeyError,
    )
    _assert_loader_error(
        b'{"schema_version":"aar.method-adapter-manifest.v1",'
        b'"nested":{"same":1,"same":1}}',
        ProviderReadyDuplicateKeyError,
    )


def test_loader_rejects_encoding_syntax_and_trailing_data_distinctly() -> None:
    _assert_loader_error(b"\xef\xbb\xbf{}", ProviderReadyBomError)
    _assert_loader_error(b'{"value":"\xff"}', ProviderReadyUtf8Error)
    _assert_loader_error(b'{"schema_version":}', ProviderReadyJsonSyntaxError)
    _assert_loader_error(b"{} {}", ProviderReadyJsonTrailingDataError)


@pytest.mark.parametrize("root", [None, True, False, 1, "text", []])
def test_loader_rejects_every_non_object_json_root(root: object) -> None:
    _assert_loader_error(_json_bytes(root), ProviderReadyNonObjectError)


def test_loader_rejects_float_and_nonfinite_values_before_dispatch() -> None:
    _assert_loader_error(b'{"schema_version":"unknown","value":1.5}', ProviderReadyFloatError)
    for token in (b"NaN", b"Infinity", b"-Infinity"):
        _assert_loader_error(
            b'{"schema_version":"unknown","value":' + token + b"}",
            ProviderReadyNonfiniteError,
        )


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        bytearray(b"{}"),
        memoryview(b"{}"),
        {"schema_version": "unknown"},
        PurePath("document.json"),
        type("BytesSubclass", (bytes,), {})(b"{}"),
    ],
)
def test_loader_accepts_only_exact_bytes(value: object) -> None:
    _assert_loader_error(value, ProviderReadyInputTypeError)


def test_loader_dispatch_precedence_is_raw_first_then_schema_then_pydantic() -> None:
    _assert_loader_error(
        b'{"schema_version":"not-known","schema_version":"not-known"}',
        ProviderReadyDuplicateKeyError,
    )
    _assert_loader_error(
        b'{"schema_version":"not-known","value":1.25}',
        ProviderReadyFloatError,
    )
    _assert_dispatch_error(b"{}", ProviderReadySchemaMissingError)
    _assert_dispatch_error(b'{"schema_version":1}', ProviderReadySchemaTypeError)
    _assert_dispatch_error(
        b'{"schema_version":"not-known"}',
        ProviderReadySchemaUnknownError,
    )

    mismatch = _manifest().model_dump(mode="json")
    mismatch["unexpected"] = True
    mismatch = _redigest(mismatch, "manifest_digest")
    _assert_dispatch_error(
        _json_bytes(mismatch),
        ProviderReadySchemaMismatchError,
        expected_schema_id="aar.host-activation-profile.v1",
    )

    invalid = _manifest().model_dump(mode="json")
    invalid["reference_only"] = "false"
    with pytest.raises(ValidationError) as caught:
        validate_provider_ready_document_bytes(
            _json_bytes(invalid), expected_schema_id=METHOD_ADAPTER_MANIFEST_SCHEMA_VERSION
        )
    assert not isinstance(caught.value, ProviderReadyContractError)
    assert caught.value.errors()[0]["loc"] == ("reference_only",)
    assert caught.value.errors()[0]["type"] == "bool_type"


def test_compound_malformed_duplicate_is_syntax_when_parser_cannot_reach_hook() -> None:
    _assert_loader_error(
        b'{"schema_version":"not-known","same":1,"same":}',
        ProviderReadyJsonSyntaxError,
    )


def test_one_valid_document_from_each_source_registry_round_trips_to_exact_type() -> None:
    documents = (
        (MethodAdapterManifest, _manifest()),
        (ActivationGenerationAuthority, _authority()),
        (WorkbenchGrantSet, _grant_set()),
        (EvaluationEvidenceClassification, _classification()),
    )
    for model_type, model in documents:
        parsed = validate_provider_ready_document_bytes(model.model_dump_json().encode())
        assert type(parsed) is model_type
        assert parsed.model_dump(mode="json") == model.model_dump(mode="json")


def test_each_source_registry_preserves_semantic_and_digest_negative_axes() -> None:
    semantic_cases = (
        (
            _manifest().model_dump(mode="json"),
            "manifest_digest",
            {"backend_kind": "reference", "reference_only": False},
            "reference backend",
        ),
        (
            _plan().model_dump(mode="json"),
            "plan_digest",
            {"authority_store_id": "local-file-authority-v1:other"},
            "plan authority_store_id",
        ),
        (
            _grant_set().model_dump(mode="json"),
            "grant_set_digest",
            {"principal_ids": ["z-runner", "a-runner"]},
            "principal_ids must be sorted",
        ),
        (
            _classification().model_dump(mode="json"),
            "classification_digest",
            {"route_qualification": "insufficient"},
            "route_qualification does not match",
        ),
    )
    for payload, digest_field, updates, expected_message in semantic_cases:
        changed = dict(payload)
        changed.update(updates)
        changed = _redigest(changed, digest_field)
        with pytest.raises(ValidationError, match=expected_message):
            validate_provider_ready_document_bytes(_json_bytes(changed))

    stale_cases = (
        (_manifest().model_dump(mode="json"), "manifest_digest", "manifest digest"),
        (_plan().model_dump(mode="json"), "plan_digest", "plan_digest"),
        (_grant_set().model_dump(mode="json"), "grant_set_digest", "grant set digest"),
        (
            _classification().model_dump(mode="json"),
            "classification_digest",
            "classification digest",
        ),
    )
    for payload, digest_field, expected_message in stale_cases:
        stale = dict(payload)
        stale[digest_field] = canonical_sha256({"stale": digest_field})
        with pytest.raises(ValidationError, match=expected_message):
            validate_provider_ready_document_bytes(_json_bytes(stale))


def test_json_arrays_are_reserialized_without_sorting_or_deduplication() -> None:
    valid = _grant_set().model_dump(mode="json")
    unsorted = dict(valid)
    unsorted["principal_ids"] = ["z-runner", "a-runner"]
    unsorted = _redigest(unsorted, "grant_set_digest")
    with pytest.raises(ValidationError) as unsorted_error:
        validate_provider_ready_document_bytes(_json_bytes(unsorted))
    assert "principal_ids" in str(unsorted_error.value)
    assert "tuple_type" not in {item["type"] for item in unsorted_error.value.errors()}

    duplicate = dict(valid)
    duplicate["principal_ids"] = ["aar-eval-runner", "aar-eval-runner"]
    duplicate = _redigest(duplicate, "grant_set_digest")
    with pytest.raises(ValidationError) as duplicate_error:
        validate_provider_ready_document_bytes(_json_bytes(duplicate))
    assert "principal_ids" in str(duplicate_error.value)
    assert "tuple_type" not in {item["type"] for item in duplicate_error.value.errors()}


def test_unknown_field_and_strict_scalar_errors_are_pydantic_and_precise() -> None:
    extra = _manifest().model_dump(mode="json")
    extra["unexpected"] = True
    extra = _redigest(extra, "manifest_digest")
    with pytest.raises(ValidationError) as extra_error:
        validate_provider_ready_document_bytes(_json_bytes(extra))
    assert extra_error.value.errors()[0]["loc"] == ("unexpected",)
    assert extra_error.value.errors()[0]["type"] == "extra_forbidden"

    strict_bool = _manifest().model_dump(mode="json")
    strict_bool["reference_only"] = "false"
    strict_bool = _redigest(strict_bool, "manifest_digest")
    with pytest.raises(ValidationError) as bool_error:
        validate_provider_ready_document_bytes(_json_bytes(strict_bool))
    assert bool_error.value.errors()[0]["loc"] == ("reference_only",)
    assert bool_error.value.errors()[0]["type"] == "bool_type"


def test_contract_has_only_one_way_pure_imports_and_no_external_authority() -> None:
    module = importlib.import_module("aar.provider_ready_contract")
    source = inspect.getsource(module)
    tree = ast.parse(source)
    imported_modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    }
    imported_modules.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    assert imported_modules <= {
        "__future__",
        "json",
        "collections.abc",
        "typing",
        "aar.canonical",
        "aar.provider_ready_evaluation_models",
        "aar.provider_ready_models",
        "aar.provider_ready_operator_models",
        "aar.provider_ready_runtime_models",
        "aar.schemas",
    }
    for forbidden in (
        "aar.contract",
        "aar.schema_generator",
        "aar.schema_profile",
        "aar.canonical.py",  # protects against a path-loader spelling
        "subprocess",
        "socket",
        "requests",
        "sqlite3",
        "os.environ",
        "open(",
        "Path(",
        "time.",
        "network",
        "provider call",
    ):
        assert forbidden not in source
