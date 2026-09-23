"""Consolidated exact-snapshot typing for Nova bounded expressions.

Arithmetic, string concatenation, comparison, and logical expression typing share
one product boundary. Operator ownership follows Nova precedence while preserving
conservative fallbacks into the preceding exact-snapshot inference layer.
"""

from __future__ import annotations

from typing import Any

from .inferred_return_inlay_hints import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_ADDITIVE = frozenset({"+", "-"})
_MULTIPLICATIVE = frozenset({"*", "/", "%"})
_EQUALITY = frozenset({"==", "!="})
_ORDERING = frozenset({"<", "<=", ">", ">="})
_BOUNDED_EQUALITY_TYPES = frozenset({"Int", "String", "Bool", "Unit"})
_COMPARISON_TOKENS = ("==", "!=", "<=", ">=", "<", ">")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Nova product with one conservative bounded-expression typing boundary."""

    def _return_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        present, logical_type = self._logical_type_if_present(
            semantic, expression, span
        )
        if present:
            return logical_type

        present, comparison_type = self._comparison_type_if_present(
            semantic, expression, span
        )
        if present:
            return comparison_type
        return self._integer_arithmetic_type(semantic, expression, span)

    def _closed_argument_type(
        self,
        snapshot: Any,
        argument: Span,
    ) -> str | None:
        """Extend detached reference evidence with bounded expression typing."""
        inherited = super()._closed_argument_type(snapshot, argument)
        if inherited is not None:
            return inherited
        text = snapshot.symbols.syntax.document.text
        return self._closed_expression_type(
            snapshot,
            text[argument.start : argument.end],
            argument,
        )

    def _closed_expression_type(
        self,
        snapshot: Any,
        expression: str,
        span: Span,
    ) -> str | None:
        """Type one detached expression using only same-snapshot leaf evidence."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)
        if not expression:
            return None

        parts = self._split_top_level_logical(expression, span, "||")
        if parts is not None:
            if not parts:
                return None
            return (
                "Bool"
                if all(
                    self._closed_expression_type(snapshot, part, part_span) == "Bool"
                    for part, part_span in parts
                )
                else None
            )

        parts = self._split_top_level_logical(expression, span, "&&")
        if parts is not None:
            if not parts:
                return None
            return (
                "Bool"
                if all(
                    self._closed_expression_type(snapshot, part, part_span) == "Bool"
                    for part, part_span in parts
                )
                else None
            )

        operators = self._top_level_comparison_operators(expression)
        if operators:
            if len(operators) != 1:
                return None
            operator, offset = operators[0]
            left_span = Span(span.start, span.start + offset)
            right_span = Span(span.start + offset + len(operator), span.end)
            left_type = self._closed_expression_type(
                snapshot,
                expression[:offset],
                left_span,
            )
            right_type = self._closed_expression_type(
                snapshot,
                expression[offset + len(operator) :],
                right_span,
            )
            if operator in _EQUALITY:
                if left_type == right_type and left_type in _BOUNDED_EQUALITY_TYPES:
                    return "Bool"
                return None
            if operator in _ORDERING and left_type == right_type == "Int":
                return "Bool"
            return None

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is not None:
            operator, offset = split
            left_span = Span(span.start, span.start + offset)
            right_span = Span(span.start + offset + len(operator), span.end)
            left_type = self._closed_expression_type(
                snapshot,
                expression[:offset],
                left_span,
            )
            right_type = self._closed_expression_type(
                snapshot,
                expression[offset + len(operator) :],
                right_span,
            )
            if left_type == right_type == "Int":
                return "Int"
            if operator == "+" and left_type == right_type == "String":
                return "String"
            return None

        unary = self._unary_negation_operand(expression, span)
        if unary is not None:
            operand, operand_span = unary
            return (
                "Int"
                if self._closed_expression_type(snapshot, operand, operand_span)
                == "Int"
                else None
            )

        if expression.startswith("!") and not expression.startswith("!="):
            operand, operand_span = self._trim_expression(
                expression[1:],
                Span(span.start + 1, span.end),
            )
            if not operand:
                return None
            return (
                "Bool"
                if self._closed_expression_type(snapshot, operand, operand_span)
                == "Bool"
                else None
            )

        return super()._closed_argument_type(snapshot, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        text = snapshot.symbols.syntax.document.text
        expression = text[argument.start : argument.end]

        present, logical_type = self._logical_type_if_present(
            snapshot, expression, argument
        )
        if present:
            return logical_type

        present, comparison_type = self._comparison_type_if_present(
            snapshot, expression, argument
        )
        if present:
            return comparison_type
        return self._integer_arithmetic_type(snapshot, expression, argument)

    def _inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Infer one bounded expression using Nova operator precedence."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        parts = self._split_top_level_logical(expression, span, "||")
        if parts is not None:
            if not parts:
                return None
            return self._all_bool_parts_type(semantic, parts, resolving)

        parts = self._split_top_level_logical(expression, span, "&&")
        if parts is not None:
            if not parts:
                return None
            return self._all_bool_parts_type(semantic, parts, resolving)

        operators = self._top_level_comparison_operators(expression)
        if operators:
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
            if left_type == right_type == "Int":
                return "Int"
            if operator == "+" and left_type == right_type == "String":
                return "String"
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

        if expression.startswith("!") and not expression.startswith("!="):
            operand, operand_span = self._trim_expression(
                expression[1:], Span(span.start + 1, span.end)
            )
            if not operand:
                return None
            operand_type = self._inference_expression_type(
                semantic,
                operand,
                operand_span,
                resolving,
            )
            return "Bool" if operand_type == "Bool" else None

        return super()._inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _integer_arithmetic_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        """Infer bounded Int arithmetic and String concatenation results."""
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
            return super()._return_expression_type(semantic, expression, span)

        operator, offset = split
        left_text = expression[:offset]
        right_text = expression[offset + len(operator) :]
        left_span = Span(span.start, span.start + offset)
        right_span = Span(span.start + offset + len(operator), span.end)
        left_type = self._integer_arithmetic_type(semantic, left_text, left_span)
        right_type = self._integer_arithmetic_type(semantic, right_text, right_span)
        if left_type == right_type == "Int":
            return "Int"
        if operator == "+" and left_type == right_type == "String":
            return "String"
        return None

    def _comparison_inference_operand_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Preserve Unit and the historical arithmetic-only comparison operand scope."""
        if expression.strip() == "()":
            return "Unit"
        return self._arithmetic_inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _arithmetic_inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Apply the former arithmetic-layer inference behavior without another class."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is None:
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
        if left_type == right_type == "Int":
            return "Int"
        if operator == "+" and left_type == right_type == "String":
            return "String"
        return None

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

    def _logical_type_if_present(
        self, semantic: Any, expression: str, span: Span
    ) -> tuple[bool, str | None]:
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        has_binary_logical = (
            self._split_top_level_logical(expression, span, "||") is not None
            or self._split_top_level_logical(expression, span, "&&") is not None
        )
        if has_binary_logical:
            return True, self._inference_expression_type(
                semantic,
                expression,
                span,
                frozenset(),
            )

        if expression.startswith("!") and not expression.startswith("!="):
            # Unary ! binds tighter than equality/ordering. A top-level comparison
            # therefore owns `!value == other`; only a grouped comparison such as
            # `!(value == other)` belongs to unary logical typing.
            if self._top_level_comparison_operators(expression):
                return False, None
            return True, self._inference_expression_type(
                semantic,
                expression,
                span,
                frozenset(),
            )
        return False, None

    def _all_bool_parts_type(
        self,
        semantic: Any,
        parts: tuple[tuple[str, Span], ...],
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        for part_text, part_span in parts:
            if (
                self._inference_expression_type(
                    semantic,
                    part_text,
                    part_span,
                    resolving,
                )
                != "Bool"
            ):
                return None
        return "Bool"

    def _has_logical_operator(self, expression: str) -> bool:
        normalized = self._trim_expression(expression, Span(0, len(expression)))[0]
        normalized = self._unwrap_expression_with_span(
            normalized, Span(0, len(normalized))
        )[0]
        if normalized.startswith("!") and not normalized.startswith("!="):
            return True
        return (
            self._split_top_level_logical(
                normalized,
                Span(0, len(normalized)),
                "||",
            )
            is not None
            or self._split_top_level_logical(
                normalized,
                Span(0, len(normalized)),
                "&&",
            )
            is not None
        )

    def _split_top_level_logical(
        self,
        expression: str,
        span: Span,
        operator: str,
    ) -> tuple[tuple[str, Span], ...] | None:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        offsets: list[int] = []
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
            if depth == 0 and code.startswith(operator, offset):
                offsets.append(offset)
                offset += len(operator)
                continue
            offset += 1

        if depth != 0 or not offsets:
            return None

        parts: list[tuple[str, Span]] = []
        start = 0
        for operator_offset in (*offsets, len(expression)):
            end = operator_offset
            part_text = expression[start:end]
            part_span = Span(span.start + start, span.start + end)
            trimmed_text, trimmed_span = self._trim_expression(part_text, part_span)
            if not trimmed_text:
                return ()
            parts.append((trimmed_text, trimmed_span))
            start = end + len(operator)
        return tuple(parts)

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
            if char in {"+", "-"} and self._is_unary_sign(expression, offset):
                continue
            candidate = (char, offset)
        return candidate

    def _unary_negation_operand(
        self, expression: str, span: Span
    ) -> tuple[str, Span] | None:
        """Return Nova's unary `-` operand while rejecting unsupported unary `+`."""
        code = self.nova_adapter.code_view(expression)
        if not code or code[0] != "-":
            return None
        operand, operand_span = self._trim_expression(
            expression[1:], Span(span.start + 1, span.end)
        )
        if not operand:
            return None
        return operand, operand_span

    @staticmethod
    def _is_unary_sign(expression: str, offset: int) -> bool:
        prefix = expression[:offset].rstrip()
        if not prefix:
            return True
        return prefix[-1] in "(,+-*/%!&|<>=:"
