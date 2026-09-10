"""Bounded exact-snapshot Nova division-by-zero diagnostics."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .diagnostics import Diagnostic
from .loop_control import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_ZERO_DIVISOR = re.compile(r"(?P<operator>/|%)\s*(?P<divisor>[+-]?0)(?![A-Za-z0-9_.])")
_DIVISION_BY_ZERO_DIAGNOSTIC = "nova.division-by-zero"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative literal-zero divisor diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _DIVISION_BY_ZERO_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_division_by_zero_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        current = self.diagnostics.get(uri)
        if current is None or current.semantic.symbols.syntax.document is not document:
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code != _DIVISION_BY_ZERO_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if start_offset == end_offset:
                overlaps = diagnostic.span.start <= start_offset <= diagnostic.span.end
            else:
                overlaps = (
                    diagnostic.span.start < end_offset
                    and start_offset < diagnostic.span.end
                )
            if not overlaps:
                continue
            actions.append(
                {
                    "title": "Replace zero divisor with 1",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "1",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _nova_division_by_zero_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for match in _ZERO_DIVISOR.finditer(code):
            divisor_start, divisor_end = match.span("divisor")
            operator = match.group("operator")
            operation = "division" if operator == "/" else "remainder"
            diagnostics.append(
                Diagnostic(
                    span=Span(divisor_start, divisor_end),
                    message=f"integer {operation} by zero is invalid",
                    code=_DIVISION_BY_ZERO_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)
