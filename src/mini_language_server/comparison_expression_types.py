"""Bounded exact-snapshot typing for Nova comparison expressions."""

from __future__ import annotations

from typing import Any

from .arithmetic_expression_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_EQUALITY = frozenset({"==", "!="})
_ORDERING = frozenset({"<", "<=", ">", ">="})
_BOUNDED_EQUALITY_TYPES = frozenset({"Int", "String", "Bool", "Unit"})
_COMPARISON_TOKENS = ("==", "!=", "<=", ">=", "<", ">")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative comparison-expression typing."""

    def _return_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        present, comparison_type = self._comparison_type_if_present(
            semantic, expression, span
        )
        if present:
            return comparison_type
        return super()._return_expression_type(semantic, expression, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        text = snapshot.symbols.syntax.document.text
        expression = text[argument.start : argument.end]
        present, comparison_type = self._comparison_type_if_present(
            snapshot, expression, argument
        )
        if present:
            return comparison_type
        return super()._argument_type(snapshot, argument)

    def _inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Reuse bounded comparison semantics without dropping call-cycle identity."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        operators = self._top_level_comparison_operators(expression)
        if not operators:
            return super()._inference_expression_type(
                semantic,
                expression,
                span,
                resolving,
            )
        if len(operators) != 1:
            return None

        operator, offset = operators[0]
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(span.start + offset + len(operator), span.end)

        left_type = self._comparison_inference_operand_type(
            semantic,
            left_text,
            left_span,
            resolving,
        )
        right_type = self._comparison_inference_operand_type(
            semantic,
            right_text,
            right_span,
            resolving,
        )

        if operator in _EQUALITY:
            if left_type == right_type and left_type in _BOUNDED_EQUALITY_TYPES:
                return "Bool"
            return None
        if operator in _ORDERING and left_type == right_type == "Int":
            return "Bool"
        return None

    def _comparison_inference_operand_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Preserve Nova's Unit literal before grouping-parenthesis unwrapping."""
        if expression.strip() == "()":
            return "Unit"
        return super()._inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _comparison_type_if_present(
        self, semantic: Any, expression: str, span: Span
    ) -> tuple[bool, str | None]:
        """Own top-level comparisons even when their bounded type is unknown."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        operators = self._top_level_comparison_operators(expression)
        if not operators:
            return False, None
        if len(operators) != 1:
            return True, None

        operator, offset = operators[0]
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(span.start + offset + len(operator), span.end)

        left_type = self._comparison_operand_type(semantic, left_text, left_span)
        right_type = self._comparison_operand_type(semantic, right_text, right_span)

        if operator in _EQUALITY:
            if left_type == right_type and left_type in _BOUNDED_EQUALITY_TYPES:
                return True, "Bool"
            return True, None
        if operator in _ORDERING and left_type == right_type == "Int":
            return True, "Bool"
        return True, None

    def _comparison_operand_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        """Type one bounded comparison operand without erasing Unit's `()` literal."""
        if expression.strip() == "()":
            return "Unit"
        return self._integer_arithmetic_type(semantic, expression, span)

    def _top_level_comparison_operators(
        self, expression: str
    ) -> tuple[tuple[str, int], ...]:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        result: list[tuple[str, int]] = []
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
                    return ()
                offset += 1
                continue
            if depth != 0:
                offset += 1
                continue

            operator = None
            for token in _COMPARISON_TOKENS:
                if code.startswith(token, offset):
                    operator = token
                    break
            if operator is None:
                offset += 1
                continue
            result.append((operator, offset))
            offset += len(operator)

        if depth != 0:
            return ()
        return tuple(result)
