"""Bounded exact-snapshot Nova division-by-zero diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .diagnostics import Diagnostic
from .loop_control import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_DIVISION_OPERATOR = re.compile(r"/|%")
_DIVISION_BY_ZERO_DIAGNOSTIC = "nova.division-by-zero"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative constant-zero divisor diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _DIVISION_BY_ZERO_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_division_by_zero_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        current = self.diagnostics.get(uri)
        if current is None or current.semantic.symbols.syntax.document is not document:
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code != _DIVISION_BY_ZERO_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if start_offset == end_offset:
                overlaps = diagnostic.span.start <= start_offset <= diagnostic.span.end
            else:
                overlaps = (
                    diagnostic.span.start < end_offset
                    and start_offset < diagnostic.span.end
                )
            if not overlaps:
                continue
            actions.append(
                {
                    "title": "Replace zero divisor with 1",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "1",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _nova_division_by_zero_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for match in _DIVISION_OPERATOR.finditer(code):
            divisor_span = _constant_zero_divisor_span(code, match.end())
            if divisor_span is None:
                continue
            operator = match.group()
            operation = "division" if operator == "/" else "remainder"
            diagnostics.append(
                Diagnostic(
                    span=Span(*divisor_span),
                    message=f"integer {operation} by zero is invalid",
                    code=_DIVISION_BY_ZERO_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)


@dataclass(frozen=True)
class _Constant:
    value: int
    start: int
    end: int
    atomic: bool


def _constant_zero_divisor_span(code: str, offset: int) -> tuple[int, int] | None:
    """Return an exact repair span for a bounded constant-zero divisor."""
    parsed = _parse_primary(code, _skip_whitespace(code, offset))
    if parsed is None:
        return None
    constant, _ = parsed
    if constant.value != 0:
        return None
    return constant.start, constant.end


def bounded_integer_constant_value(expression: str) -> int | None:
    """Evaluate the bounded integer-constant grammar only when it consumes all input."""
    parsed = _parse_expression(expression, 0)
    if parsed is None:
        return None
    constant, cursor = parsed
    if _skip_whitespace(expression, cursor) != len(expression):
        return None
    return constant.value


def _parse_primary(code: str, offset: int) -> tuple[_Constant, int] | None:
    cursor = _skip_whitespace(code, offset)
    sign = 1
    signed_start = cursor
    if cursor < len(code) and code[cursor] in "+-":
        if code[cursor] == "-":
            sign = -1
        cursor = _skip_whitespace(code, cursor + 1)

    if cursor < len(code) and code[cursor].isdigit():
        literal_start = cursor
        while cursor < len(code) and code[cursor].isdigit():
            cursor += 1
        if cursor < len(code) and (code[cursor].isalnum() or code[cursor] in "_."):
            return None
        start = signed_start if signed_start != literal_start else literal_start
        return _Constant(sign * int(code[literal_start:cursor]), start, cursor, True), cursor

    if sign != 1 or signed_start != cursor:
        return None
    if cursor >= len(code) or code[cursor] != "(":
        return None

    open_paren = cursor
    parsed = _parse_expression(code, cursor + 1)
    if parsed is None:
        return None
    constant, cursor = parsed
    cursor = _skip_whitespace(code, cursor)
    if cursor >= len(code) or code[cursor] != ")":
        return None
    close_paren = cursor
    cursor += 1

    if constant.atomic:
        return _Constant(constant.value, constant.start, constant.end, True), cursor

    inner_start = _skip_whitespace(code, open_paren + 1)
    inner_end = close_paren
    while inner_end > inner_start and code[inner_end - 1].isspace():
        inner_end -= 1
    return _Constant(constant.value, inner_start, inner_end, False), cursor


def _parse_expression(code: str, offset: int) -> tuple[_Constant, int] | None:
    parsed = _parse_term(code, offset)
    if parsed is None:
        return None
    left, cursor = parsed

    while True:
        operator_offset = _skip_whitespace(code, cursor)
        if operator_offset >= len(code) or code[operator_offset] not in "+-":
            break
        operator = code[operator_offset]
        parsed = _parse_term(code, operator_offset + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        value = left.value + right.value if operator == "+" else left.value - right.value
        left = _Constant(value, left.start, right.end, False)

    return left, cursor


def _parse_term(code: str, offset: int) -> tuple[_Constant, int] | None:
    parsed = _parse_primary(code, offset)
    if parsed is None:
        return None
    left, cursor = parsed

    while True:
        operator_offset = _skip_whitespace(code, cursor)
        if operator_offset >= len(code) or code[operator_offset] not in "*/%":
            break
        operator = code[operator_offset]
        parsed = _parse_primary(code, operator_offset + 1)
        if parsed is None:
            return None
        right, cursor = parsed
        if operator in "/%" and right.value == 0:
            return None
        if operator == "*":
            value = left.value * right.value
        elif operator == "/":
            value = _truncating_division(left.value, right.value)
        else:
            quotient = _truncating_division(left.value, right.value)
            value = left.value - quotient * right.value
        left = _Constant(value, left.start, right.end, False)

    return left, cursor


def _truncating_division(left: int, right: int) -> int:
    quotient = abs(left) // abs(right)
    return -quotient if (left < 0) != (right < 0) else quotient


def _skip_whitespace(code: str, offset: int) -> int:
    while offset < len(code) and code[offset].isspace():
        offset += 1
    return offset
