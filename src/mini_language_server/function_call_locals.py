"""Exact-snapshot Nova local types from same-file direct function calls."""

from __future__ import annotations

import re
from typing import Any

from .function_call_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_LOCAL_CALL_PREFIX = re.compile(rf"\s*=\s*(?P<name>{_IDENTIFIER})\s*\(")
_LOCAL_INITIALIZER_TAIL = re.compile(
    rf"\s*(?=\}}|let\b|{_IDENTIFIER}(?:\s*\(|\b)|$)"
)
_TYPED_FUNCTION = re.compile(
    rf"\bfn\s+(?P<name>{_IDENTIFIER})\s*\([^)]*\)\s*->\s*(?P<type>{_IDENTIFIER}|!)\s*\{{"
)
_VALUE_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded same-file call initializer inference."""

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer inherited local types, then one exact same-file direct call."""
        inherited = super()._local_type(snapshot, target, seen)
        if inherited is not None:
            return inherited

        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        suffix = code[target.span.end :]
        call = _LOCAL_CALL_PREFIX.match(suffix)
        if call is None:
            return None

        expression_start = target.span.end + call.start("name")
        expression = text[expression_start:]
        expression_code = code[expression_start:]
        name_end = call.end("name") - call.start("name")
        parsed = self._call_argument_bounds(expression, name_end)
        if parsed is None:
            return None
        closing = parsed[1]
        if _LOCAL_INITIALIZER_TAIL.match(expression_code[closing + 1 :]) is None:
            return None

        name = call.group("name")
        declarations = tuple(
            symbol
            for symbol in snapshot.symbols.symbols
            if symbol.kind == "function" and symbol.name == name
        )
        if len(declarations) != 1:
            return None

        declaration = declarations[0]
        for function in _TYPED_FUNCTION.finditer(code):
            if (
                function.group("name") == name
                and function.start("name") == declaration.span.start
                and function.end("name") == declaration.span.end
            ):
                result_type = function.group("type")
                return result_type if result_type in _VALUE_TYPES else None
        return None
