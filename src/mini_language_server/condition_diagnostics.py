"""Bounded exact-snapshot diagnostics for Nova control-flow conditions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .diagnostics import Diagnostic
from .expression_local_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .return_types import ReturnTypeNovaFunctionAdapter
from .semantic import SemanticSnapshot
from .source import Span

_CONTROL_FLOW_CONDITION = re.compile(r"\b(?:if|while)\s*\(")
_CONDITION_TYPE_DIAGNOSTIC = "nova.condition-type"
_CONTROL_FLOW_NAMES = frozenset({"if", "while"})


class ControlFlowNovaFunctionAdapter(ReturnTypeNovaFunctionAdapter):
    """Recognize bounded control-flow forms without treating them as function calls."""

    @classmethod
    def parse(cls, text: str):
        tree = super().parse(text)
        calls = tuple(
            (name, span) for name, span in tree.calls if name not in _CONTROL_FLOW_NAMES
        )
        if calls == tree.calls:
            return tree
        return replace(tree, calls=calls)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with Bool validation for bounded control-flow conditions."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = ControlFlowNovaFunctionAdapter()

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _CONDITION_TYPE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_condition_type_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

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
            if diagnostic.code != _CONDITION_TYPE_DIAGNOSTIC:
                continue
            if not self._condition_diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue

            expression = document.text[diagnostic.span.start : diagnostic.span.end]
            actual = self._return_expression_type(semantic, expression, diagnostic.span)
            typed_repair: tuple[str, str] | None = None
            if actual == "Int":
                typed_repair = ("Compare Int condition with zero", f"({expression}) != 0")
            elif actual == "String":
                typed_repair = (
                    "Compare String condition with empty string",
                    f'({expression}) != ""',
                )
            if typed_repair is not None:
                title, new_text = typed_repair
                actions.append(
                    {
                        "title": title,
                        "kind": "quickfix",
                        "diagnostics": [self._diagnostic(source, diagnostic)],
                        "edit": {
                            "changes": {
                                uri: [
                                    {
                                        "range": self._range(source, diagnostic.span),
                                        "newText": new_text,
                                    }
                                ]
                            }
                        },
                    }
                )

            actions.append(
                {
                    "title": "Replace condition with Bool literal",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "false",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _nova_condition_type_diagnostics(
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
            if not expression:
                continue
            actual = self._return_expression_type(semantic, expression, span)
            if actual is None or actual == "Bool":
                continue
            diagnostics.append(
                Diagnostic(
                    span=span,
                    message=(
                        "condition type mismatch: expected 'Bool', "
                        f"got '{actual}'"
                    ),
                    code=_CONDITION_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

    @staticmethod
    def _condition_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
