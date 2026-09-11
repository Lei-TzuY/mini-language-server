"""Bounded Nova Unit inference for unannotated functions with bare returns."""

from __future__ import annotations

from typing import Any

from .inferred_function_returns import _RETURN
from .unit_type_actions import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative bare-return Unit inference."""

    def _bounded_function_return_type(
        self,
        declaration: Any,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> str | None:
        identity = self._inference_identity(declaration)
        if identity in resolving:
            return None

        text = declaration.snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        opening = code.find("{", declaration.symbol.span.end)
        if opening < 0:
            return None
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return None

        saw_bare_return = False
        saw_value_return = False
        body_code = code[opening + 1 : closing]
        for statement in _RETURN.finditer(body_code):
            keyword_end = opening + 1 + statement.end()
            boundary = closing
            for delimiter in (";", "\n", "\r"):
                found = code.find(delimiter, keyword_end, closing)
                if found >= 0:
                    boundary = min(boundary, found)
            if text[keyword_end:boundary].strip():
                saw_value_return = True
            else:
                saw_bare_return = True

        inferred = super()._bounded_function_return_type(declaration, resolving)
        if not saw_bare_return:
            return inferred
        if not saw_value_return:
            return "Unit"
        return "Unit" if inferred == "Unit" else None
