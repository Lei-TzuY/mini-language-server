"""Nova unresolved-name quick fixes over exact diagnostic snapshots."""

from __future__ import annotations

import re
from typing import Any

from .linked_editing import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import SourceText, Span

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final product server with executable unresolved-local quick fixes."""

    def _nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: SourceText,
        diagnostics: tuple[Any, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.unresolved-name":
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

            name = document.text[diagnostic.span.start : diagnostic.span.end]
            if _IDENTIFIER.fullmatch(name) is None:
                continue

            insertion = Span(diagnostic.span.start, diagnostic.span.start)
            actions.append(
                {
                    "title": f"Declare local '{name}'",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, insertion),
                                    "newText": f"let {name} = 0 ",
                                }
                            ]
                        }
                    },
                }
            )
        return actions
