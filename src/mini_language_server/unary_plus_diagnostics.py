"""Exact-snapshot diagnostics for unsupported Nova unary plus expressions."""

from __future__ import annotations

from collections.abc import Iterable

from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .semantic_token_modifiers import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_UNARY_PLUS_DIAGNOSTIC = "nova.unary-plus"
_UNARY_PRECEDERS = frozenset("({[=,:;!+-*/%&|<>")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative unsupported-unary-plus diagnostics."""

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
