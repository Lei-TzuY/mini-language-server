"""Exact-snapshot Nova quick fixes for duplicate declarations."""

from __future__ import annotations

from typing import Any

from .diagnostics import Diagnostic
from .local_type_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax

_DUPLICATE_CODES = frozenset(
    {
        "nova.duplicate-function",
        "nova.duplicate-parameter",
        "nova.duplicate-variable",
    }
)
_KIND_BY_CODE = {
    "nova.duplicate-function": "function",
    "nova.duplicate-parameter": "parameter",
    "nova.duplicate-variable": "local",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with deterministic duplicate-declaration repairs."""

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
        parsed = semantic.symbols.syntax.tree
        if not isinstance(parsed, NovaFunctionSyntax):
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code not in _DUPLICATE_CODES:
                continue
            if not self._duplicate_diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            name = document.text[diagnostic.span.start : diagnostic.span.end]
            if not name:
                continue
            replacement = self._duplicate_replacement_name(
                parsed, diagnostic.code, diagnostic.span, name
            )
            if replacement is None:
                continue
            kind = _KIND_BY_CODE[diagnostic.code]
            actions.append(
                {
                    "title": f"Rename duplicate {kind} '{name}' to '{replacement}'",
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
    def _duplicate_replacement_name(
        parsed: NovaFunctionSyntax, code: str, span: Any, name: str
    ) -> str | None:
        if code == "nova.duplicate-function":
            if not any(item_span == span for _, item_span in parsed.declarations):
                return None
            occupied = {item_name for item_name, _ in parsed.declarations}
        else:
            scoped = (
                parsed.parameters
                if code == "nova.duplicate-parameter"
                else parsed.locals
            )
            target = next((item for item in scoped if item.span == span), None)
            if target is None:
                return None
            occupied = {
                item.name
                for item in (*parsed.parameters, *parsed.locals)
                if item.owner == target.owner
            }

        suffix = 2
        candidate = f"{name}_{suffix}"
        while candidate in occupied:
            suffix += 1
            candidate = f"{name}_{suffix}"
        return candidate

    @staticmethod
    def _duplicate_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
