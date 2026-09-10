"""Exact-snapshot diagnostics for bounded Nova local declaration shapes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .assignment_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_UNINITIALIZED_LOCAL = re.compile(
    rf"\b(?P<keyword>let|var)\s+(?P<name>{_IDENTIFIER})"
    rf"(?:\s*:\s*(?P<type>{_IDENTIFIER}|!))?\s*;"
)
_ASSIGNMENT_SUFFIX = re.compile(r"\s*=(?!=)")
_UNINITIALIZED_LET_DIAGNOSTIC = "nova.uninitialized-let"
_UNTYPED_VAR_DIAGNOSTIC = "nova.untyped-var"
_UNINITIALIZED_READ_DIAGNOSTIC = "nova.uninitialized-read"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded local declaration validation."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code
            not in {
                _UNINITIALIZED_LET_DIAGNOSTIC,
                _UNTYPED_VAR_DIAGNOSTIC,
                _UNINITIALIZED_READ_DIAGNOSTIC,
            }
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_local_declaration_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_local_declaration_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []

        for match in _UNINITIALIZED_LOCAL.finditer(code):
            keyword = match.group("keyword")
            name = match.group("name")
            if keyword == "let":
                diagnostics.append(
                    Diagnostic(
                        span=Span(*match.span("keyword")),
                        message=f"immutable local '{name}' requires an initializer",
                        code=_UNINITIALIZED_LET_DIAGNOSTIC,
                        source="nova",
                    )
                )
            elif match.group("type") is None:
                diagnostics.append(
                    Diagnostic(
                        span=Span(*match.span("name")),
                        message=f"uninitialized mutable local '{name}' requires an explicit type",
                        code=_UNTYPED_VAR_DIAGNOSTIC,
                        source="nova",
                    )
                )
            else:
                diagnostics.extend(
                    self._nova_reads_before_first_assignment(semantic, code, match)
                )

        return tuple(diagnostics)

    @staticmethod
    def _brace_scope_at(code: str, offset: int) -> tuple[int, ...]:
        """Return the structural brace ancestry containing ``offset``."""
        scope: list[int] = []
        for index, char in enumerate(code[:offset]):
            if char == "{":
                scope.append(index)
            elif char == "}" and scope:
                scope.pop()
        return tuple(scope)

    @classmethod
    def _nova_reads_before_first_assignment(
        cls, semantic: SemanticSnapshot, code: str, declaration: re.Match[str]
    ) -> tuple[Diagnostic, ...]:
        name_span = Span(*declaration.span("name"))
        target = next(
            (
                symbol
                for symbol in semantic.symbols.symbols
                if symbol.kind == "variable" and symbol.span == name_span
            ),
            None,
        )
        if target is None:
            return ()

        references = sorted(
            (
                reference
                for reference in semantic.references
                if reference.target is target and reference.span.start > declaration.end()
            ),
            key=lambda reference: reference.span.start,
        )
        assignments: list[tuple[int, tuple[int, ...]]] = []
        diagnostics: list[Diagnostic] = []
        for reference in references:
            reference_scope = cls._brace_scope_at(code, reference.span.start)
            if _ASSIGNMENT_SUFFIX.match(code, reference.span.end):
                assignments.append((reference.span.start, reference_scope))
                continue

            definitely_initialized = any(
                assignment_start < reference.span.start
                and len(assignment_scope) <= len(reference_scope)
                and reference_scope[: len(assignment_scope)] == assignment_scope
                for assignment_start, assignment_scope in assignments
            )
            if definitely_initialized:
                continue
            diagnostics.append(
                Diagnostic(
                    span=reference.span,
                    message=f"local '{target.name}' is read before its first assignment",
                    code=_UNINITIALIZED_READ_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

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

        code = self.nova_adapter.code_view(document.text)
        for diagnostic in diagnostics:
            if diagnostic.code != _UNINITIALIZED_LET_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if start_offset == end_offset:
                overlaps = diagnostic.span.start <= start_offset <= diagnostic.span.end
            else:
                overlaps = diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
            if not overlaps:
                continue

            declaration = next(
                (
                    match
                    for match in _UNINITIALIZED_LOCAL.finditer(code)
                    if match.span("keyword") == (diagnostic.span.start, diagnostic.span.end)
                ),
                None,
            )
            if declaration is None or declaration.group("type") is None:
                continue
            actions.append(
                {
                    "title": "Change uninitialized let declaration to var",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, diagnostic.span),
                                    "newText": "var",
                                }
                            ]
                        }
                    },
                }
            )
        return actions
