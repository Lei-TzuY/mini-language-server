"""Exact-snapshot quick fixes for bounded Nova unreachable-code diagnostics."""

from __future__ import annotations

from typing import Any

from .condition_diagnostics import _UNREACHABLE_CODE_DIAGNOSTIC
from .condition_diagnostics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .diagnostics import Diagnostic


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with exact-snapshot unreachable-code repairs."""

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
        semantic = self.semantics.get(uri)
        diagnostic_snapshot = self.diagnostics.get(uri)
        if (
            semantic is None
            or diagnostic_snapshot is None
            or semantic.symbols.syntax.document is not document
            or diagnostic_snapshot.semantic is not semantic
        ):
            return actions

        current = diagnostic_snapshot.diagnostics
        for diagnostic in diagnostics:
            if diagnostic.code != _UNREACHABLE_CODE_DIAGNOSTIC:
                continue
            if not any(diagnostic is candidate for candidate in current):
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            actions.append(
                {
                    "title": "Remove unreachable code",
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
