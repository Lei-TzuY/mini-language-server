"""Exact-workspace Nova function-call result types in typed arguments."""

from __future__ import annotations

from typing import Any

from .duplicate_declaration_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded function-call argument type propagation."""

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Infer literals/references first, then one exact direct function call."""
        inherited = super()._argument_type(snapshot, argument)
        if inherited is not None:
            return inherited
        text = snapshot.symbols.syntax.document.text
        expression = text[argument.start : argument.end].strip()
        return self._function_call_return_type(expression)
