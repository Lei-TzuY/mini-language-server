"""Bounded exact-snapshot Nova break/continue semantics."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .condition_diagnostics import _FUNCTION, ControlFlowNovaFunctionAdapter
from .diagnostics import Diagnostic
from .inferred_return_call_hierarchy import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .semantic import SemanticSnapshot
from .source import Span

_LOOP_CONTROL = re.compile(r"\b(?P<keyword>break|continue)\b")
_WHILE = re.compile(r"\bwhile\s*\(")
_INVALID_LOOP_CONTROL_DIAGNOSTIC = "nova.invalid-loop-control"


class LoopControlNovaFunctionAdapter(ControlFlowNovaFunctionAdapter):
    """Treat break/continue as control-flow syntax rather than unresolved names."""

    @classmethod
    def parse(cls, text: str):
        tree = super().parse(text)
        return replace(
            tree,
            unresolved_names=tuple(
                item
                for item in tree.unresolved_names
                if item.name not in {"break", "continue"}
            ),
        )


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded loop-control validation and repair."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = LoopControlNovaFunctionAdapter()

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _INVALID_LOOP_CONTROL_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_loop_control_diagnostics(semantic)
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
        current = self.diagnostics.get(uri)
        if current is None or current.semantic.symbols.syntax.document is not document:
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code != _INVALID_LOOP_CONTROL_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
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
            actions.append(
                {
                    "title": "Remove invalid loop-control statement",
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

    def _nova_loop_control_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for function in _FUNCTION.finditer(code):
            opening = function.end() - 1
            closing = self._matching_delimiter(code, opening, "{", "}")
            if closing is None:
                continue
            body_start = opening + 1
            body_code = code[body_start:closing]
            loop_bodies = self._while_body_ranges(body_code)
            for statement in _LOOP_CONTROL.finditer(body_code):
                if any(start < statement.start() < end for start, end in loop_bodies):
                    continue
                statement_end = self._loop_control_statement_end(
                    body_code, statement.end()
                )
                keyword = statement.group("keyword")
                diagnostics.append(
                    Diagnostic(
                        span=Span(
                            body_start + statement.start(),
                            body_start + statement_end,
                        ),
                        message=f"'{keyword}' is only valid inside a while loop",
                        code=_INVALID_LOOP_CONTROL_DIAGNOSTIC,
                        source="nova",
                    )
                )
        return tuple(diagnostics)

    def _while_body_ranges(self, code: str) -> tuple[tuple[int, int], ...]:
        ranges: list[tuple[int, int]] = []
        for statement in _WHILE.finditer(code):
            condition_open = statement.end() - 1
            condition_close = self._matching_delimiter(code, condition_open, "(", ")")
            if condition_close is None:
                continue
            body_open = self._next_non_space(code, condition_close + 1)
            if body_open is None or code[body_open] != "{":
                continue
            body_close = self._matching_delimiter(code, body_open, "{", "}")
            if body_close is not None:
                ranges.append((body_open, body_close))
        return tuple(ranges)

    @staticmethod
    def _loop_control_statement_end(code: str, keyword_end: int) -> int:
        cursor = keyword_end
        while cursor < len(code) and code[cursor] in {" ", "\t"}:
            cursor += 1
        if cursor < len(code) and code[cursor] == ";":
            return cursor + 1
        return keyword_end
