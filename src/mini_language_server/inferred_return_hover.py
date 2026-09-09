"""Exact-workspace Nova hover surfacing conservative inferred return types."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nested_unreachable import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with inferred return types in exact-workspace hover."""

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, offset, source = parsed
        if semantics is None:
            return None
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        target = semantics.definition_at(offset)
        if target is not None and target.kind != "function":
            return None

        call = next(
            (
                (call_name, span)
                for call_name, span in tree.calls
                if span.start <= offset < span.end
            ),
            None,
        )
        if target is not None:
            name = target.name
            hover_span = call[1] if call is not None else target.span
        elif call is not None:
            name, hover_span = call
        else:
            return None

        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function"
        )
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result: Any = None
            if len(declarations) == 1:
                declaration = declarations[0]
                signature = self._function_signature(declaration)
                if "->" not in self.nova_adapter.code_view(signature):
                    inferred = self._bounded_function_return_type(declaration)
                    if inferred is not None:
                        signature = f"{signature} -> {inferred}"
                result = {
                    "contents": {"kind": "plaintext", "value": signature},
                    "range": self._range(source, hover_span),
                }
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, lambda: self._result(request_id, result)
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
