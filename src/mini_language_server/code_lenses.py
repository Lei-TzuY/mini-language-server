"""Exact-workspace Nova reference-count CodeLens support."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nova import NovaFunctionSyntax
from .pull_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText
from .workspace import WorkspaceIndexError

_SHOW_REFERENCES_COMMAND = "mini-language-server.showReferences"


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact-workspace reference CodeLens."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/codeLens"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_code_lens(message.get("id"), message.get("params"))
        if (
            method == "workspace/executeCommand"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            params = message.get("params")
            if isinstance(params, dict) and params.get("command") == _SHOW_REFERENCES_COMMAND:
                return self._handle_show_references(message.get("id"), params)

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_code_lens(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["codeLensProvider"] = {"resolveProvider": False}
                capabilities["executeCommandProvider"] = {
                    "commands": [_SHOW_REFERENCES_COMMAND]
                }
        return result

    @staticmethod
    def _client_supports_code_lens(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("codeLens"), dict)

    def _handle_code_lens(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        uri = self._document_uri(params)
        if uri is None:
            return self._error(request_id, -32602, "Invalid params")
        semantic = self.semantics.get(uri)
        if semantic is None:
            return self._error(request_id, -32602, "Invalid params")
        if not isinstance(semantic.symbols.syntax.tree, NovaFunctionSyntax):
            return self._result(request_id, [])
        if self.workspace_symbols.get(uri) is not semantic:
            return self._error(request_id, -32801, "Content modified")

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            source = SourceText(semantic.symbols.syntax.document.text)
            result: list[dict[str, Any]] = []
            for symbol in semantic.symbols.symbols:
                if symbol.kind != "function":
                    continue
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(symbol.name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) != 1 or declarations[0].snapshot is not semantic:
                    continue
                references = self._workspace_call_locations(symbol.name, snapshots)
                count = len(references)
                suffix = "reference" if count == 1 else "references"
                result.append(
                    {
                        "range": self._range(source, symbol.span),
                        "command": {
                            "title": f"{count} {suffix}",
                            "command": _SHOW_REFERENCES_COMMAND,
                            "arguments": [{"uri": uri, "name": symbol.name}],
                        },
                        "data": {"uri": uri, "name": symbol.name},
                    }
                )

            result.sort(
                key=lambda lens: (
                    lens["range"]["start"]["line"],
                    lens["range"]["start"]["character"],
                )
            )
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

    def _handle_show_references(self, request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
        arguments = params.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 1:
            return self._error(request_id, -32602, "Invalid params")
        target = arguments[0]
        if not isinstance(target, dict):
            return self._error(request_id, -32602, "Invalid params")
        uri = target.get("uri")
        name = target.get("name")
        if not isinstance(uri, str) or not uri or not isinstance(name, str) or not name:
            return self._error(request_id, -32602, "Invalid params")

        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1 or declarations[0].uri != uri:
            return self._result(request_id, [])
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = self._workspace_call_locations(name, snapshots)
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

    def _workspace_call_locations(
        self, name: str, snapshots: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        locations: list[tuple[str, int, dict[str, Any]]] = []
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            source = SourceText(snapshot.symbols.syntax.document.text)
            for call_name, span in tree.calls:
                if call_name != name:
                    continue
                locations.append(
                    (
                        snapshot.uri,
                        span.start,
                        {"uri": snapshot.uri, "range": self._range(source, span)},
                    )
                )
        locations.sort(key=lambda item: (item[0], item[1]))
        return [location for _, _, location in locations]
