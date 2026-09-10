"""Bounded exact-snapshot diagnostics for typed Nova assignments."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .diagnostics import Diagnostic
from .division_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_ASSIGNMENT = re.compile(rf"\b(?P<name>{_IDENTIFIER})\s*=(?!=)")
_EXPLICIT_TYPE = re.compile(rf"\s*:\s*(?P<type>{_IDENTIFIER}|!)")
_ASSIGNMENT_TYPE_DIAGNOSTIC = "nova.assignment-type"
_SUPPORTED_TYPES = frozenset({"Int", "String", "Bool"})
_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}
_ASSIGNMENT_TYPE_MESSAGE = re.compile(
    r"^assignment type mismatch: expected '([^']+)', got '[^']+'$"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative typed-assignment validation."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code != _ASSIGNMENT_TYPE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_assignment_type_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_assignment_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        references = {
            (reference.span.start, reference.span.end): reference.target
            for reference in semantic.references
        }

        for match in _ASSIGNMENT.finditer(code):
            lhs_span = Span(*match.span("name"))
            target = references.get((lhs_span.start, lhs_span.end))
            if target is None or target.kind not in {"variable", "parameter"}:
                continue
            expected = self._explicit_assignment_target_type(text, target)
            if expected not in _SUPPORTED_TYPES:
                continue

            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            expression, expression_span = initializer
            actual = self._return_expression_type(semantic, expression, expression_span)
            if actual is None or actual == expected:
                continue
            diagnostics.append(
                Diagnostic(
                    span=expression_span,
                    message=(
                        f"assignment type mismatch: expected '{expected}', got '{actual}'"
                    ),
                    code=_ASSIGNMENT_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

    @staticmethod
    def _explicit_assignment_target_type(text: str, target: Any) -> str | None:
        suffix = _EXPLICIT_TYPE.match(text, target.span.end)
        if suffix is None:
            return None
        return suffix.group("type")

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
            if diagnostic.code != _ASSIGNMENT_TYPE_DIAGNOSTIC:
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
            match = _ASSIGNMENT_TYPE_MESSAGE.fullmatch(diagnostic.message)
            if match is None:
                continue
            expected = match.group(1)
            replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected)
            if replacement is None:
                continue
            actions.append(
                {
                    "title": f"Replace assignment value with {expected} literal",
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
