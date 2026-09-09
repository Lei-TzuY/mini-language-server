"""Exact-workspace Nova signature help with conservative inferred returns."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .inferred_return_completion import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with inferred return types in signature help."""

    def _handle_signature_help(self, request_id: Any, params: Any) -> dict[str, Any]:
        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")
        semantics, offset, _ = parsed
        if semantics is None:
            return self._result(request_id, None)
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return self._result(request_id, None)

        document = semantics.symbols.syntax.document
        call = self._containing_call(document.text, tree, offset)
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result: Any = None
            if call is not None:
                name, opening, active_parameter = call
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) == 1:
                    declaration = declarations[0]
                    label = self._function_signature(declaration)
                    if "->" not in self.nova_adapter.code_view(label):
                        inferred = self._bounded_function_return_type(declaration)
                        if inferred is not None:
                            label = f"{label} -> {inferred}"
                    parameters = self._signature_parameters(label)
                    signature: dict[str, Any] = {"label": label}
                    if parameters:
                        signature["parameters"] = [
                            {"label": parameter} for parameter in parameters
                        ]
                    result = {
                        "signatures": [signature],
                        "activeSignature": 0,
                    }
                    if parameters:
                        result["activeParameter"] = min(
                            active_parameter, len(parameters) - 1
                        )
                    elif offset > opening:
                        result["activeParameter"] = 0

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
