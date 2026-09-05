"""Workspace-aware Nova LSP composition over exact semantic snapshots."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError
from .nova import NovaLanguageServer
from .source import SourceText
from .workspace import WorkspaceIndexError, WorkspaceSymbolIndex

_SYMBOL_KINDS = {
    "class": 5,
    "function": 12,
    "variable": 13,
    "parameter": 13,
}


class WorkspaceNovaLanguageServer(NovaLanguageServer):
    """Nova server with deterministic, version-safe ``workspace/symbol`` support."""

    def __init__(self) -> None:
        super().__init__()
        self.workspace_symbols = WorkspaceSymbolIndex()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "workspace/symbol" and "id" in message:
            return self._handle_workspace_symbol(message.get("id"), message.get("params"))

        result = super().handle(message)
        if method == "initialize" and result is not None and "result" in result:
            params = message.get("params")
            if self._client_supports_workspace_symbol(params):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["workspaceSymbolProvider"] = True
        return result

    def _handle_document_notification(self, method: str, params: Any) -> None:
        uri = self._document_uri(params)
        previous = self.workspace_symbols.get(uri) if uri is not None else None
        super()._handle_document_notification(method, params)
        if uri is None:
            return
        if method == "textDocument/didClose":
            if previous is not None:
                try:
                    self.workspace_symbols.remove(uri, expected=previous)
                except WorkspaceIndexError:
                    pass
            return
        if method not in {"textDocument/didOpen", "textDocument/didChange"}:
            return
        current = self.semantics.get(uri)
        if current is None:
            return
        try:
            self.workspace_symbols.replace(current, expected=previous)
        except WorkspaceIndexError:
            return

    def _handle_workspace_symbol(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict) or not isinstance(params.get("query"), str):
            return self._error(request_id, -32602, "Invalid params")
        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            declarations = self.workspace_symbols.search(params["query"])
            self.requests.checkpoint(context)
            result = []
            for declaration in declarations:
                source = SourceText(declaration.snapshot.symbols.syntax.document.text)
                result.append(
                    {
                        "name": declaration.symbol.name,
                        "kind": _SYMBOL_KINDS.get(declaration.symbol.kind, 13),
                        "location": {
                            "uri": declaration.uri,
                            "range": self._range(source, declaration.symbol.span),
                        },
                    }
                )
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_if_current(
                    declarations, lambda: self._result(request_id, result)
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _client_supports_workspace_symbol(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        return isinstance(workspace.get("symbol"), dict)
