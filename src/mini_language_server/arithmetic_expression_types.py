"""Bounded exact-snapshot typing for Nova integer arithmetic expressions."""

from __future__ import annotations

from typing import Any

from .inferred_function_returns import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_ADDITIVE = frozenset({"+", "-"})
_MULTIPLICATIVE = frozenset({"*", "/", "%"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative integer arithmetic typing."""

    def _return_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        inherited = super()._return_expression_type(semantic, expression, span)
        if inherited is not None:
            return inherited
        return self._integer_arithmetic_type(semantic, expression, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        inherited = super()._argument_type(snapshot, argument)
        if inherited is not None:
            return inherited
        text = snapshot.symbols.syntax.document.text
        return self._integer_arithmetic_type(
            snapshot,
            text[argument.start : argument.end],
            argument,
        )

    def _integer_arithmetic_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        """Infer Int for bounded arithmetic whose operands are exactly typed Int."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        inherited = super()._return_expression_type(semantic, expression, span)
        if inherited is not None:
            return inherited

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is None:
            return None

        operator, offset = split
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(
            span.start + offset + len(operator),
            span.end,
        )
        left_type = self._integer_arithmetic_type(semantic, left_text, left_span)
        right_type = self._integer_arithmetic_type(semantic, right_text, right_span)
        if left_type == right_type == "Int":
            return "Int"
        return None

    @staticmethod
    def _trim_expression(expression: str, span: Span) -> tuple[str, Span]:
        leading = len(expression) - len(expression.lstrip())
        trailing = len(expression) - len(expression.rstrip())
        end = span.end - trailing if trailing else span.end
        return expression.strip(), Span(span.start + leading, end)

    def _unwrap_expression_with_span(
        self, expression: str, span: Span
    ) -> tuple[str, Span]:
        while True:
            code = self.nova_adapter.code_view(expression)
            if len(code) < 2 or code[0] != "(" or code[-1] != ")":
                return expression, span
            depth = 0
            closes_at_end = False
            for offset, char in enumerate(code):
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                    if depth == 0:
                        closes_at_end = offset == len(code) - 1
                        break
                    if depth < 0:
                        return expression, span
            if not closes_at_end:
                return expression, span
            expression, span = self._trim_expression(
                expression[1:-1], Span(span.start + 1, span.end - 1)
            )

    def _top_level_operator(
        self, expression: str, operators: frozenset[str]
    ) -> tuple[str, int] | None:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        candidate: tuple[str, int] | None = None
        for offset, char in enumerate(code):
            if char == "(":
                depth += 1
                continue
            if char == ")":
                depth -= 1
                if depth < 0:
                    return None
                continue
            if depth != 0 or char not in operators:
                continue
            if char in {"+", "-"} and self._is_unary_sign(code, offset):
                continue
            candidate = (char, offset)
        return candidate

    @staticmethod
    def _is_unary_sign(code: str, offset: int) -> bool:
        prefix = code[:offset].rstrip()
        if not prefix:
            return True
        return prefix[-1] in "(,+-*/%"
