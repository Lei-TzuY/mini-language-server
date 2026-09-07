"""Exact-snapshot quick fixes for Nova return diagnostics."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .return_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}
_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TYPED_FUNCTION = re.compile(
    rf"\bfn\s+(?P<name>{_IDENTIFIER})\s*\([^)]*\)\s*->\s*(?P<type>{_IDENTIFIER}|!)\s*\{{"
)
_EXPECTED_TYPE = re.compile(r"^return type mismatch: expected '([^']+)', got '[^']+'$")
_MISSING_RETURN = re.compile(
    r"^function '([^']+)' with return type '([^']+)' has no value return$"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with executable return-diagnostic repairs."""

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
            if diagnostic.code not in {"nova.return-type", "nova.missing-return"}:
                continue
            if not self._return_diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            if diagnostic.code == "nova.return-type":
                action = self._return_type_quick_fix(uri, source, diagnostic)
            else:
                action = self._missing_return_quick_fix(
                    uri, document, source, diagnostic
                )
            if action is not None:
                actions.append(action)
        return actions

    def _return_type_quick_fix(
        self, uri: str, source: Any, diagnostic: Diagnostic
    ) -> dict[str, Any] | None:
        match = _EXPECTED_TYPE.fullmatch(diagnostic.message)
        if match is None:
            return None
        expected_type = match.group(1)
        replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
        if replacement is None:
            return None
        return {
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

    def _missing_return_quick_fix(
        self, uri: str, document: Any, source: Any, diagnostic: Diagnostic
    ) -> dict[str, Any] | None:
        message = _MISSING_RETURN.fullmatch(diagnostic.message)
        if message is None:
            return None
        function_name, expected_type = message.groups()
        replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
        if replacement is None:
            return None

        text = document.text
        code = self.nova_adapter.code_view(text)
        for function in _TYPED_FUNCTION.finditer(code):
            if function.group("name") != function_name:
                continue
            if function.group("type") != expected_type:
                continue
            type_span = Span(function.start("type"), function.end("type"))
            if type_span != diagnostic.span:
                continue
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                return None
            insert_offset, new_text = self._missing_return_insert(
                text, opening=opening, closing=closing, replacement=replacement
            )
            insertion_span = Span(insert_offset, insert_offset)
            return {
                "title": f"Add {expected_type} return",
                "kind": "quickfix",
                "diagnostics": [self._diagnostic(source, diagnostic)],
                "edit": {
                    "changes": {
                        uri: [
                            {
                                "range": self._range(source, insertion_span),
                                "newText": new_text,
                            }
                        ]
                    }
                },
            }
        return None

    @staticmethod
    def _missing_return_insert(
        text: str, *, opening: int, closing: int, replacement: str
    ) -> tuple[int, str]:
        body = text[opening + 1 : closing]
        if "\n" not in body and "\r" not in body:
            separator = "" if body.endswith((" ", "\t")) else " "
            suffix = "" if closing > 0 and text[closing - 1].isspace() else " "
            return closing, f"{separator}return {replacement};{suffix}"

        line_start = max(text.rfind("\n", 0, closing), text.rfind("\r", 0, closing)) + 1
        closing_prefix = text[line_start:closing]
        closing_indent = closing_prefix if closing_prefix.strip() == "" else ""
        newline = "\r\n" if "\r\n" in text else "\n"
        return line_start, f"{closing_indent}    return {replacement};{newline}"

    @staticmethod
    def _return_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
