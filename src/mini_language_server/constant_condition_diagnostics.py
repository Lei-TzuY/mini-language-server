"""Bounded exact-snapshot diagnostics for provably constant Nova conditions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diagnostics import Diagnostic
from .division_diagnostics import bounded_integer_constant_value
from .loop_exit_initialization import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_CONTROL_FLOW_CONDITION = re.compile(r"\b(?P<kind>if|while)\s*\(")
_CONSTANT_CONDITION_DIAGNOSTIC = "nova.constant-condition"
_UNREACHABLE_CODE_DIAGNOSTIC = "nova.unreachable-code"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative constant-expression diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _CONSTANT_CONDITION_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_constant_condition_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_constant_condition_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []

        for match in _CONTROL_FLOW_CONDITION.finditer(code):
            opening = match.end() - 1
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue

            expression = text[opening + 1 : closing]
            span = Span(opening + 1, closing)
            expression, span = self._trim_expression(expression, span)
            expression, span = self._unwrap_expression_with_span(expression, span)
            constant = self._bounded_boolean_constant_value(
                code[span.start : span.end]
            )
            if constant is None:
                continue
            value = "true" if constant else "false"

            diagnostics.append(
                Diagnostic(
                    span=span,
                    message=f"{match.group('kind')} condition is always {value}",
                    code=_CONSTANT_CONDITION_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(diagnostics)

    def _nova_unreachable_code_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_unreachable_code_diagnostics(semantic))
        document = semantic.symbols.syntax.document
        if document.language_id != self.nova_adapter.language_id:
            return tuple(diagnostics)

        for diagnostic in self._nova_constant_dead_branch_diagnostics(semantic):
            if any(
                existing.span.start <= diagnostic.span.start
                and diagnostic.span.end <= existing.span.end
                for existing in diagnostics
            ):
                continue
            diagnostics = [
                existing
                for existing in diagnostics
                if not (
                    diagnostic.span.start <= existing.span.start
                    and existing.span.end <= diagnostic.span.end
                )
            ]
            diagnostics.append(diagnostic)

        diagnostics.sort(key=lambda item: (item.span.start, item.span.end, item.message))
        return tuple(diagnostics)

    def _nova_constant_dead_branch_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []

        for match in _CONTROL_FLOW_CONDITION.finditer(code):
            opening = match.end() - 1
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue

            expression = text[opening + 1 : closing]
            expression_span = Span(opening + 1, closing)
            expression, expression_span = self._trim_expression(
                expression, expression_span
            )
            expression, expression_span = self._unwrap_expression_with_span(
                expression, expression_span
            )
            constant = self._bounded_boolean_constant_value(
                code[expression_span.start : expression_span.end]
            )
            if constant is None:
                continue

            body_open = self._next_non_space(code, closing + 1)
            if body_open is None or code[body_open] != "{":
                continue
            body_close = self._matching_delimiter(code, body_open, "{", "}")
            if body_close is None:
                continue

            kind = match.group("kind")
            if not constant:
                body_span = self._trim_dead_body_span(code, body_open + 1, body_close)
                if body_span is not None:
                    diagnostics.append(
                        Diagnostic(
                            span=body_span,
                            message=(
                                "unreachable code in constant-false "
                                f"{kind} body"
                            ),
                            code=_UNREACHABLE_CODE_DIAGNOSTIC,
                            source="nova",
                            tags=("unnecessary",),
                        )
                    )
                continue

            if kind != "if":
                continue
            else_keyword = self._next_non_space(code, body_close + 1)
            if else_keyword is None or not self._keyword_at(code, else_keyword, "else"):
                continue
            else_body_open = self._next_non_space(code, else_keyword + len("else"))
            if else_body_open is None or code[else_body_open] != "{":
                continue
            else_body_close = self._matching_delimiter(
                code, else_body_open, "{", "}"
            )
            if else_body_close is None:
                continue
            else_span = self._trim_dead_body_span(
                code, else_body_open + 1, else_body_close
            )
            if else_span is not None:
                diagnostics.append(
                    Diagnostic(
                        span=else_span,
                        message="unreachable code in constant-true else body",
                        code=_UNREACHABLE_CODE_DIAGNOSTIC,
                        source="nova",
                        tags=("unnecessary",),
                    )
                )

        return tuple(diagnostics)

    def _bounded_boolean_constant_value(self, expression: str) -> bool | None:
        """Evaluate only the bounded constant grammar proven by current Nova syntax."""
        if not expression.strip():
            return None
        expression, span = self._trim_expression(
            expression, Span(0, len(expression))
        )
        expression, _ = self._unwrap_expression_with_span(expression, span)
        if expression == "true":
            return True
        if expression == "false":
            return False

        parts = self._split_top_level_logical(
            expression, Span(0, len(expression)), "||"
        )
        if parts is not None:
            if not parts:
                return None
            values = [
                self._bounded_boolean_constant_value(part)
                for part, _ in parts
            ]
            return None if any(value is None for value in values) else any(values)

        parts = self._split_top_level_logical(
            expression, Span(0, len(expression)), "&&"
        )
        if parts is not None:
            if not parts:
                return None
            values = [
                self._bounded_boolean_constant_value(part)
                for part, _ in parts
            ]
            return None if any(value is None for value in values) else all(values)

        comparisons = self._top_level_comparison_operators(expression)
        if comparisons:
            if len(comparisons) != 1:
                return None
            operator, offset = comparisons[0]
            left = expression[:offset]
            right = expression[offset + len(operator) :]

            left_integer = self._bounded_condition_integer_value(left)
            right_integer = self._bounded_condition_integer_value(right)
            if left_integer is not None and right_integer is not None:
                if operator == "==":
                    return left_integer == right_integer
                if operator == "!=":
                    return left_integer != right_integer
                if operator == "<":
                    return left_integer < right_integer
                if operator == "<=":
                    return left_integer <= right_integer
                if operator == ">":
                    return left_integer > right_integer
                if operator == ">=":
                    return left_integer >= right_integer
                return None

            if operator not in {"==", "!="}:
                return None
            left_boolean = self._bounded_boolean_constant_value(left)
            right_boolean = self._bounded_boolean_constant_value(right)
            if left_boolean is None or right_boolean is None:
                return None
            return (
                left_boolean == right_boolean
                if operator == "=="
                else left_boolean != right_boolean
            )

        if expression.startswith("!") and not expression.startswith("!="):
            operand = expression[1:]
            value = self._bounded_boolean_constant_value(operand)
            return None if value is None else not value

        return None

    @classmethod
    def _bounded_condition_integer_value(cls, expression: str) -> int | None:
        """Reject Nova-invalid unary plus before reusing bounded integer folding."""
        if cls._contains_unsupported_unary_plus(expression):
            return None
        return bounded_integer_constant_value(expression)

    @staticmethod
    def _contains_unsupported_unary_plus(expression: str) -> bool:
        unary_preceders = frozenset("({[=,:;!+-*/%&|<>")
        for offset, character in enumerate(expression):
            if character != "+":
                continue
            cursor = offset - 1
            while cursor >= 0 and expression[cursor].isspace():
                cursor -= 1
            if cursor < 0 or expression[cursor] in unary_preceders:
                return True
        return False

    @staticmethod
    def _keyword_at(code: str, offset: int, keyword: str) -> bool:
        if not code.startswith(keyword, offset):
            return False
        end = offset + len(keyword)
        if offset > 0 and (code[offset - 1].isalnum() or code[offset - 1] == "_"):
            return False
        return end >= len(code) or not (code[end].isalnum() or code[end] == "_")

    @staticmethod
    def _trim_dead_body_span(code: str, start: int, end: int) -> Span | None:
        while start < end and code[start].isspace():
            start += 1
        while end > start and code[end - 1].isspace():
            end -= 1
        return None if end <= start else Span(start, end)
