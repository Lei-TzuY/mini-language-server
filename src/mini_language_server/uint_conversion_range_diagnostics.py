"""Exact-snapshot diagnostics for provably out-of-range Nova numeric conversions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diagnostics import Diagnostic
from .division_diagnostics import bounded_integer_constant_value
from .semantic import SemanticSnapshot
from .uint_intrinsic_semantic_tokens import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)

_CONVERSION_RANGE_DIAGNOSTIC = "nova.conversion-range"
_UINT_MAX = (1 << 64) - 1
_INT_MAX = (1 << 63) - 1
_UINT_MAX_MEMBER = re.compile(r"UInt\s*::\s*MAX\Z")
_UINT_MIN_MEMBER = re.compile(r"UInt\s*::\s*MIN\Z")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative checked-conversion range diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _CONVERSION_RANGE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_conversion_range_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_conversion_range_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        diagnostics: list[Diagnostic] = []
        for call in self._conversion_calls(text):
            if len(call.arguments) != 1:
                continue
            argument, argument_span = call.arguments[0]
            code = self.nova_adapter.code_view(argument).strip()

            if call.name == "UInt::from":
                value = bounded_integer_constant_value(code)
                if value is None or 0 <= value <= _UINT_MAX:
                    continue
                diagnostics.append(
                    Diagnostic(
                        span=argument_span,
                        message=(
                            "checked conversion 'UInt::from' cannot represent "
                            f"constant Int value {value} as UInt"
                        ),
                        code=_CONVERSION_RANGE_DIAGNOSTIC,
                        source="nova",
                    )
                )
                continue

            member = _strip_balanced_outer_parentheses(code)
            if _UINT_MIN_MEMBER.fullmatch(member):
                continue
            if not _UINT_MAX_MEMBER.fullmatch(member):
                continue
            diagnostics.append(
                Diagnostic(
                    span=argument_span,
                    message=(
                        "checked conversion 'Int::from_uint' cannot represent "
                        f"UInt::MAX ({_UINT_MAX}) as Int; maximum is {_INT_MAX}"
                    ),
                    code=_CONVERSION_RANGE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)


def _strip_balanced_outer_parentheses(expression: str) -> str:
    value = expression.strip()
    while value.startswith("(") and value.endswith(")"):
        depth = 0
        closes_at_end = False
        for index, char in enumerate(value):
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth < 0:
                    return value
                if depth == 0:
                    closes_at_end = index == len(value) - 1
                    break
        if not closes_at_end:
            return value
        value = value[1:-1].strip()
    return value
