"""Exact-workspace Nova local types from direct function calls."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .function_call_arguments import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError

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
    """Final Nova product with bounded exact-workspace call initializer inference."""

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer inherited local types, then one exact-workspace direct call."""
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
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return None

        declaration = declarations[0]
        declaration_text = declaration.snapshot.symbols.syntax.document.text
        declaration_code = self.nova_adapter.code_view(declaration_text)
        for function in _TYPED_FUNCTION.finditer(declaration_code):
            if (
                function.group("name") == name
                and function.start("name") == declaration.symbol.span.start
                and function.end("name") == declaration.symbol.span.end
            ):
                result_type = function.group("type")
                return result_type if result_type in _VALUE_TYPES else None
        return None

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Publish local call-derived hover types against the exact workspace set."""
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_hover(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_hover(request_id, params)

        target = semantics.definition_at(offset)
        if target is None or target.kind not in {"parameter", "variable"}:
            return super()._handle_workspace_hover(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
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
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._current_semantic_result(
                        semantics, request_id, result
                    ),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
