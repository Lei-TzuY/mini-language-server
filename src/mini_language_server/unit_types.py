"""Bounded exact-snapshot Unit literal and result typing for Nova."""

from __future__ import annotations

import re

from .constant_branch_actions import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative Unit literal/result typing."""

    def _literal_type(self, expression: str) -> str | None:
        if expression.strip() == "()":
            return "Unit"
        return super()._literal_type(expression)

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
