"""Deterministic JSON encoding and digest helpers for public contract values."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        raise TypeError("floating-point values are not allowed in canonical AAR JSON")
    if isinstance(value, Mapping):
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical AAR JSON object keys must be strings")
            normalized[key] = _json_value(item)
        return normalized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    raise TypeError(f"unsupported canonical AAR JSON value: {type(value).__name__}")


def canonical_json_bytes(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes without insignificant whitespace."""

    normalized = _json_value(value)
    return json.dumps(
        normalized,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """Return the typed SHA-256 digest of a canonical JSON value."""

    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"


def pretty_json_bytes(value: Any) -> bytes:
    """Return deterministic review-friendly UTF-8 JSON with one trailing newline."""

    normalized = _json_value(value)
    text = json.dumps(normalized, allow_nan=False, ensure_ascii=False, indent=2, sort_keys=True)
    return f"{text}\n".encode()
