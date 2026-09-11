"""Bounded Nova function-result inference from final tail expressions."""

from __future__ import annotations

from typing import Any

from .inferred_function_returns import _RETURN
from .tail_expression_returns import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative tail-expression result inference."""

    def _bounded_function_return_type(
        self,
        declaration: Any,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> str | None:
        inherited = super()._bounded_function_return_type(declaration, resolving)
        if inherited is not None:
            return inherited

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

        body_code = code[opening + 1 : closing]
        if _RETURN.search(body_code) is not None:
            return None

        tail = self._bounded_tail_expression(text, code, opening + 1, closing)
        if tail is None:
            return None
        expression, expression_span = tail
        return self._inference_expression_type(
            declaration.snapshot,
            expression,
            expression_span,
            resolving | {identity},
        )
