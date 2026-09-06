"""Exact-snapshot Nova argument type propagation for bounded locals."""

from __future__ import annotations

import re
from typing import Any

from .source import Span
from .typed_parameter_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCAL_INITIALIZER_SUFFIX = re.compile(
    r'\s*=\s*(?P<value>\d+|true\b|false\b|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*)'
    r"\s*(?=\}|let\b|[A-Za-z_][A-Za-z0-9_]*\s*\(|$)"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with bounded local type propagation."""

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Return a bounded actual type from literals, parameters, or local aliases."""
        inherited_type = super()._argument_type(snapshot, argument)
        if inherited_type is not None:
            return inherited_type

        target = self._exact_reference_target(snapshot, argument)
        if target is None or target.kind != "variable":
            return None
        return self._local_type(snapshot, target, frozenset())

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer only literal or exact-reference local initializer types."""
        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None
        seen = seen | {identity}

        text = snapshot.symbols.syntax.document.text
        match = _LOCAL_INITIALIZER_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        value = match.group("value")
        literal_type = self._literal_type(value)
        if literal_type is not None:
            return literal_type
        if _IDENTIFIER.fullmatch(value) is None:
            return None

        value_span = Span(
            target.span.end + match.start("value"),
            target.span.end + match.end("value"),
        )
        inherited_type = super()._argument_type(snapshot, value_span)
        if inherited_type is not None:
            return inherited_type

        alias_target = self._exact_reference_target(snapshot, value_span)
        if alias_target is None or alias_target.kind != "variable":
            return None
        return self._local_type(snapshot, alias_target, seen)

    @staticmethod
    def _exact_reference_target(snapshot: Any, span: Span) -> Any | None:
        references = tuple(
            reference for reference in snapshot.references if reference.span == span
        )
        if len(references) != 1:
            return None
        return references[0].target
