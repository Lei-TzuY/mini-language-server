"""Consolidated exact-snapshot UInt typing for Nova.

UInt constants, arithmetic/comparisons, and explicit Int/UInt conversions share
one product layer. New UInt type semantics belong here or in pure helpers.
"""

from __future__ import annotations

import re
from typing import Any

from .assignment_diagnostics import _ASSIGNMENT
from .diagnostics import Diagnostic
from .expression_types import (
    _ADDITIVE,
    _BOUNDED_EQUALITY_TYPES,
    _EQUALITY,
    _MULTIPLICATIVE,
    _ORDERING,
)
from .inferred_function_returns import _LOCAL_TYPE_SUFFIX
from .local_type_diagnostics import _ANNOTATED_INITIALIZER_PREFIX
from .return_types import _TYPED_FUNCTION
from .semantic import SemanticSnapshot
from .source import Span
from .tail_expression_inference import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_UINT_CONSTANT = re.compile(r"\s*UInt\s*::\s*(?:MIN|MAX)\s*")
_UINT_CONSTANT_IN_SOURCE = re.compile(r"\bUInt\s*::\s*(?:MIN|MAX)\b")
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")
_MISSING_RETURN_DIAGNOSTIC = "nova.missing-return"

_UINT_NUMERIC_TYPES = frozenset({"Int", "UInt"})
_UINT_EQUALITY_TYPES = _BOUNDED_EQUALITY_TYPES | {"UInt"}
_UINT_FROM = re.compile(r"\s*UInt\s*::\s*from\s*\((?P<argument>.*)\)\s*", re.DOTALL)
_INT_FROM_UINT = re.compile(
    r"\s*Int\s*::\s*from_uint\s*\((?P<argument>.*)\)\s*", re.DOTALL
)
_CONVERSION_IN_SOURCE = re.compile(
    r"\b(?:UInt\s*::\s*from|Int\s*::\s*from_uint)\s*\([^;\n]*\)"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Nova product with one conservative UInt typing boundary."""

    def _literal_type(self, expression: str) -> str | None:
        if _UINT_CONSTANT.fullmatch(expression) is not None:
            return "UInt"
        return super()._literal_type(expression)

    @classmethod
    def _is_literal_unresolved_name(cls, text: str, diagnostic: Diagnostic) -> bool:
        if super()._is_literal_unresolved_name(text, diagnostic):
            return True
        if diagnostic.code != "nova.unresolved-name":
            return False
        return any(
            match.start() <= diagnostic.span.start
            and diagnostic.span.end <= match.end()
            for match in _UINT_CONSTANT_IN_SOURCE.finditer(text)
        ) or any(
            match.start() <= diagnostic.span.start
            and diagnostic.span.end <= match.end()
            for match in _CONVERSION_IN_SOURCE.finditer(text)
        )

    def _integer_arithmetic_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        conversion = self._conversion_bounds(expression, span)
        if conversion is not None:
            target, argument, argument_span = conversion
            argument_type = self._integer_arithmetic_type(
                semantic,
                argument,
                argument_span,
            )
            if target == "UInt" and argument_type == "Int":
                return "UInt"
            if target == "Int" and argument_type == "UInt":
                return "Int"
            return None
        return self._uint_numeric_arithmetic_type(semantic, expression, span)

    def _inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        conversion = self._conversion_bounds(expression, span)
        if conversion is not None:
            target, argument, argument_span = conversion
            argument_type = self._inference_expression_type(
                semantic,
                argument,
                argument_span,
                resolving,
            )
            if target == "UInt" and argument_type == "Int":
                return "UInt"
            if target == "Int" and argument_type == "UInt":
                return "Int"
            return None
        return self._uint_numeric_inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _conversion_bounds(
        self, expression: str, span: Span
    ) -> tuple[str, str, Span] | None:
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)
        code = self.nova_adapter.code_view(expression)

        for target, pattern in (("UInt", _UINT_FROM), ("Int", _INT_FROM_UINT)):
            match = pattern.fullmatch(code)
            if match is None:
                continue
            start, end = match.span("argument")
            argument, argument_span = self._trim_expression(
                expression[start:end],
                Span(span.start + start, span.start + end),
            )
            if not argument or self._has_top_level_comma(argument):
                return None
            return target, argument, argument_span
        return None

    def _has_top_level_comma(self, expression: str) -> bool:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        for char in code:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return True
            elif char == "," and depth == 0:
                return True
        return depth != 0

    def _uint_numeric_arithmetic_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        """Infer Int/UInt arithmetic plus bounded String concatenation."""
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
        if operator == "+" and left_type == right_type == "String":
            return "String"
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

    def _uint_numeric_inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Propagate UInt numeric and String concatenation results."""
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

        return super()._inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _function_call_return_type(self, expression: str) -> str | None:
        """Extend exact-workspace call typing with explicit UInt results."""
        resolved = super()._function_call_return_type(expression)
        if resolved is not None:
            return resolved

        expression = self._unwrap_parenthesized_expression(expression)
        code = self.nova_adapter.code_view(expression)
        match = _CALL_EXPRESSION.match(code)
        if match is None:
            return None
        parsed = self._call_argument_bounds(expression, match.end("name"))
        if parsed is None:
            return None
        closing = parsed[1]
        if code[closing + 1 :].strip():
            return None

        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(match.group("name"))
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return None
        signature = self._function_signature(declarations[0])
        annotation = _RETURN_ANNOTATION.search(signature)
        if annotation is None or annotation.group("type") != "UInt":
            return None
        return "UInt"

    def _explicit_target_return_type(self, semantic: Any, target: Any) -> str | None:
        inherited = super()._explicit_target_return_type(semantic, target)
        if inherited is not None:
            return inherited

        if target.kind == "parameter":
            return "UInt" if self._parameter_type(semantic, target) == "UInt" else None
        if target.kind != "variable":
            return None
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        annotation = _LOCAL_TYPE_SUFFIX.match(code, target.span.end)
        if annotation is None or annotation.group("type") != "UInt":
            return None
        return "UInt"

    def _nova_local_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_local_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        for symbol in semantic.symbols.symbols:
            if symbol.kind != "variable":
                continue
            match = _ANNOTATED_INITIALIZER_PREFIX.match(text, symbol.span.end)
            if match is None or match.group("expected") != "UInt":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            value, value_span = initializer
            actual = self._return_expression_type(semantic, value, value_span)
            if actual is None or actual == "UInt":
                continue
            diagnostics.append(
                Diagnostic(
                    span=value_span,
                    message=f"local type mismatch: expected 'UInt', got '{actual}'",
                    code="nova.local-type",
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _nova_assignment_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_assignment_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        references = {
            (reference.span.start, reference.span.end): reference.target
            for reference in semantic.references
        }
        for match in _ASSIGNMENT.finditer(code):
            lhs_span = Span(*match.span("name"))
            target = references.get((lhs_span.start, lhs_span.end))
            if target is None or target.kind not in {"variable", "parameter"}:
                continue
            if self._assignment_target_type(semantic, target) != "UInt":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            expression, expression_span = initializer
            actual = self._return_expression_type(semantic, expression, expression_span)
            if actual is None or actual == "UInt":
                continue
            diagnostics.append(
                Diagnostic(
                    span=expression_span,
                    message=f"assignment type mismatch: expected 'UInt', got '{actual}'",
                    code="nova.assignment-type",
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _nova_return_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_return_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)

        for function in _TYPED_FUNCTION.finditer(code):
            if function.group("type") != "UInt":
                continue
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue
            body_text = text[opening + 1 : closing]
            body_code = code[opening + 1 : closing]
            if self._body_guarantees_value_return(body_code, body_text):
                continue

            tail = self._bounded_tail_expression(text, code, opening + 1, closing)
            if tail is not None:
                expression, expression_span = tail
                if self._return_expression_type(semantic, expression, expression_span) is not None:
                    continue

            type_span = Span(function.start("type"), function.end("type"))
            if any(
                diagnostic.code == _MISSING_RETURN_DIAGNOSTIC
                and diagnostic.span == type_span
                for diagnostic in diagnostics
            ):
                continue
            diagnostics.append(
                Diagnostic(
                    span=type_span,
                    message=(
                        f"function '{function.group('name')}' with return type "
                        "'UInt' has no value return"
                    ),
                    code=_MISSING_RETURN_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(diagnostics)
