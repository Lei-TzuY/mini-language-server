"""Exact-workspace Nova argument type propagation for typed parameters."""

from __future__ import annotations

import re
from typing import Any

from .argument_type_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_PARAMETER_TYPE_SUFFIX = re.compile(r"\s*:\s*([A-Za-z_][A-Za-z0-9_]*|!)")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact typed-parameter argument propagation."""

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Return a bounded actual type from a literal or exact parameter reference."""
        literal_type = super()._argument_type(snapshot, argument)
        if literal_type is not None:
            return literal_type

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
        if target.kind != "parameter":
            return None

        match = _PARAMETER_TYPE_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        return match.group(1)
