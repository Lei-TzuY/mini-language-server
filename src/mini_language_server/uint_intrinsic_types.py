"""Bounded exact-snapshot UInt intrinsic typing for Nova."""

from __future__ import annotations

import re
from typing import Any

from .assignment_diagnostics import _ASSIGNMENT
from .diagnostics import Diagnostic
from .inferred_function_returns import _LOCAL_TYPE_SUFFIX
from .local_type_diagnostics import _ANNOTATED_INITIALIZER_PREFIX
from .return_types import _TYPED_FUNCTION
from .semantic import SemanticSnapshot
from .source import Span
from .tail_expression_inference import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_UINT_CONSTANT = re.compile(r"\s*UInt\s*::\s*(?:MIN|MAX)\s*")
_UINT_CONSTANT_IN_SOURCE = re.compile(r"\bUInt\s*::\s*(?:MIN|MAX)\b")
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")
_MISSING_RETURN_DIAGNOSTIC = "nova.missing-return"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative UInt constant/result typing."""

    def _literal_type(self, expression: str) -> str | None:
        if _UINT_CONSTANT.fullmatch(expression) is not None:
            return "UInt"
        return super()._literal_type(expression)

    @classmethod
    def _is_literal_unresolved_name(cls, text: str, diagnostic: Diagnostic) -> bool:
        if super()._is_literal_unresolved_name(text, diagnostic):
            return True
        if diagnostic.code != "nova.unresolved-name":
            return False
        return any(
            match.start() <= diagnostic.span.start
            and diagnostic.span.end <= match.end()
            for match in _UINT_CONSTANT_IN_SOURCE.finditer(text)
        )

    def _function_call_return_type(self, expression: str) -> str | None:
        """Extend exact-workspace call typing with explicit UInt results."""
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
        if annotation is None or annotation.group("type") != "UInt":
            return None
        return "UInt"

    def _explicit_target_return_type(self, semantic: Any, target: Any) -> str | None:
        inherited = super()._explicit_target_return_type(semantic, target)
        if inherited is not None:
            return inherited

        if target.kind == "parameter":
            return "UInt" if self._parameter_type(semantic, target) == "UInt" else None
        if target.kind != "variable":
            return None
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        annotation = _LOCAL_TYPE_SUFFIX.match(code, target.span.end)
        if annotation is None or annotation.group("type") != "UInt":
            return None
        return "UInt"

    def _nova_local_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_local_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        for symbol in semantic.symbols.symbols:
            if symbol.kind != "variable":
                continue
            match = _ANNOTATED_INITIALIZER_PREFIX.match(text, symbol.span.end)
            if match is None or match.group("expected") != "UInt":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            value, value_span = initializer
            actual = self._return_expression_type(semantic, value, value_span)
            if actual is None or actual == "UInt":
                continue
            diagnostics.append(
                Diagnostic(
                    span=value_span,
                    message=f"local type mismatch: expected 'UInt', got '{actual}'",
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
            if self._assignment_target_type(semantic, target) != "UInt":
                continue
            initializer = self._local_initializer(text, match.end())
            if initializer is None:
                continue
            expression, expression_span = initializer
            actual = self._return_expression_type(semantic, expression, expression_span)
            if actual is None or actual == "UInt":
                continue
            diagnostics.append(
                Diagnostic(
                    span=expression_span,
                    message=f"assignment type mismatch: expected 'UInt', got '{actual}'",
                    code="nova.assignment-type",
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _nova_return_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(super()._nova_return_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)

        for function in _TYPED_FUNCTION.finditer(code):
            if function.group("type") != "UInt":
                continue
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue
            body_text = text[opening + 1 : closing]
            body_code = code[opening + 1 : closing]
            if self._body_guarantees_value_return(body_code, body_text):
                continue

            tail = self._bounded_tail_expression(text, code, opening + 1, closing)
            if tail is not None:
                expression, expression_span = tail
                if self._return_expression_type(semantic, expression, expression_span) is not None:
                    continue

            type_span = Span(function.start("type"), function.end("type"))
            if any(
                diagnostic.code == _MISSING_RETURN_DIAGNOSTIC
                and diagnostic.span == type_span
                for diagnostic in diagnostics
            ):
                continue
            diagnostics.append(
                Diagnostic(
                    span=type_span,
                    message=(
                        f"function '{function.group('name')}' with return type "
                        "'UInt' has no value return"
                    ),
                    code=_MISSING_RETURN_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(diagnostics)
