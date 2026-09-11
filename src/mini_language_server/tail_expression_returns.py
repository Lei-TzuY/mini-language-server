"""Bounded Nova function tail-expression return validation."""

from __future__ import annotations

from .diagnostics import Diagnostic
from .return_types import _TYPED_FUNCTION
from .semantic import SemanticSnapshot
from .source import Span
from .unit_return_inference import NovaProductLanguageServer as _NovaProductLanguageServer

_RETURN_TYPE_DIAGNOSTIC = "nova.return-type"
_MISSING_RETURN_DIAGNOSTIC = "nova.missing-return"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative function tail-expression semantics."""

    def _nova_return_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        inherited = list(super()._nova_return_type_diagnostics(semantic))
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)

        for function in _TYPED_FUNCTION.finditer(code):
            expected = function.group("type")
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue

            body_text = text[opening + 1 : closing]
            body_code = code[opening + 1 : closing]
            if self._body_guarantees_value_return(body_code, body_text):
                continue

            tail = self._bounded_tail_expression(text, code, opening + 1, closing)
            if tail is None:
                continue
            expression, expression_span = tail
            actual = self._return_expression_type(semantic, expression, expression_span)
            if actual is None:
                continue

            type_span = Span(function.start("type"), function.end("type"))
            inherited = [
                diagnostic
                for diagnostic in inherited
                if not (
                    diagnostic.code == _MISSING_RETURN_DIAGNOSTIC
                    and diagnostic.span == type_span
                )
            ]
            if actual == expected:
                continue
            if any(
                diagnostic.code == _RETURN_TYPE_DIAGNOSTIC
                and diagnostic.span == expression_span
                for diagnostic in inherited
            ):
                continue
            inherited.append(
                Diagnostic(
                    span=expression_span,
                    message=(
                        f"return type mismatch: expected '{expected}', got '{actual}'"
                    ),
                    code=_RETURN_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )

        return tuple(inherited)

    @staticmethod
    def _bounded_tail_expression(
        text: str, code: str, start: int, end: int
    ) -> tuple[str, Span] | None:
        """Return one top-level final expression after the last statement terminator.

        This intentionally does not parse arbitrary Nova blocks. It only isolates the
        final top-level source region after the last top-level semicolon, then lets the
        existing exact-snapshot expression typing decide whether that region is a
        supported expression. Nested delimiters are ignored while locating statement
        boundaries so semicolons inside nested blocks cannot manufacture a tail.
        """
        candidate_start = start
        brace_depth = 0
        paren_depth = 0
        bracket_depth = 0
        for offset in range(start, end):
            character = code[offset]
            if character == "{":
                brace_depth += 1
            elif character == "}" and brace_depth > 0:
                brace_depth -= 1
            elif character == "(":
                paren_depth += 1
            elif character == ")" and paren_depth > 0:
                paren_depth -= 1
            elif character == "[":
                bracket_depth += 1
            elif character == "]" and bracket_depth > 0:
                bracket_depth -= 1
            elif (
                character == ";"
                and brace_depth == 0
                and paren_depth == 0
                and bracket_depth == 0
            ):
                candidate_start = offset + 1

        while candidate_start < end and code[candidate_start].isspace():
            candidate_start += 1
        candidate_end = end
        while candidate_end > candidate_start and code[candidate_end - 1].isspace():
            candidate_end -= 1
        if candidate_start >= candidate_end:
            return None

        expression = text[candidate_start:candidate_end]
        return expression, Span(candidate_start, candidate_end)
