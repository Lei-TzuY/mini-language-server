"""Exact-snapshot quick fix for unsupported Nova unary plus expressions."""

from __future__ import annotations

from typing import Any

from .diagnostics import Diagnostic
from .unary_plus_diagnostics import (
    _UNARY_PLUS_DIAGNOSTIC,
    NovaProductLanguageServer as _NovaProductLanguageServer,
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with a deterministic unary-plus removal repair."""

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
            if diagnostic.code != _UNARY_PLUS_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            actions.append(
                {
                    "title": "Remove unsupported unary '+'",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    @staticmethod
    def _diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
