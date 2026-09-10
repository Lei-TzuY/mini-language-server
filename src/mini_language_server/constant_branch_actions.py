"""Exact-snapshot structural repairs for bounded Nova constant dead branches."""

from __future__ import annotations

from typing import Any

from .constant_condition_diagnostics import (
    _BOOLEAN_LITERALS,
    _CONTROL_FLOW_CONDITION,
    _UNREACHABLE_CODE_DIAGNOSTIC,
)
from .constant_condition_diagnostics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .diagnostics import Diagnostic
from .source import Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with semantic-preserving constant-branch quick fixes."""

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
        diagnostic_snapshot = self.diagnostics.get(uri)
        if (
            semantic is None
            or diagnostic_snapshot is None
            or semantic.symbols.syntax.document is not document
            or diagnostic_snapshot.semantic is not semantic
        ):
            return actions

        code = self.nova_adapter.code_view(document.text)
        current = diagnostic_snapshot.diagnostics
        for diagnostic in diagnostics:
            if diagnostic.code != _UNREACHABLE_CODE_DIAGNOSTIC:
                continue
            if not any(diagnostic is candidate for candidate in current):
                continue
            if not self._constant_branch_action_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue

            repair = self._constant_dead_branch_repair(code, diagnostic)
            if repair is None:
                continue
            title, edit_span = repair
            actions.append(
                {
                    "title": title,
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, edit_span),
                                    "newText": "",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _constant_dead_branch_repair(
        self, code: str, diagnostic: Diagnostic
    ) -> tuple[str, Span] | None:
        for match in _CONTROL_FLOW_CONDITION.finditer(code):
            opening = match.end() - 1
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue

            expression = code[opening + 1 : closing]
            expression_span = Span(opening + 1, closing)
            expression, expression_span = self._trim_expression(
                expression, expression_span
            )
            expression, expression_span = self._unwrap_expression_with_span(
                expression, expression_span
            )
            if expression not in _BOOLEAN_LITERALS:
                continue

            body_open = self._next_non_space(code, closing + 1)
            if body_open is None or code[body_open] != "{":
                continue
            body_close = self._matching_delimiter(code, body_open, "{", "}")
            if body_close is None:
                continue

            kind = match.group("kind")
            if expression == "false":
                body_span = self._trim_dead_body_span(code, body_open + 1, body_close)
                if body_span != diagnostic.span:
                    continue
                if kind == "while":
                    return (
                        "Remove unreachable constant-false while",
                        Span(match.start(), body_close + 1),
                    )
                if self._is_else_if(code, match.start()):
                    return None
                else_keyword = self._next_non_space(code, body_close + 1)
                if else_keyword is not None and self._keyword_at(
                    code, else_keyword, "else"
                ):
                    return None
                return (
                    "Remove unreachable constant-false if",
                    Span(match.start(), body_close + 1),
                )

            if kind != "if":
                continue
            else_keyword = self._next_non_space(code, body_close + 1)
            if else_keyword is None or not self._keyword_at(code, else_keyword, "else"):
                continue
            else_body_open = self._next_non_space(code, else_keyword + len("else"))
            if else_body_open is None or code[else_body_open] != "{":
                continue
            else_body_close = self._matching_delimiter(
                code, else_body_open, "{", "}"
            )
            if else_body_close is None:
                continue
            else_span = self._trim_dead_body_span(
                code, else_body_open + 1, else_body_close
            )
            if else_span != diagnostic.span:
                continue
            return (
                "Remove unreachable constant-true else",
                Span(else_keyword, else_body_close + 1),
            )

        return None

    @staticmethod
    def _is_else_if(code: str, if_offset: int) -> bool:
        cursor = if_offset - 1
        while cursor >= 0 and code[cursor].isspace():
            cursor -= 1
        end = cursor + 1
        while cursor >= 0 and (code[cursor].isalnum() or code[cursor] == "_"):
            cursor -= 1
        return code[cursor + 1 : end] == "else"

    @staticmethod
    def _constant_branch_action_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
