"""Bounded exact-snapshot Unit literal and result typing for Nova."""

from __future__ import annotations

import re
from typing import Any

from .assignment_diagnostics import _ASSIGNMENT
from .constant_branch_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import Diagnostic
from .local_type_diagnostics import _ANNOTATED_INITIALIZER_PREFIX
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative Unit literal/result typing."""

    def _literal_type(self, expression: str) -> str | None:
        if expression.strip() == "()":
            return "Unit"
        return super()._literal_type(expression)

    def _return_expression_type(
        self, semantic: SemanticSnapshot, expression: str, span: Span
    ) -> str | None:
        if expression.strip() == "()":
            return "Unit"
        return super()._return_expression_type(semantic, expression, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        text = snapshot.symbols.syntax.document.text
        if text[argument.start : argument.end].strip() == "()":
            return "Unit"
        return super()._argument_type(snapshot, argument)

    def _nova_local_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_local_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        for symbol in semantic.symbols.symbols:
            if symbol.kind != "variable":
                continue
            match = _ANNOTATED_INITIALIZER_PREFIX.match(text, symbol.span.end)
            if match is None or match.group("expected") != "Unit":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            value, value_span = initializer
            actual = self._return_expression_type(semantic, value, value_span)
            if actual is None or actual == "Unit":
                continue
            diagnostics.append(
                Diagnostic(
                    span=value_span,
                    message=f"local type mismatch: expected 'Unit', got '{actual}'",
                    code="nova.local-type",
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _nova_assignment_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_assignment_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        references = {
            (reference.span.start, reference.span.end): reference.target
            for reference in semantic.references
        }
        for match in _ASSIGNMENT.finditer(code):
            lhs_span = Span(*match.span("name"))
            target = references.get((lhs_span.start, lhs_span.end))
            if target is None or target.kind not in {"variable", "parameter"}:
                continue
            if self._assignment_target_type(semantic, target) != "Unit":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            expression, expression_span = initializer
            actual = self._return_expression_type(semantic, expression, expression_span)
            if actual is None or actual == "Unit":
                continue
            diagnostics.append(
                Diagnostic(
                    span=expression_span,
                    message=f"assignment type mismatch: expected 'Unit', got '{actual}'",
                    code="nova.assignment-type",
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _function_call_return_type(self, expression: str) -> str | None:
        """Extend exact-workspace call typing with explicit Unit results."""
        resolved = super()._function_call_return_type(expression)
        if resolved is not None:
            return resolved

        expression = self._unwrap_parenthesized_expression(expression)
        code = self.nova_adapter.code_view(expression)
        match = _CALL_EXPRESSION.match(code)
        if match is None:
            return None
        parsed = self._call_argument_bounds(expression, match.end("name"))
        if parsed is None:
            return None
        closing = parsed[1]
        if code[closing + 1 :].strip():
            return None

        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(match.group("name"))
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return None
        signature = self._function_signature(declarations[0])
        annotation = _RETURN_ANNOTATION.search(signature)
        if annotation is None or annotation.group("type") != "Unit":
            return None
        return "Unit"
