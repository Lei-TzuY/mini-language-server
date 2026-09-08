"""Bounded Nova function result inference from consistent return expressions."""

from __future__ import annotations

import re
from typing import Any

from .function_call_locals import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_LOCAL_TYPE_SUFFIX = re.compile(rf"\s*:\s*(?P<type>{_IDENTIFIER}|!)\s*(?==)")
_RETURN = re.compile(r"\breturn\b")
_RETURN_ANNOTATION = re.compile(r"->")
_VALUE_RETURN_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative bounded function-result inference."""

    def _function_call_return_type(self, expression: str) -> str | None:
        """Resolve explicit results first, then an acyclic inferred-wrapper chain."""
        explicit = super()._function_call_return_type(expression)
        if explicit is not None:
            return explicit
        return self._inferred_function_call_return_type(expression, frozenset())

    def _inferred_function_call_return_type(
        self,
        expression: str,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        """Resolve one uniquely named unannotated call through bounded inference."""
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
        return self._bounded_function_return_type(declaration, resolving)

    @staticmethod
    def _inference_identity(declaration: Any) -> tuple[int, int, int]:
        """Bind recursive inference to the exact semantic parent snapshot and symbol."""
        return (
            id(declaration.snapshot),
            declaration.symbol.span.start,
            declaration.symbol.span.end,
        )

    def _bounded_function_return_type(
        self,
        declaration: Any,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> str | None:
        """Infer one bounded type through explicit references and acyclic call chains."""
        identity = self._inference_identity(declaration)
        if identity in resolving:
            return None
        resolving = resolving | {identity}

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
            raw = text[keyword_end:boundary]
            leading = len(raw) - len(raw.lstrip())
            expression = raw.strip()
            if not expression:
                continue
            expression_span = Span(
                keyword_end + leading,
                keyword_end + leading + len(expression),
            )

            actual = self._literal_type(expression)
            if actual is None and re.fullmatch(_IDENTIFIER, expression) is not None:
                actual = self._explicit_reference_return_type(
                    declaration.snapshot, expression_span
                )
            if actual is None:
                actual = super()._function_call_return_type(expression)
            if actual is None:
                actual = self._inferred_function_call_return_type(expression, resolving)
            if actual is None:
                return None
            if inferred is None:
                inferred = actual
            elif inferred != actual:
                return None
        return inferred

    def _explicit_reference_return_type(self, semantic: Any, span: Span) -> str | None:
        """Resolve only explicitly typed parameter/local references to bounded results."""
        target = self._exact_reference_target(semantic, span)
        if target is None:
            return None

        result: str | None = None
        if target.kind == "parameter":
            result = self._parameter_type(semantic, target)
        elif target.kind == "variable":
            text = semantic.symbols.syntax.document.text
            code = self.nova_adapter.code_view(text)
            annotation = _LOCAL_TYPE_SUFFIX.match(code, target.span.end)
            if annotation is not None:
                result = annotation.group("type")

        return result if result in _VALUE_RETURN_TYPES else None
