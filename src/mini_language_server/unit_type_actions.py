"""Exact-snapshot quick fixes for bounded Nova Unit type mismatches."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .unit_types import NovaProductLanguageServer as _NovaProductLanguageServer

_UNIT_DIAGNOSTIC_MESSAGES = {
    "nova.argument-type": re.compile(
        r"^argument \d+ to '[^']+' has type '[^']+'; expected 'Unit'$"
    ),
    "nova.return-type": re.compile(
        r"^return type mismatch: expected 'Unit', got '[^']+'$"
    ),
    "nova.local-type": re.compile(
        r"^local type mismatch: expected 'Unit', got '[^']+'$"
    ),
    "nova.assignment-type": re.compile(
        r"^assignment type mismatch: expected 'Unit', got '[^']+'$"
    ),
}

_UNIT_ACTION_TITLES = {
    "nova.argument-type": "Replace argument with Unit literal",
    "nova.return-type": "Replace return expression with Unit literal",
    "nova.local-type": "Replace local initializer with Unit literal",
    "nova.assignment-type": "Replace assignment value with Unit literal",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with deterministic Unit mismatch repairs."""

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
            matcher = _UNIT_DIAGNOSTIC_MESSAGES.get(diagnostic.code or "")
            if matcher is None or matcher.fullmatch(diagnostic.message) is None:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if not self._unit_diagnostic_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue
            actions.append(
                {
                    "title": _UNIT_ACTION_TITLES[diagnostic.code],
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "()",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    @staticmethod
    def _unit_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
