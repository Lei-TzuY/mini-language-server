"""Bounded exact-snapshot typing for Nova explicit Int/UInt conversions."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .source import Span
from .uint_numeric_types import NovaProductLanguageServer as _NovaProductLanguageServer

_UINT_FROM = re.compile(r"\s*UInt\s*::\s*from\s*\((?P<argument>.*)\)\s*", re.DOTALL)
_INT_FROM_UINT = re.compile(
    r"\s*Int\s*::\s*from_uint\s*\((?P<argument>.*)\)\s*", re.DOTALL
)
_CONVERSION_IN_SOURCE = re.compile(
    r"\b(?:UInt\s*::\s*from|Int\s*::\s*from_uint)\s*\([^;\n]*\)"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative explicit numeric conversion typing."""

    @classmethod
    def _is_literal_unresolved_name(cls, text: str, diagnostic: Diagnostic) -> bool:
        if super()._is_literal_unresolved_name(text, diagnostic):
            return True
        if diagnostic.code != "nova.unresolved-name":
            return False
        return any(
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
            argument_type = super()._integer_arithmetic_type(
                semantic,
                argument,
                argument_span,
            )
            if target == "UInt" and argument_type == "Int":
                return "UInt"
            if target == "Int" and argument_type == "UInt":
                return "Int"
            return None
        return super()._integer_arithmetic_type(semantic, expression, span)

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
            argument_type = super()._inference_expression_type(
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
        return super()._inference_expression_type(
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
