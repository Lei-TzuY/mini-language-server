"""Exact-snapshot quick fixes for Nova return type mismatches."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .return_types import NovaProductLanguageServer as _NovaProductLanguageServer

_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}
_EXPECTED_TYPE = re.compile(r"^return type mismatch: expected '([^']+)', got '[^']+'$")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with executable return-type mismatch repairs."""

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
        if semantic is None or semantic.symbols.syntax.document is not document:
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code != "nova.return-type":
                continue
            if not self._return_diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            match = _EXPECTED_TYPE.fullmatch(diagnostic.message)
            if match is None:
                continue
            expected_type = match.group(1)
            replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
            if replacement is None:
                continue
            actions.append(
                {
                    "title": f"Replace return expression with {expected_type} literal",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": replacement,
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    @staticmethod
    def _return_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
