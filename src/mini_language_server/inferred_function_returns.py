"""Bounded Nova function result inference from consistent return expressions."""

from __future__ import annotations

import re

from .function_call_locals import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN = re.compile(r"\breturn\b")
_RETURN_ANNOTATION = re.compile(r"->")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative bounded function-result inference."""

    def _function_call_return_type(self, expression: str) -> str | None:
        """Resolve explicit results first, then one unannotated bounded-return function."""
        explicit = super()._function_call_return_type(expression)
        if explicit is not None:
            return explicit

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
        declaration = declarations[0]

        signature = self._function_signature(declaration)
        if _RETURN_ANNOTATION.search(self.nova_adapter.code_view(signature)) is not None:
            return None
        return self._bounded_function_return_type(declaration)

    def _bounded_function_return_type(self, declaration) -> str | None:
        """Infer a result when every value return has one bounded consistent type."""
        text = declaration.snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        opening = code.find("{", declaration.symbol.span.end)
        if opening < 0:
            return None
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return None

        inferred: str | None = None
        body_code = code[opening + 1 : closing]
        for statement in _RETURN.finditer(body_code):
            keyword_end = opening + 1 + statement.end()
            boundary = closing
            for delimiter in (";", "\n", "\r"):
                found = code.find(delimiter, keyword_end, closing)
                if found >= 0:
                    boundary = min(boundary, found)
            expression = text[keyword_end:boundary].strip()
            if not expression:
                continue
            actual = self._literal_type(expression)
            if actual is None:
                # Deliberately delegate to the explicit-result layer only. Calling this
                # class's resolver here would make inferred wrappers recursively depend
                # on inferred wrappers and would need a separate cycle-safe fixed point.
                actual = super()._function_call_return_type(expression)
            if actual is None:
                return None
            if inferred is None:
                inferred = actual
            elif inferred != actual:
                return None
        return inferred
