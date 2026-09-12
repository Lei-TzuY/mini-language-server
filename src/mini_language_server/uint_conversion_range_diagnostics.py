"""Exact-snapshot diagnostics for provably out-of-range Nova numeric conversions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diagnostics import Diagnostic
from .division_diagnostics import bounded_integer_constant_value
from .semantic import SemanticSnapshot
from .uint_intrinsic_semantic_tokens import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)

_CONVERSION_RANGE_DIAGNOSTIC = "nova.conversion-range"
_UINT_MAX = (1 << 64) - 1
_INT_MAX = (1 << 63) - 1
_UINT_MAX_MEMBER = re.compile(r"UInt\s*::\s*MAX\Z")
_UINT_MIN_MEMBER = re.compile(r"UInt\s*::\s*MIN\Z")
_UINT_MEMBER = re.compile(r"UInt\s*::\s*(MIN|MAX)")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative checked-conversion range diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _CONVERSION_RANGE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_conversion_range_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_conversion_range_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        diagnostics: list[Diagnostic] = []
        for call in self._conversion_calls(text):
            if len(call.arguments) != 1:
                continue
            argument, argument_span = call.arguments[0]
            code = self.nova_adapter.code_view(argument).strip()

            if call.name == "UInt::from":
                value = _known_integer_constant_value(code)
                if value is None or 0 <= value <= _UINT_MAX:
                    continue
                diagnostics.append(
                    Diagnostic(
                        span=argument_span,
                        message=(
                            "checked conversion 'UInt::from' cannot represent "
                            f"constant Int value {value} as UInt"
                        ),
                        code=_CONVERSION_RANGE_DIAGNOSTIC,
                        source="nova",
                    )
                )
                continue

            value = _known_uint_constant_value(code)
            if value is None or value <= _INT_MAX:
                continue
            diagnostics.append(
                Diagnostic(
                    span=argument_span,
                    message=(
                        "checked conversion 'Int::from_uint' cannot represent "
                        f"constant UInt value {value} as Int; maximum is {_INT_MAX}"
                    ),
                    code=_CONVERSION_RANGE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)


def _known_integer_constant_value(expression: str) -> int | None:
    value = bounded_integer_constant_value(expression)
    if value is not None:
        return value
    return _known_uint_constant_value(expression)


def _known_uint_constant_value(expression: str) -> int | None:
    member = _strip_balanced_outer_parentheses(expression)
    if _UINT_MIN_MEMBER.fullmatch(member):
        return 0
    if _UINT_MAX_MEMBER.fullmatch(member):
        return _UINT_MAX

    parsed = _parse_uint_expression(member, 0)
    if parsed is None:
        return None
    value, cursor = parsed
    if _skip_whitespace(member, cursor) != len(member):
        return None
    return value


def _parse_uint_expression(expression: str, offset: int) -> tuple[int, int] | None:
    parsed = _parse_uint_term(expression, offset)
    if parsed is None:
        return None
    left, cursor = parsed

    while True:
        operator_offset = _skip_whitespace(expression, cursor)
        if operator_offset >= len(expression) or expression[operator_offset] not in "+-":
            return left, cursor
        operator = expression[operator_offset]
        parsed = _parse_uint_term(expression, operator_offset + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        value = left + right if operator == "+" else left - right
        if not 0 <= value <= _UINT_MAX:
            return None
        left = value


def _parse_uint_term(expression: str, offset: int) -> tuple[int, int] | None:
    parsed = _parse_uint_primary(expression, offset)
    if parsed is None:
        return None
    left, cursor = parsed

    while True:
        operator_offset = _skip_whitespace(expression, cursor)
        if operator_offset >= len(expression) or expression[operator_offset] not in "*/%":
            return left, cursor
        operator = expression[operator_offset]
        parsed = _parse_uint_primary(expression, operator_offset + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        if operator in "/%" and right == 0:
            return None
        if operator == "*":
            value = left * right
        elif operator == "/":
            value = left // right
        else:
            value = left % right
        if not 0 <= value <= _UINT_MAX:
            return None
        left = value


def _parse_uint_primary(expression: str, offset: int) -> tuple[int, int] | None:
    cursor = _skip_whitespace(expression, offset)
    member = _UINT_MEMBER.match(expression, cursor)
    if member is not None:
        value = 0 if member.group(1) == "MIN" else _UINT_MAX
        return value, member.end()

    if cursor >= len(expression) or expression[cursor] != "(":
        return None
    parsed = _parse_uint_expression(expression, cursor + 1)
    if parsed is None:
        return None
    value, cursor = parsed
    cursor = _skip_whitespace(expression, cursor)
    if cursor >= len(expression) or expression[cursor] != ")":
        return None
    return value, cursor + 1


def _skip_whitespace(expression: str, offset: int) -> int:
    while offset < len(expression) and expression[offset].isspace():
        offset += 1
    return offset


def _strip_balanced_outer_parentheses(expression: str) -> str:
    value = expression.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, char in enumerate(value):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return value
                if depth == 0:
                    closes_at_end = index == len(value) - 1
                    break
        if not closes_at_end:
            return value
        value = value[1:-1].strip()
    return value
