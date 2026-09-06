"""Exact-snapshot Nova argument type propagation for bounded locals."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .source import Span
from .typed_parameter_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCAL_INITIALIZER_SUFFIX = re.compile(
    r'\s*=\s*(?P<value>\d+|true\b|false\b|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*)'
    r"\s*(?=\}|let\b|[A-Za-z_][A-Za-z0-9_]*(?:\s*\(|\b)|$)"
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

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Expose bounded Nova parameter/local types from the exact semantic snapshot."""
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_hover(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_hover(request_id, params)

        target = semantics.definition_at(offset)
        if target is None or target.kind not in {"parameter", "variable"}:
            return super()._handle_workspace_hover(request_id, params)

        target_type = self._symbol_type(semantics, target)
        if target_type is None:
            return super()._handle_workspace_hover(request_id, params)

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = {
                "contents": {
                    "kind": "plaintext",
                    "value": f"{target.kind} {target.name}: {target_type}",
                },
                "range": self._range(source, target.span),
            }
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, result)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _symbol_type(self, snapshot: Any, target: Any) -> str | None:
        """Return bounded type knowledge for one exact parameter or local symbol."""
        parameter_type = self._parameter_type(snapshot, target)
        if parameter_type is not None:
            return parameter_type
        if target.kind != "variable":
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

        value_span = Span(match.start("value"), match.end("value"))
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
