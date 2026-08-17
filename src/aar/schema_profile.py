"""Bounded JSON Schema profile used by AR-RW embedded contracts."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from jsonschema import Draft202012Validator

MAX_PATTERN_LENGTH: Final = 256
MAX_REPEAT: Final = 64
_ESCAPABLE_LITERALS: Final = frozenset(r"\\.^$*+?{}[]()|")
_FORBIDDEN_ATOM_CHARS: Final = frozenset(".^$*+?{}[]()|")
ALLOWED_SCHEMA_KEYWORDS: Final = frozenset(
    {
        "$schema",
        "$defs",
        "$ref",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "minItems",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "pattern",
        "enum",
        "const",
        "oneOf",
        "allOf",
        "if",
        "then",
        "else",
        "not",
        "minProperties",
        "maxProperties",
        "propertyNames",
        "title",
        "description",
    }
)


def validate_portable_pattern(pattern: object) -> None:
    """Validate the bounded ASCII regex subset shared by Python and ECMAScript."""

    if not isinstance(pattern, str) or len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError("schema pattern invalid")
    if not pattern.isascii():
        raise ValueError("schema pattern must be ASCII")

    cursor = 1 if pattern.startswith("^") else 0
    end = len(pattern)
    if cursor >= end:
        raise ValueError("schema pattern must contain an atom")

    quantified_atoms = 0
    while cursor < end:
        cursor = _consume_atom(pattern, cursor, end)
        cursor, quantified = _consume_optional_quantifier(pattern, cursor, end)
        quantified_atoms += int(quantified)
        if quantified_atoms > 1:
            raise ValueError("schema regex permits at most one quantified atom")

    try:
        re.compile(pattern)
    except re.error as error:
        raise ValueError(f"schema regex invalid: {error}") from error


def validate_schema_profile(schema: Mapping[str, Any]) -> None:
    """Validate the bounded, local-reference-only Draft 2020-12 profile."""

    value = dict(schema)
    _check_json_tree(value, max_depth=32, max_nodes=4096)
    root_defs = value.get("$defs", {})
    if root_defs is None:
        root_defs = {}
    if not isinstance(root_defs, dict):
        raise ValueError("$defs must be an object")

    def validate_node(node: Any) -> None:
        if not isinstance(node, dict):
            raise ValueError("schema node must be an object")
        unknown = set(node) - ALLOWED_SCHEMA_KEYWORDS
        if unknown:
            raise ValueError(f"schema keywords forbidden: {sorted(unknown)}")
        ref_value = node.get("$ref")
        if ref_value is not None:
            if not isinstance(ref_value, str) or not ref_value.startswith("#/$defs/"):
                raise ValueError(f"schema ref forbidden: {ref_value!r}")
            target_name = ref_value.removeprefix("#/$defs/")
            if "/" in target_name or target_name not in root_defs:
                raise ValueError(f"schema local ref unresolved: {ref_value}")
        type_value = node.get("type")
        if type_value is not None and type_value not in {
            "object",
            "array",
            "string",
            "integer",
            "number",
            "boolean",
            "null",
        }:
            raise ValueError("schema type invalid")
        properties = node.get("properties")
        if properties is not None:
            if not isinstance(properties, dict) or len(properties) > 256:
                raise ValueError("schema properties invalid")
            for property_name, child in properties.items():
                if not isinstance(property_name, str) or not 0 < len(property_name) <= 128:
                    raise ValueError("schema property name invalid")
                validate_node(child)
        definitions = node.get("$defs")
        if definitions is not None:
            if not isinstance(definitions, dict) or len(definitions) > 256:
                raise ValueError("schema defs invalid")
            for definition_name, child in definitions.items():
                if (
                    not isinstance(definition_name, str)
                    or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]{0,127}", definition_name)
                    is None
                ):
                    raise ValueError("schema def name invalid")
                validate_node(child)
        required = node.get("required")
        if required is not None and not (
            isinstance(required, list)
            and all(isinstance(item, str) for item in required)
            and required == sorted(set(required))
            and len(required) <= 256
        ):
            raise ValueError("schema required list must be sorted and unique")
        additional = node.get("additionalProperties")
        if additional is not None and not isinstance(additional, bool):
            validate_node(additional)
        items = node.get("items")
        if items is not None:
            validate_node(items)
        for keyword in ("oneOf", "allOf"):
            branches = node.get(keyword)
            if branches is not None:
                if not isinstance(branches, list) or not 1 <= len(branches) <= 32:
                    raise ValueError(f"schema {keyword} invalid")
                for branch in branches:
                    validate_node(branch)
        for keyword in ("if", "then", "else", "not", "propertyNames"):
            child = node.get(keyword)
            if child is not None:
                validate_node(child)
        pattern = node.get("pattern")
        if pattern is not None:
            validate_portable_pattern(pattern)
        enum_values = node.get("enum")
        if enum_values is not None:
            if not isinstance(enum_values, list) or not 1 <= len(enum_values) <= 128:
                raise ValueError("schema enum invalid")
            _check_json_tree(enum_values, max_depth=4, max_nodes=512)
        for key in (
            "minItems",
            "maxItems",
            "minLength",
            "maxLength",
            "minProperties",
            "maxProperties",
        ):
            if key in node and (
                not isinstance(node[key], int)
                or isinstance(node[key], bool)
                or not 0 <= node[key] <= 1_048_576
            ):
                raise ValueError(f"schema {key} invalid")
        for key in ("minimum", "maximum"):
            if key in node and (
                not isinstance(node[key], (int, float)) or isinstance(node[key], bool)
            ):
                raise ValueError(f"schema {key} invalid")

    validate_node(value)
    Draft202012Validator.check_schema(value)


def _check_json_tree(value: Any, *, max_depth: int, max_nodes: int) -> None:
    node_count = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal node_count
        node_count += 1
        if node_count > max_nodes:
            raise ValueError(f"JSON node limit exceeded: {max_nodes}")
        if depth > max_depth:
            raise ValueError(f"JSON depth limit exceeded: {max_depth}")
        if isinstance(item, str):
            if any(0xD800 <= ord(character) <= 0xDFFF for character in item):
                raise ValueError("JSON string contains a lone surrogate")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                visit(key, depth + 1)
                visit(child, depth + 1)

    visit(value, 0)


def _consume_atom(pattern: str, cursor: int, end: int) -> int:
    character = pattern[cursor]
    if character == "\\":
        if cursor + 1 >= end or pattern[cursor + 1] not in _ESCAPABLE_LITERALS:
            raise ValueError("schema regex escape is not portable")
        return cursor + 2
    if character == "[":
        closing = pattern.find("]", cursor + 1, end)
        if closing < 0:
            raise ValueError("schema regex class is unterminated")
        _validate_positive_class(pattern[cursor + 1 : closing])
        return closing + 1
    if character in _FORBIDDEN_ATOM_CHARS:
        raise ValueError("schema regex construct is forbidden")
    if ord(character) < 0x20 or ord(character) > 0x7E:
        raise ValueError("schema regex literal must be printable ASCII")
    return cursor + 1


def _validate_positive_class(content: str) -> None:
    if not content or content.startswith("^"):
        raise ValueError("schema regex class must be positive and non-empty")
    if any(ord(character) < 0x20 or ord(character) > 0x7E for character in content):
        raise ValueError("schema regex class must contain printable ASCII")
    if any(character in "\\[]^" for character in content):
        raise ValueError("schema regex class escape or nesting is forbidden")

    cursor = 0
    while cursor < len(content):
        start = content[cursor]
        if start == "-":
            raise ValueError("schema regex class has a bare hyphen")
        if cursor + 1 < len(content) and content[cursor + 1] == "-":
            if cursor + 2 >= len(content):
                raise ValueError("schema regex class range is incomplete")
            finish = content[cursor + 2]
            if finish == "-" or ord(start) > ord(finish):
                raise ValueError("schema regex class range is invalid")
            cursor += 3
        else:
            cursor += 1


def _consume_optional_quantifier(
    pattern: str,
    cursor: int,
    end: int,
) -> tuple[int, bool]:
    if cursor >= end:
        return cursor, False
    if pattern[cursor] == "?":
        return cursor + 1, True
    if pattern[cursor] != "{":
        return cursor, False

    closing = pattern.find("}", cursor + 1, end)
    if closing < 0:
        raise ValueError("schema regex repeat is unterminated")
    repeat = pattern[cursor + 1 : closing]
    match = re.fullmatch(r"([0-9]{1,2})(?:,([0-9]{1,2}))?", repeat)
    if match is None:
        raise ValueError("schema regex repeat must be finite")
    minimum = int(match.group(1))
    maximum = int(match.group(2) or match.group(1))
    if minimum > maximum or maximum > MAX_REPEAT:
        raise ValueError("schema regex repeat exceeds bounded profile")
    return closing + 1, True
