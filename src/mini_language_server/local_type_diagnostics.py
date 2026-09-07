"""Bounded exact-snapshot diagnostics for explicit Nova local annotations."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diagnostics import Diagnostic
from .return_type_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ANNOTATED_INITIALIZER = re.compile(
    rf"\s*:\s*(?P<expected>{_IDENTIFIER}|!)\s*=\s*"
    rf"(?P<value>-?[0-9]+|true\b|false\b|"
    rf'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|{_IDENTIFIER})'
)
_LOCAL_TYPE_DIAGNOSTIC = "nova.local-type"
_SUPPORTED_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded explicit-local initializer validation."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _LOCAL_TYPE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_local_type_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_local_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        diagnostics: list[Diagnostic] = []
        for symbol in semantic.symbols.symbols:
            if symbol.kind != "variable":
                continue
            match = _ANNOTATED_INITIALIZER.match(text, symbol.span.end)
            if match is None:
                continue
            expected = match.group("expected")
            if expected not in _SUPPORTED_TYPES:
                continue
            value = match.group("value")
            value_span = Span(match.start("value"), match.end("value"))
            actual = self._return_expression_type(semantic, value, value_span)
            if actual is None or actual == expected:
                continue
            diagnostics.append(
                Diagnostic(
                    span=value_span,
                    message=(
                        f"local type mismatch: expected '{expected}', got '{actual}'"
                    ),
                    code=_LOCAL_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)
