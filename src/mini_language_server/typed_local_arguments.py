"""Exact-snapshot Nova argument type propagation for literal-initialized locals."""

from __future__ import annotations

import re
from typing import Any

from .source import Span
from .typed_parameter_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCAL_INITIALIZER_SUFFIX = re.compile(
    r'\s*=\s*(?P<value>\d+|true\b|false\b|"(?:\\.|[^"\\])*")'
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with bounded local-initializer type propagation."""

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Return a bounded actual type from literals, parameters, or typed locals."""
        inherited_type = super()._argument_type(snapshot, argument)
        if inherited_type is not None:
            return inherited_type

        text = snapshot.symbols.syntax.document.text
        token = text[argument.start : argument.end].strip()
        if _IDENTIFIER.fullmatch(token) is None:
            return None
        references = tuple(
            reference for reference in snapshot.references if reference.span == argument
        )
        if len(references) != 1:
            return None
        target = references[0].target
        if target.kind != "variable":
            return None

        match = _LOCAL_INITIALIZER_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        return self._literal_type(match.group("value"))
