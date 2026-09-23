"""Pure bounded parsing for direct Nova local call initializers."""

from __future__ import annotations

import re
from collections.abc import Callable

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_LOCAL_CALL_PREFIX = re.compile(rf"\s*=\s*(?P<name>{_IDENTIFIER})\s*\(")
_LOCAL_INITIALIZER_TAIL = re.compile(
    rf"\s*;?\s*(?=\}}|let\b|{_IDENTIFIER}(?:\s*\(|\b)|$)"
)

_CallBounds = tuple[int, int, tuple[tuple[int, int], ...]]


def direct_local_call_initializer(
    text: str,
    code: str,
    target_end: int,
    call_argument_bounds: Callable[[str, int], _CallBounds | None],
) -> tuple[str, str] | None:
    """Return the unique direct-call initializer text owned by one local declaration."""
    suffix = code[target_end:]
    call = _LOCAL_CALL_PREFIX.match(suffix)
    if call is None:
        return None

    expression_start = target_end + call.start("name")
    expression = text[expression_start:]
    expression_code = code[expression_start:]
    name_end = call.end("name") - call.start("name")
    parsed = call_argument_bounds(expression, name_end)
    if parsed is None:
        return None
    closing = parsed[1]
    if _LOCAL_INITIALIZER_TAIL.match(expression_code[closing + 1 :]) is None:
        return None

    return call.group("name"), expression[: closing + 1]
