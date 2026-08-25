"""Canonical registry and strict raw-byte loader for provider-ready contracts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar

from aar.canonical import canonical_json_bytes
from aar.provider_ready_evaluation_models import (
    PROVIDER_READY_EVALUATION_SCHEMA_MODELS as _EVALUATION_SCHEMA_MODELS,
)
from aar.provider_ready_models import PROVIDER_READY_SCHEMA_MODELS as _SCHEMA_MODELS
from aar.provider_ready_operator_models import (
    PROVIDER_READY_OPERATOR_SCHEMA_MODELS as _OPERATOR_SCHEMA_MODELS,
)
from aar.provider_ready_runtime_models import (
    PROVIDER_READY_RUNTIME_SCHEMA_MODELS as _RUNTIME_SCHEMA_MODELS,
)
from aar.schemas import StrictModel


class ProviderReadyContractError(ValueError):
    """Base error for deterministic provider-ready byte and dispatch failures."""

    code: ClassVar[str] = "PROVIDER_READY_CONTRACT"

    def __init__(self, message: str = "contract input is invalid") -> None:
        prefix = f"{self.code}:"
        text = message if message.startswith(prefix) else f"{prefix} {message}"
        super().__init__(text)


class ProviderReadyInputTypeError(ProviderReadyContractError):
    code = "INPUT_TYPE"


class ProviderReadyBomError(ProviderReadyContractError):
    code = "UTF8_BOM"


class ProviderReadyUtf8Error(ProviderReadyContractError):
    code = "UTF8_DECODE"


class ProviderReadyDuplicateKeyError(ProviderReadyContractError):
    code = "DUPLICATE_KEY"


class ProviderReadyFloatError(ProviderReadyContractError):
    code = "FLOAT_NOT_ALLOWED"


class ProviderReadyNonfiniteError(ProviderReadyContractError):
    code = "NONFINITE_NOT_ALLOWED"


class ProviderReadyJsonSyntaxError(ProviderReadyContractError):
    code = "JSON_SYNTAX"


class ProviderReadyJsonTrailingDataError(ProviderReadyContractError):
    code = "JSON_TRAILING_DATA"


class ProviderReadyNonObjectError(ProviderReadyContractError):
    code = "NON_OBJECT_ROOT"


class ProviderReadySchemaMissingError(ProviderReadyContractError):
    code = "SCHEMA_VERSION_MISSING"


class ProviderReadySchemaTypeError(ProviderReadyContractError):
    code = "SCHEMA_VERSION_TYPE"


class ProviderReadySchemaUnknownError(ProviderReadyContractError):
    code = "SCHEMA_VERSION_UNKNOWN"


class ProviderReadySchemaMismatchError(ProviderReadyContractError):
    code = "SCHEMA_VERSION_MISMATCH"


def _merge_schema_registries(
    *registries: Mapping[str, type[StrictModel]],
) -> dict[str, type[StrictModel]]:
    """Merge source registries without allowing a key to be overwritten."""

    merged: dict[str, type[StrictModel]] = {}
    for registry in registries:
        for schema_id, model_type in registry.items():
            if schema_id in merged:
                raise ValueError(f"duplicate provider-ready schema key: {schema_id}")
            merged[schema_id] = model_type
    return merged


PROVIDER_READY_SCHEMA_MODELS = _merge_schema_registries(
    _SCHEMA_MODELS,
    _OPERATOR_SCHEMA_MODELS,
    _RUNTIME_SCHEMA_MODELS,
    _EVALUATION_SCHEMA_MODELS,
)
PROVIDER_READY_SCHEMA_ORDER = tuple(sorted(PROVIDER_READY_SCHEMA_MODELS))


def reject_duplicate_object_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object and fail closed when any key occurs more than once."""

    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProviderReadyDuplicateKeyError(
                f"duplicate object key {key!r}"
            )
        result[key] = value
    return result


def _reject_float(value: str) -> float:
    raise ProviderReadyFloatError(f"JSON floating-point value {value!r} is not allowed")


def _reject_nonfinite(value: str) -> float:
    raise ProviderReadyNonfiniteError(f"JSON non-finite value {value!r} is not allowed")


def load_provider_ready_json_bytes(data: bytes) -> dict[str, object]:
    """Parse exact UTF-8 JSON bytes with duplicate keys and floats rejected."""

    if type(data) is not bytes:
        raise ProviderReadyInputTypeError("data must be an exact bytes instance")
    if data.startswith(b"\xef\xbb\xbf"):
        raise ProviderReadyBomError("UTF-8 BOM is not allowed")
    try:
        text = data.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ProviderReadyUtf8Error("data is not valid UTF-8") from error

    try:
        parsed: Any = json.loads(
            text,
            object_pairs_hook=reject_duplicate_object_keys,
            parse_float=_reject_float,
            parse_constant=_reject_nonfinite,
        )
    except ProviderReadyContractError:
        raise
    except json.JSONDecodeError as error:
        if error.msg == "Extra data":
            raise ProviderReadyJsonTrailingDataError(
                "JSON contains trailing data"
            ) from error
        raise ProviderReadyJsonSyntaxError("JSON syntax is invalid") from error

    if not isinstance(parsed, dict):
        raise ProviderReadyNonObjectError("JSON root must be an object")
    return parsed


def validate_provider_ready_document_bytes(
    data: bytes,
    *,
    expected_schema_id: str | None = None,
) -> StrictModel:
    """Load, dispatch, and strictly validate one canonical provider-ready document."""

    document = load_provider_ready_json_bytes(data)
    if "schema_version" not in document:
        raise ProviderReadySchemaMissingError("schema_version field is required")
    schema_id = document["schema_version"]
    if not isinstance(schema_id, str):
        raise ProviderReadySchemaTypeError("schema_version must be a string")

    model_type = PROVIDER_READY_SCHEMA_MODELS.get(schema_id)
    if model_type is None:
        raise ProviderReadySchemaUnknownError(f"unknown schema_version {schema_id!r}")
    if expected_schema_id is not None and schema_id != expected_schema_id:
        raise ProviderReadySchemaMismatchError(
            f"schema_version {schema_id!r} does not match expected {expected_schema_id!r}"
        )

    # Re-serialize parsed JSON rather than passing Python lists to strict tuple fields.
    return model_type.model_validate_json(canonical_json_bytes(document), strict=True)


__all__ = [
    "PROVIDER_READY_SCHEMA_MODELS",
    "PROVIDER_READY_SCHEMA_ORDER",
    "ProviderReadyBomError",
    "ProviderReadyContractError",
    "ProviderReadyDuplicateKeyError",
    "ProviderReadyFloatError",
    "ProviderReadyInputTypeError",
    "ProviderReadyJsonSyntaxError",
    "ProviderReadyJsonTrailingDataError",
    "ProviderReadyNonObjectError",
    "ProviderReadyNonfiniteError",
    "ProviderReadySchemaMismatchError",
    "ProviderReadySchemaMissingError",
    "ProviderReadySchemaTypeError",
    "ProviderReadySchemaUnknownError",
    "ProviderReadyUtf8Error",
    "load_provider_ready_json_bytes",
    "reject_duplicate_object_keys",
    "validate_provider_ready_document_bytes",
]
