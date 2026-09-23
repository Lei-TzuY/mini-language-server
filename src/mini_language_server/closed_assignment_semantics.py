"""Detached closed-workspace Nova assignment diagnostics and repairs."""

from __future__ import annotations

import re
from typing import Any

from .assignment_diagnostics import (
    _ASSIGNMENT,
    _ASSIGNMENT_TYPE_DIAGNOSTIC,
    _IMMUTABLE_ASSIGNMENT_DIAGNOSTIC,
    _SUPPORTED_TYPES,
)
from .closed_definite_initialization import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .diagnostics import Diagnostic
from .source import Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Promote bounded assignment type and mutability semantics into detached ownership."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics = [
            diagnostic
            for diagnostic in super()._closed_workspace_product_diagnostics(
                snapshot,
                functions,
            )
            if diagnostic.code
            not in {
                _ASSIGNMENT_TYPE_DIAGNOSTIC,
                _IMMUTABLE_ASSIGNMENT_DIAGNOSTIC,
            }
        ]

        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        references = {
            (reference.span.start, reference.span.end): reference.target
            for reference in snapshot.references
        }

        for match in _ASSIGNMENT.finditer(code):
            lhs_span = Span(*match.span("name"))
            target = references.get((lhs_span.start, lhs_span.end))
            if target is None or target.kind not in {"variable", "parameter"}:
                continue

            declaration = self._local_declaration_keyword(text, target)
            if target.kind == "variable" and declaration is not None:
                keyword, _ = declaration
                if keyword == "let":
                    diagnostics.append(
                        Diagnostic(
                            span=lhs_span,
                            message=(
                                f"cannot assign to immutable local "
                                f"'{match.group('name')}'"
                            ),
                            code=_IMMUTABLE_ASSIGNMENT_DIAGNOSTIC,
                            source="nova",
                        )
                    )

            expected = self._closed_assignment_target_type(
                snapshot,
                target,
                functions,
            )
            if expected not in _SUPPORTED_TYPES:
                continue

            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            expression, expression_span = initializer
            actual = self._closed_expression_type(
                snapshot,
                expression,
                expression_span,
                functions,
                frozenset(),
            )
            if actual is None or actual == expected:
                continue
            diagnostics.append(
                Diagnostic(
                    span=expression_span,
                    message=(
                        f"assignment type mismatch: expected '{expected}', "
                        f"got '{actual}'"
                    ),
                    code=_ASSIGNMENT_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(diagnostics)

    def _closed_assignment_target_type(
        self,
        snapshot: Any,
        target: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> str | None:
        text = snapshot.symbols.syntax.document.text
        explicit = self._explicit_assignment_target_type(text, target)
        if explicit is not None:
            return explicit
        if target.kind != "variable":
            return None
        return self._closed_local_type(
            snapshot,
            target,
            frozenset(),
            functions,
        )

    def _closed_nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._closed_nova_code_actions(
            uri,
            document,
            source,
            diagnostics,
            start_offset,
            end_offset,
        )
        code = self.nova_adapter.code_view(document.text)

        for diagnostic in diagnostics:
            if diagnostic.code not in {
                _ASSIGNMENT_TYPE_DIAGNOSTIC,
                _IMMUTABLE_ASSIGNMENT_DIAGNOSTIC,
            }:
                continue
            if not self._assignment_diagnostic_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue

            if diagnostic.code == _ASSIGNMENT_TYPE_DIAGNOSTIC:
                action = self._assignment_type_action(uri, source, diagnostic)
                if action is not None:
                    actions.append(action)
                continue

            keyword_span = self._closed_immutable_declaration_keyword(
                code,
                document.text,
                diagnostic,
            )
            if keyword_span is None:
                continue
            actions.append(
                self._immutable_assignment_action(
                    uri,
                    source,
                    diagnostic,
                    keyword_span,
                )
            )

        return actions

    def _closed_immutable_declaration_keyword(
        self,
        code: str,
        text: str,
        diagnostic: Diagnostic,
    ) -> Span | None:
        name = text[diagnostic.span.start : diagnostic.span.end]
        if not name.isidentifier():
            return None

        reference_scope = self._brace_scope_at(code, diagnostic.span.start)
        pattern = re.compile(rf"\blet\s+(?P<name>{re.escape(name)})\b")
        candidates = [
            match
            for match in pattern.finditer(code, 0, diagnostic.span.start)
            if self._declaration_visible_from_scope(
                code,
                match.start("name"),
                reference_scope,
            )
        ]
        if len(candidates) != 1:
            return None
        match = candidates[0]
        return Span(match.start(), match.start() + len("let"))
