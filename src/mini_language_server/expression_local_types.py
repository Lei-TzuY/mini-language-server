"""Exact-snapshot Nova local types from bounded expression initializers."""

from __future__ import annotations

import re
from typing import Any

from .expression_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_UNANNOTATED_INITIALIZER_PREFIX = re.compile(r"\s*=\s*")
_ADDITIVE = frozenset({"+", "-"})
_MULTIPLICATIVE = frozenset({"*", "/", "%"})
_EQUALITY = frozenset({"==", "!="})
_ORDERING = frozenset({"<", "<=", ">", ">="})
_CLOSED_EQUALITY_TYPES = frozenset({"Int", "String", "Bool", "Unit"})
_CLOSED_CALL_RESULT_TYPES = frozenset({"Int", "String", "Bool", "Unit", "UInt"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded expression-derived local type knowledge."""

    def _closed_local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> str | None:
        """Infer detached local expression types from one captured workspace set."""
        inherited = super()._closed_local_type(snapshot, target, seen, functions)
        if inherited is not None:
            return inherited

        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None

        text = snapshot.symbols.syntax.document.text
        prefix = _UNANNOTATED_INITIALIZER_PREFIX.match(text, target.span.end)
        if prefix is None:
            return None
        initializer = self._local_initializer(text, prefix.end())
        if initializer is None:
            return None
        expression, expression_span = initializer
        normalized, normalized_span = self._trim_expression(
            expression, expression_span
        )
        normalized, normalized_span = self._unwrap_expression_with_span(
            normalized, normalized_span
        )

        owns_logical = self._has_logical_operator(normalized)
        owns_comparison = bool(self._top_level_comparison_operators(normalized))
        owns_arithmetic = self._top_level_operator(normalized, _ADDITIVE) is not None
        if not owns_arithmetic:
            owns_arithmetic = (
                self._top_level_operator(normalized, _MULTIPLICATIVE) is not None
            )
        if not owns_logical and not owns_comparison and not owns_arithmetic:
            return None

        return self._closed_expression_type(
            snapshot,
            normalized,
            normalized_span,
            functions,
            seen | {identity},
        )

    def _closed_expression_type(
        self,
        snapshot: Any,
        expression: str,
        span: Span,
        functions: dict[str, list[tuple[Any, Any]]],
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Type one bounded expression using only detached captured evidence."""
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        parts = self._split_top_level_logical(expression, span, "||")
        if parts is not None:
            if not parts:
                return None
            return (
                "Bool"
                if all(
                    self._closed_expression_type(
                        snapshot, part, part_span, functions, seen
                    )
                    == "Bool"
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
                    self._closed_expression_type(
                        snapshot, part, part_span, functions, seen
                    )
                    == "Bool"
                    for part, part_span in parts
                )
                else None
            )

        operators = self._top_level_comparison_operators(expression)
        if operators:
            if len(operators) != 1:
                return None
            operator, offset = operators[0]
            left = self._closed_expression_type(
                snapshot,
                expression[:offset],
                Span(span.start, span.start + offset),
                functions,
                seen,
            )
            right = self._closed_expression_type(
                snapshot,
                expression[offset + len(operator) :],
                Span(span.start + offset + len(operator), span.end),
                functions,
                seen,
            )
            if operator in _EQUALITY:
                if left == right and left in _CLOSED_EQUALITY_TYPES:
                    return "Bool"
                return None
            if operator in _ORDERING and left == right == "Int":
                return "Bool"
            return None

        split = self._top_level_operator(expression, _ADDITIVE)
        if split is None:
            split = self._top_level_operator(expression, _MULTIPLICATIVE)
        if split is not None:
            operator, offset = split
            left = self._closed_expression_type(
                snapshot,
                expression[:offset],
                Span(span.start, span.start + offset),
                functions,
                seen,
            )
            right = self._closed_expression_type(
                snapshot,
                expression[offset + len(operator) :],
                Span(span.start + offset + len(operator), span.end),
                functions,
                seen,
            )
            if left == right == "Int":
                return "Int"
            if operator == "+" and left == right == "String":
                return "String"
            return None

        unary = self._unary_negation_operand(expression, span)
        if unary is not None:
            operand, operand_span = unary
            return (
                "Int"
                if self._closed_expression_type(
                    snapshot, operand, operand_span, functions, seen
                )
                == "Int"
                else None
            )

        if expression.startswith("!") and not expression.startswith("!="):
            operand, operand_span = self._trim_expression(
                expression[1:], Span(span.start + 1, span.end)
            )
            if not operand:
                return None
            return (
                "Bool"
                if self._closed_expression_type(
                    snapshot, operand, operand_span, functions, seen
                )
                == "Bool"
                else None
            )

        literal = self._literal_type(expression)
        if literal is not None:
            return literal

        if expression.isidentifier():
            target = self._exact_reference_target(snapshot, span)
            if target is None:
                return None
            parameter_type = self._parameter_type(snapshot, target)
            if parameter_type is not None:
                return parameter_type
            if target.kind != "variable":
                return None
            return self._closed_local_type(
                snapshot,
                target,
                seen,
                functions,
            )

        code = self.nova_adapter.code_view(expression)
        call = re.match(r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", code)
        if call is None:
            return None
        parsed = self._call_argument_bounds(expression, call.end("name"))
        if parsed is None or code[parsed[1] + 1 :].strip():
            return None
        candidates = functions.get(call.group("name"), [])
        if len(candidates) != 1:
            return None
        candidate_snapshot, candidate_symbol = candidates[0]
        result_type = self._closed_function_result_type(
            candidate_snapshot,
            candidate_symbol.span,
        )
        return (
            result_type
            if result_type in _CLOSED_CALL_RESULT_TYPES
            else None
        )

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer inherited local types, then one bounded expression initializer."""
        inherited = super()._local_type(snapshot, target, seen)
        if inherited is not None:
            return inherited

        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None

        text = snapshot.symbols.syntax.document.text
        prefix = _UNANNOTATED_INITIALIZER_PREFIX.match(text, target.span.end)
        if prefix is None:
            return None
        initializer = self._local_initializer(text, prefix.end())
        if initializer is None:
            return None
        expression, expression_span = initializer

        # Only promote initializer forms owned by the bounded arithmetic/comparison/
        # logical expression layers. Bare identifiers, calls, and literals remain
        # delegated to the inherited exact-snapshot local inference path above.
        normalized, normalized_span = self._trim_expression(
            expression, expression_span
        )
        normalized, normalized_span = self._unwrap_expression_with_span(
            normalized, normalized_span
        )
        owns_logical = self._has_logical_operator(normalized)
        owns_comparison = bool(self._top_level_comparison_operators(normalized))
        owns_arithmetic = self._top_level_operator(normalized, _ADDITIVE) is not None
        if not owns_arithmetic:
            owns_arithmetic = (
                self._top_level_operator(normalized, _MULTIPLICATIVE) is not None
            )
        if not owns_logical and not owns_comparison and not owns_arithmetic:
            return None

        # Reuse the cycle-safe exact-snapshot inference path so function-call operands
        # retain recursive identity and mixed/ambiguous expressions stay unknown.
        return self._inference_expression_type(
            snapshot,
            normalized,
            normalized_span,
            frozenset(),
        )
