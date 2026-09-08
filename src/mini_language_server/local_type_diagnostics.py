"""Bounded exact-snapshot diagnostics and repairs for explicit Nova locals."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .diagnostics import Diagnostic
from .return_type_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ANNOTATED_INITIALIZER_PREFIX = re.compile(
    rf"\s*:\s*(?P<expected>{_IDENTIFIER}|!)\s*=\s*"
)
_LOCAL_TYPE_DIAGNOSTIC = "nova.local-type"
_LOCAL_TYPE_MESSAGE = re.compile(
    r"^local type mismatch: expected '([^']+)', got '[^']+'$"
)
_SUPPORTED_TYPES = frozenset({"Int", "String", "Bool"})
_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with explicit-local validation and repairs."""

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
            match = _ANNOTATED_INITIALIZER_PREFIX.match(text, symbol.span.end)
            if match is None:
                continue
            expected = match.group("expected")
            if expected not in _SUPPORTED_TYPES:
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            value, value_span = initializer
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

    def _local_initializer(self, text: str, start: int) -> tuple[str, Span] | None:
        while start < len(text) and text[start].isspace():
            start += 1
        if start >= len(text):
            return None

        tail = text[start:]
        code = self.nova_adapter.code_view(tail)
        depth = 0
        boundary = len(code)
        for offset, char in enumerate(code):
            if char == "(":
                depth += 1
                continue
            if char == ")":
                if depth == 0:
                    boundary = offset
                    break
                depth -= 1
                continue
            if depth == 0 and char in "\n;}":
                boundary = offset
                break

        meaningful = code[:boundary].rstrip()
        if not meaningful:
            return None
        end = start + len(meaningful)
        return text[start:end], Span(start, end)

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
            if diagnostic.code != _LOCAL_TYPE_DIAGNOSTIC:
                continue
            if not self._local_type_diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            match = _LOCAL_TYPE_MESSAGE.fullmatch(diagnostic.message)
            if match is None:
                continue
            expected_type = match.group(1)
            replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
            if replacement is None:
                continue
            actions.append(
                {
                    "title": (
                        f"Replace local initializer with {expected_type} literal"
                    ),
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
    def _local_type_diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
