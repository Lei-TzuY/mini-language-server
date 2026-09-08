"""Bounded exact-snapshot typing for Nova comparison expressions."""

from __future__ import annotations

from typing import Any

from .arithmetic_expression_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_EQUALITY = frozenset({"==", "!="})
_ORDERING = frozenset({"<", "<=", ">", ">="})
_BOUNDED_EQUALITY_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative comparison-expression typing."""

    def _return_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        inherited = super()._return_expression_type(semantic, expression, span)
        if inherited is not None:
            return inherited
        return self._comparison_expression_type(semantic, expression, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        inherited = super()._argument_type(snapshot, argument)
        if inherited is not None:
            return inherited
        text = snapshot.symbols.syntax.document.text
        return self._comparison_expression_type(
            snapshot,
            text[argument.start : argument.end],
            argument,
        )

    def _comparison_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        split = self._top_level_comparison_operator(expression)
        if split is None:
            return None

        operator, offset = split
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(span.start + offset + len(operator), span.end)

        left_type = self._integer_arithmetic_type(semantic, left_text, left_span)
        right_type = self._integer_arithmetic_type(semantic, right_text, right_span)

        if operator in _EQUALITY:
            if left_type == right_type and left_type in _BOUNDED_EQUALITY_TYPES:
                return "Bool"
            return None
        if operator in _ORDERING and left_type == right_type == "Int":
            return "Bool"
        return None

    def _top_level_comparison_operator(
        self, expression: str
    ) -> tuple[str, int] | None:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        candidate: tuple[str, int] | None = None
        offset = 0
        while offset < len(code):
            char = code[offset]
            if char == "(":
                depth += 1
                offset += 1
                continue
            if char == ")":
                depth -= 1
                if depth < 0:
                    return None
                offset += 1
                continue
            if depth != 0:
                offset += 1
                continue

            operator = None
            for token in ("==", "!=", "<=", ">=", "<", ">"):
                if code.startswith(token, offset):
                    operator = token
                    break
            if operator is None:
                offset += 1
                continue
            if candidate is not None:
                return None
            candidate = (operator, offset)
            offset += len(operator)

        if depth != 0:
            return None
        return candidate
