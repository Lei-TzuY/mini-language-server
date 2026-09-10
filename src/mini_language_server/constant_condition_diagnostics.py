"""Bounded exact-snapshot diagnostics for literal Nova control-flow conditions."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .diagnostics import Diagnostic
from .loop_exit_initialization import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_CONTROL_FLOW_CONDITION = re.compile(r"\b(?P<kind>if|while)\s*\(")
_CONSTANT_CONDITION_DIAGNOSTIC = "nova.constant-condition"
_BOOLEAN_LITERALS = frozenset({"true", "false"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative literal constant-condition diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _CONSTANT_CONDITION_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_constant_condition_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_constant_condition_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []

        for match in _CONTROL_FLOW_CONDITION.finditer(code):
            opening = match.end() - 1
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue

            expression = text[opening + 1 : closing]
            span = Span(opening + 1, closing)
            expression, span = self._trim_expression(expression, span)
            expression, span = self._unwrap_expression_with_span(expression, span)
            if expression not in _BOOLEAN_LITERALS:
                continue

            diagnostics.append(
                Diagnostic(
                    span=span,
                    message=f"{match.group('kind')} condition is always {expression}",
                    code=_CONSTANT_CONDITION_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(diagnostics)
