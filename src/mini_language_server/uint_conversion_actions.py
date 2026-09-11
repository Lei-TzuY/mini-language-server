"""Exact-snapshot quick fixes for bounded Nova numeric conversions."""

from __future__ import annotations

from typing import Any

from .diagnostics import Diagnostic
from .uint_conversion_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer

_CONVERSION_TYPE_DIAGNOSTIC = "nova.conversion-type"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with safe redundant-conversion repairs."""

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
        snapshot = self.workspace_symbols.get(uri)
        if snapshot is None or snapshot.symbols.syntax.document is not document:
            return actions

        semantic = snapshot
        for diagnostic in diagnostics:
            if diagnostic.code != _CONVERSION_TYPE_DIAGNOSTIC:
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            repair = self._redundant_conversion_repair(
                semantic, document.text, diagnostic
            )
            if repair is None:
                continue
            call_span, argument = repair
            actions.append(
                {
                    "title": "Remove redundant numeric conversion",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, call_span),
                                    "newText": argument,
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _redundant_conversion_repair(
        self, semantic: Any, text: str, diagnostic: Diagnostic
    ) -> tuple[Any, str] | None:
        matches: list[tuple[Any, str]] = []
        for call in self._conversion_calls(text):
            if len(call.arguments) != 1:
                continue
            argument, argument_span = call.arguments[0]
            if argument_span != diagnostic.span:
                continue
            argument_type = self._integer_arithmetic_type(
                semantic, argument, argument_span
            )
            if argument_type != call.target_type:
                continue
            matches.append((call.span, argument))
        if len(matches) != 1:
            return None
        return matches[0]
