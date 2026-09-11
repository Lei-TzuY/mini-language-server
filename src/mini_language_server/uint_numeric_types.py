"""Bounded exact-snapshot typing for Nova UInt numeric operators."""

from __future__ import annotations

from typing import Any

from .arithmetic_expression_types import _ADDITIVE, _MULTIPLICATIVE
from .comparison_expression_types import (
    _BOUNDED_EQUALITY_TYPES,
    _EQUALITY,
    _ORDERING,
)
from .source import Span
from .uint_intrinsic_types import NovaProductLanguageServer as _NovaProductLanguageServer

_UINT_NUMERIC_TYPES = frozenset({"Int", "UInt"})
_UINT_EQUALITY_TYPES = _BOUNDED_EQUALITY_TYPES | {"UInt"}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative same-family UInt numeric typing."""

    def _integer_arithmetic_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        """Infer Int/UInt only for same-family bounded arithmetic operands."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is None:
            unary = self._unary_negation_operand(expression, span)
            if unary is not None:
                operand, operand_span = unary
                operand_type = self._integer_arithmetic_type(
                    semantic,
                    operand,
                    operand_span,
                )
                return "Int" if operand_type == "Int" else None
            return super()._integer_arithmetic_type(semantic, expression, span)

        operator, offset = split
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(span.start + offset + len(operator), span.end)
        left_type = self._integer_arithmetic_type(semantic, left_text, left_span)
        right_type = self._integer_arithmetic_type(semantic, right_text, right_span)
        if left_type == right_type and left_type in _UINT_NUMERIC_TYPES:
            return left_type
        return None

    def _comparison_type_if_present(
        self, semantic: Any, expression: str, span: Span
    ) -> tuple[bool, str | None]:
        """Extend bounded comparisons with same-family UInt equality and ordering."""
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
            if left_type == right_type and left_type in _UINT_EQUALITY_TYPES:
                return True, "Bool"
            return True, None
        if operator in _ORDERING:
            if left_type == right_type and left_type in _UINT_NUMERIC_TYPES:
                return True, "Bool"
            return True, None
        return True, None

    def _inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Propagate UInt numeric results without dropping call-cycle identity."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        # Logical composition has lower precedence than comparisons. Preserve the
        # existing logical layer as the owner of expressions containing &&, ||,
        # or unary ! instead of claiming an embedded comparison too early.
        if self._has_logical_operator(expression):
            return super()._inference_expression_type(
                semantic,
                expression,
                span,
                resolving,
            )

        operators = self._top_level_comparison_operators(expression)
        if operators:
            if len(operators) != 1:
                return None
            operator, offset = operators[0]
            left_text = expression[:offset]
            right_text = expression[offset + len(operator) :]
            left_span = Span(span.start, span.start + offset)
            right_span = Span(span.start + offset + len(operator), span.end)
            left_type = self._inference_expression_type(
                semantic,
                left_text,
                left_span,
                resolving,
            )
            right_type = self._inference_expression_type(
                semantic,
                right_text,
                right_span,
                resolving,
            )
            if operator in _EQUALITY:
                if left_type == right_type and left_type in _UINT_EQUALITY_TYPES:
                    return "Bool"
                return None
            if operator in _ORDERING:
                if left_type == right_type and left_type in _UINT_NUMERIC_TYPES:
                    return "Bool"
                return None
            return None

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is not None:
            operator, offset = split
            left_text = expression[:offset]
            right_text = expression[offset + len(operator) :]
            left_span = Span(span.start, span.start + offset)
            right_span = Span(span.start + offset + len(operator), span.end)
            left_type = self._inference_expression_type(
                semantic,
                left_text,
                left_span,
                resolving,
            )
            right_type = self._inference_expression_type(
                semantic,
                right_text,
                right_span,
                resolving,
            )
            if left_type == right_type and left_type in _UINT_NUMERIC_TYPES:
                return left_type
            return None

        unary = self._unary_negation_operand(expression, span)
        if unary is not None:
            operand, operand_span = unary
            operand_type = self._inference_expression_type(
                semantic,
                operand,
                operand_span,
                resolving,
            )
            return "Int" if operand_type == "Int" else None

        return super()._inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )
