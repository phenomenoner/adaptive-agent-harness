from __future__ import annotations

import re
from typing import Final

MAX_PATTERN_LENGTH: Final = 256
MAX_REPEAT: Final = 64
_ESCAPABLE_LITERALS: Final = frozenset(r"\\.^$*+?{}[]()|")
_FORBIDDEN_ATOM_CHARS: Final = frozenset(".^$*+?{}[]()|")


def validate_portable_linear_pattern(pattern: object) -> None:
    """Validate the bounded ASCII regex subset shared by Python and ECMAScript.

    Grammar, informally::

        pattern    := '^'? atom quantifier? atom ...
        atom       := ASCII_LITERAL | ESCAPED_META | positive_class
        quantifier := '?' | '{m}' | '{m,n}'       (0 <= m <= n <= 64)

    At most one atom in the entire pattern may be quantified. A trailing `$`
    anchor is forbidden because Python permits a match before a final newline
    while ECMAScript does not. Outside a character class, only true regex
    syntax characters may be escaped; ECMAScript-Unicode identity escapes
    such as `\\-` are forbidden. Groups, alternation, dot, negated classes,
    shorthand or engine-specific escapes, and unbounded quantifiers are
    deliberately absent. With one bounded quantified atom, bounded pattern
    size, and no end-anchor ambiguity, matching work is linear in instance
    length with a fixed repeat ceiling.
    """
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


def _consume_optional_quantifier(pattern: str, cursor: int, end: int) -> tuple[int, bool]:
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
