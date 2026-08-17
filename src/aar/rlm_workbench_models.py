"""Successor-only AR-RW contract values backed by the reviewed v2 schemas."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from functools import cache, lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, ClassVar

from jsonschema import Draft202012Validator
from pydantic import ConfigDict, RootModel, model_validator
from referencing import Registry, Resource

from aar.canonical import canonical_json_bytes, canonical_sha256
from aar.schema_profile import validate_schema_profile

_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_CONTRACT_FILES = (
    "aar-acceptance-evidence-v1.schema.json",
    "aar-artifact-publication-v1.schema.json",
    "aar-caller-work-v1.schema.json",
    "aar-migration-cutover-v1.schema.json",
    "aar-rlm-workbench-v1.schema.json",
    "aar-workspace-broker-frame-v1.schema.json",
)


class _DocumentView:
    """Read-only attribute view over a nested reviewed contract object."""

    __slots__ = ("_value",)

    def __init__(self, value: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_value", value)

    def __getattr__(self, name: str) -> Any:
        try:
            value = self._value[name]
        except KeyError as error:
            raise AttributeError(name) from error
        return _view(value)

    def __getitem__(self, name: str) -> Any:
        return _view(self._value[name])

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        del mode
        return copy.deepcopy(dict(self._value))


class ContractDocument(RootModel[dict[str, Any]]):
    """Immutable Pydantic document validated against one published definition."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )

    schema_file: ClassVar[str]
    definition: ClassVar[str]

    @model_validator(mode="before")
    @classmethod
    def reviewed_schema_accepts_document(cls, value: Any) -> Any:
        if isinstance(value, cls):
            value = value.root
        if not isinstance(value, dict):
            raise ValueError("contract document must be an object")
        document = copy.deepcopy(value)
        validate_contract_document(cls.schema_file, cls.definition, document)
        cls.semantic_validate(document)
        return document

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        del document

    def __getattr__(self, name: str) -> Any:
        try:
            value = self.root[name]
        except KeyError as error:
            raise AttributeError(name) from error
        return _view(value)


class RlmWorkbenchExecuteInput(ContractDocument):
    schema_file = "aar-rlm-workbench-v1.schema.json"
    definition = "RlmWorkbenchExecuteInput"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        spec = document["spec"]
        model = spec["model"]
        if model["execution_mode"] == "caller_delegated" and document["start_only"] is not True:
            raise ValueError("caller-delegated execution requires start_only=true")

        route = model["route_binding"]
        profile = {
            key: route[key]
            for key in (
                "schema_version",
                "profile_id",
                "provider_driver",
                "provider",
                "model",
                "reasoning_effort",
                "max_output_tokens",
                "fallback_policy",
                "cache_policy",
            )
        }
        if route["profile_digest"] != canonical_sha256(profile):
            raise ValueError("route profile digest mismatch")
        if route["fallback_policy"] != "none":
            raise ValueError("workbench route fallback must be none")

        _validate_json_contract(spec["completion"]["output_contract"])


class RlmWorkbenchCapability(ContractDocument):
    schema_file = "aar-rlm-workbench-v1.schema.json"
    definition = "RlmWorkbenchCapability"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        combined = _load_projection("aar-mcp-tools-v8-combined.json")
        catalog = _load_projection("aar-broker-catalog-v2.json")
        workbench = _contract_documents()["aar-rlm-workbench-v1.schema.json"]
        planner_digest = canonical_sha256(workbench["$defs"]["RlmDirective"])
        catalog_digest = canonical_sha256(
            {
                "schema_version": catalog["schema_version"],
                "contracts": catalog["contracts"],
            }
        )
        if document["tool_surface_digest"] != combined["tool_surface_digest"]:
            raise ValueError("capability tool surface digest mismatch")
        if document["broker_catalog_digest"] != catalog_digest:
            raise ValueError("capability broker catalog digest mismatch")
        if document["planner_directive_schema_version"] != "aar.rlm-directive.v1":
            raise ValueError("capability planner schema version mismatch")
        if document["planner_directive_schema_digest"] != planner_digest:
            raise ValueError("capability planner schema digest mismatch")

        expected_rows = catalog["contracts"]
        rows = document["methods"]
        if [row["method"] for row in rows] != [row["method"] for row in expected_rows]:
            raise ValueError("capability methods must match the exact published order")
        for row, expected in zip(rows, expected_rows, strict=True):
            for key in ("contract_id", "request_schema_digest", "response_schema_digest"):
                if row[key] != expected[key]:
                    raise ValueError(f"capability {row['method']} {key} mismatch")
            if (
                not row["configured"]
                or row["reference_only"]
                or row["backend_kind"] in {"reference", "unconfigured"}
            ):
                raise ValueError(f"capability {row['method']} backend is not truthful")


class WorkspaceBrokerFrame(ContractDocument):
    schema_file = "aar-workspace-broker-frame-v1.schema.json"
    definition = "WorkspaceBrokerFrame"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        payload = document["payload"]
        if document["payload_digest"] != canonical_sha256(payload):
            raise ValueError("worker frame payload digest mismatch")
        if document["payload_bytes"] != len(canonical_json_bytes(payload)):
            raise ValueError("worker frame payload byte count mismatch")


class CallerWorkTicket(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkTicket"


class CallerWorkClaimInput(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkClaimInput"


class CallerWorkMarkSendStartedInput(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkMarkSendStartedInput"


class CallerWorkCancelBeforeSendInput(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkCancelBeforeSendInput"


class CallerWorkCommitInput(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkCommitInput"


class CallerWorkReconcileInput(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CallerWorkReconcileInput"


class CandidateReceipt(ContractDocument):
    schema_file = "aar-caller-work-v1.schema.json"
    definition = "CandidateReceipt"


class RlmWorkbenchFailure(ContractDocument):
    schema_file = "aar-rlm-workbench-v1.schema.json"
    definition = "Failure"


class RlmWorkbenchPhaseProjection(ContractDocument):
    schema_file = "aar-rlm-workbench-v1.schema.json"
    definition = "PhaseProjection"


class ArtifactBinding(ContractDocument):
    schema_file = "aar-artifact-publication-v1.schema.json"
    definition = "ArtifactBinding"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        logical_name = document["logical_name"]
        if (
            not logical_name
            or logical_name.startswith("/")
            or "\\" in logical_name
            or "\x00" in logical_name
            or any(component in {"", ".", ".."} for component in logical_name.split("/"))
        ):
            raise ValueError("artifact logical name must be a safe relative POSIX path")


class ArtifactStage(ContractDocument):
    schema_file = "aar-artifact-publication-v1.schema.json"
    definition = "ArtifactStage"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        if document["content_digest"] != document["binding"]["digest"]:
            raise ValueError("artifact stage content and binding digests differ")


class CellCommitManifest(ContractDocument):
    schema_file = "aar-artifact-publication-v1.schema.json"
    definition = "CellCommitManifest"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        payload = {key: value for key, value in document.items() if key != "manifest_digest"}
        if document["manifest_digest"] != canonical_sha256(payload):
            raise ValueError("cell commit manifest digest mismatch")


class FinalizationManifest(ContractDocument):
    schema_file = "aar-artifact-publication-v1.schema.json"
    definition = "FinalizationManifest"

    @classmethod
    def semantic_validate(cls, document: dict[str, Any]) -> None:
        payload = {key: value for key, value in document.items() if key != "manifest_digest"}
        if document["manifest_digest"] != canonical_sha256(payload):
            raise ValueError("finalization manifest digest mismatch")


def validate_contract_document(
    schema_file: str,
    definition: str,
    document: Mapping[str, Any],
) -> None:
    """Validate one document against a definition in the packaged contract set."""

    errors = sorted(
        _definition_validator(schema_file, definition).iter_errors(dict(document)),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if not errors:
        return
    error = errors[0]
    path = "/".join(str(part) for part in error.absolute_path)
    location = f" at /{path}" if path else ""
    raise ValueError(f"{schema_file}#/$defs/{definition}{location}: {error.message}")


def _validate_json_contract(contract: Mapping[str, Any]) -> None:
    if contract["dialect"] != _DRAFT:
        raise ValueError("schema dialect mismatch")
    if contract["profile"] != "aar.json-schema-profile.v1":
        raise ValueError("schema profile mismatch")
    schema = contract["schema"]
    if len(canonical_json_bytes(schema)) > 65_536:
        raise ValueError("embedded schema exceeds 64 KiB")
    if not 1 <= contract["max_instance_bytes"] <= 1_048_576:
        raise ValueError("embedded instance limit invalid")
    if contract["schema_digest"] != canonical_sha256(schema):
        raise ValueError("embedded schema digest mismatch")
    validate_schema_profile(schema)


def _view(value: Any) -> Any:
    if isinstance(value, dict):
        return _DocumentView(value)
    if isinstance(value, list):
        return tuple(_view(item) for item in value)
    return value


@lru_cache(maxsize=1)
def _contract_documents() -> dict[str, dict[str, Any]]:
    return {name: _load_projection(name) for name in _CONTRACT_FILES}


@cache
def _definition_validator(schema_file: str, definition: str) -> Draft202012Validator:
    documents = _contract_documents()
    if schema_file not in documents or definition not in documents[schema_file].get("$defs", {}):
        raise ValueError(f"unknown contract definition: {schema_file}#/$defs/{definition}")
    registry = Registry()
    for document in documents.values():
        registry = registry.with_resource(document["$id"], Resource.from_contents(document))
    root_schema = {"$schema": _DRAFT, "$ref": f"{schema_file}#/$defs/{definition}"}
    return Draft202012Validator(root_schema, registry=registry)


def _load_projection(name: str) -> dict[str, Any]:
    source_root = Path(__file__).resolve().parents[2] / "schemas"
    source_path = source_root / name
    if source_path.is_file():
        value = json.loads(source_path.read_text(encoding="utf-8"))
    else:
        bundled = resources.files("aar").joinpath("bundled", "schemas", name)
        value = json.loads(bundled.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"packaged contract projection is not an object: {name}")
    return value


__all__ = [
    "ArtifactBinding",
    "ArtifactStage",
    "CallerWorkCancelBeforeSendInput",
    "CallerWorkClaimInput",
    "CallerWorkCommitInput",
    "CallerWorkMarkSendStartedInput",
    "CallerWorkReconcileInput",
    "CallerWorkTicket",
    "CandidateReceipt",
    "CellCommitManifest",
    "ContractDocument",
    "FinalizationManifest",
    "RlmWorkbenchCapability",
    "RlmWorkbenchExecuteInput",
    "RlmWorkbenchFailure",
    "RlmWorkbenchPhaseProjection",
    "WorkspaceBrokerFrame",
    "validate_contract_document",
]
