"""Unified exact-snapshot diagnostics and quick fix for Nova unary plus.

This module is the consolidation boundary for unary-plus behavior. Detection,
publication, and repair stay together so this feature contributes one runtime
product layer instead of a diagnostic/action inheritance pair.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .semantic_token_modifiers import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_UNARY_PLUS_DIAGNOSTIC = "nova.unary-plus"
_UNARY_PRECEDERS = frozenset("({[=,:;!+-*/%&|<>")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Nova product with conservative unary-plus diagnostics and repair."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _UNARY_PLUS_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_unary_plus_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_unary_plus_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for offset, char in enumerate(code):
            if char != "+" or not _is_unary_plus(code, offset):
                continue
            diagnostics.append(
                Diagnostic(
                    span=Span(offset, offset + 1),
                    message="unary '+' is not supported in Nova",
                    code=_UNARY_PLUS_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

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


def _is_unary_plus(code: str, offset: int) -> bool:
    """Recognize only positions where `+` cannot be a binary Nova operator."""
    cursor = offset - 1
    while cursor >= 0 and code[cursor].isspace():
        cursor -= 1
    if cursor < 0:
        return True
    if code[cursor] in _UNARY_PRECEDERS:
        return True

    prefix = code[:offset].rstrip()
    return prefix.endswith("return") and (
        len(prefix) == len("return")
        or not (prefix[-len("return") - 1].isalnum() or prefix[-len("return") - 1] == "_")
    )
