"""Bounded exact-snapshot diagnostics for Nova control-flow conditions."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .diagnostics import DIAGNOSTIC_TAG_VALUES, Diagnostic
from .expression_local_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .return_types import ReturnTypeNovaFunctionAdapter
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FUNCTION = re.compile(
    rf"\bfn\s+(?P<name>{_IDENTIFIER})\s*\([^)]*\)"
    rf"(?:\s*->\s*(?:{_IDENTIFIER}|!))?\s*\{{"
)
_CONTROL_FLOW_CONDITION = re.compile(r"\b(?:if|while)\s*\(")
_IF = re.compile(r"\bif\s*\(")
_RETURN = re.compile(r"\breturn\b")
_CONDITION_TYPE_DIAGNOSTIC = "nova.condition-type"
_UNREACHABLE_CODE_DIAGNOSTIC = "nova.unreachable-code"
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
    """Final Nova product with bounded control-flow diagnostics."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = ControlFlowNovaFunctionAdapter()

    def _diagnostic(self, source: Any, diagnostic: Diagnostic) -> dict[str, Any]:
        rendered = super()._diagnostic(source, diagnostic)
        if diagnostic.tags:
            rendered["tags"] = [DIAGNOSTIC_TAG_VALUES[tag] for tag in diagnostic.tags]
        return rendered

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code
            not in {_CONDITION_TYPE_DIAGNOSTIC, _UNREACHABLE_CODE_DIAGNOSTIC}
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_condition_type_diagnostics(semantic)
            materialized += self._nova_unreachable_code_diagnostics(semantic)
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

    def _nova_unreachable_code_diagnostics(
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
            body_code = code[opening + 1 : closing]
            body_text = text[opening + 1 : closing]
            termination_end = self._first_guaranteed_termination_end(body_code, body_text)
            if termination_end is None:
                continue
            unreachable_start = self._next_non_space(body_code, termination_end)
            if unreachable_start is None:
                continue
            unreachable_end = len(body_code)
            while unreachable_end > unreachable_start and body_code[
                unreachable_end - 1
            ].isspace():
                unreachable_end -= 1
            if unreachable_end <= unreachable_start:
                continue
            diagnostics.append(
                Diagnostic(
                    span=Span(
                        opening + 1 + unreachable_start,
                        opening + 1 + unreachable_end,
                    ),
                    message="unreachable code after guaranteed return",
                    code=_UNREACHABLE_CODE_DIAGNOSTIC,
                    source="nova",
                    tags=("unnecessary",),
                )
            )
        return tuple(diagnostics)

    def _first_guaranteed_termination_end(self, code: str, text: str) -> int | None:
        candidates: list[tuple[int, int]] = []
        for statement in _RETURN.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            candidates.append(
                (statement.start(), self._return_statement_end(code, statement.end()))
            )

        for statement in _IF.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if not self._if_statement_guarantees_termination(
                code, text, statement.start(), statement.end()
            ):
                continue
            statement_end = self._if_statement_end(
                code, statement.start(), statement.end()
            )
            if statement_end is not None:
                candidates.append((statement.start(), statement_end))

        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate[0])[1]

    def _body_guarantees_termination(self, code: str, text: str) -> bool:
        for statement in _RETURN.finditer(code):
            if self._brace_depth_before(code, statement.start()) == 0:
                return True
        for statement in _IF.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if self._if_statement_guarantees_termination(
                code, text, statement.start(), statement.end()
            ):
                return True
        return False

    def _if_statement_guarantees_termination(
        self, code: str, text: str, statement_start: int, condition_prefix_end: int
    ) -> bool:
        branches = self._if_then_else_bounds(code, statement_start, condition_prefix_end)
        if branches is None:
            return False
        then_open, then_close, else_start = branches
        if not self._body_guarantees_termination(
            code[then_open + 1 : then_close], text[then_open + 1 : then_close]
        ):
            return False
        if code[else_start] == "{":
            else_close = self._matching_delimiter(code, else_start, "{", "}")
            if else_close is None:
                return False
            return self._body_guarantees_termination(
                code[else_start + 1 : else_close], text[else_start + 1 : else_close]
            )
        nested_if = _IF.match(code, else_start)
        if nested_if is None:
            return False
        return self._if_statement_guarantees_termination(
            code, text, nested_if.start(), nested_if.end()
        )

    def _if_statement_end(
        self, code: str, statement_start: int, condition_prefix_end: int
    ) -> int | None:
        branches = self._if_then_else_bounds(code, statement_start, condition_prefix_end)
        if branches is None:
            return None
        _, _, else_start = branches
        if code[else_start] == "{":
            else_close = self._matching_delimiter(code, else_start, "{", "}")
            return None if else_close is None else else_close + 1
        nested_if = _IF.match(code, else_start)
        if nested_if is None:
            return None
        return self._if_statement_end(code, nested_if.start(), nested_if.end())

    @staticmethod
    def _return_statement_end(code: str, keyword_end: int) -> int:
        boundary = len(code)
        for delimiter in (";", "\n", "\r"):
            found = code.find(delimiter, keyword_end)
            if found >= 0:
                boundary = min(boundary, found + 1)
        return boundary

    @staticmethod
    def _condition_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
