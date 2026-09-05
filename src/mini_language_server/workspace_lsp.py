"""Workspace-aware Nova LSP composition over exact semantic snapshots."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nova import NovaFunctionSyntax, NovaLanguageServer
from .server import ServerState
from .source import SourceText
from .workspace import WorkspaceIndexError, WorkspaceSymbolIndex

_SYMBOL_KINDS = {
    "class": 5,
    "function": 12,
    "variable": 13,
    "parameter": 13,
}


class WorkspaceNovaLanguageServer(NovaLanguageServer):
    """Nova server with deterministic, version-safe workspace tooling."""

    def __init__(self) -> None:
        super().__init__()
        self.workspace_symbols = WorkspaceSymbolIndex()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if "id" in message and self.state is ServerState.RUNNING:
            if method == "workspace/symbol":
                return self._handle_workspace_symbol(
                    message.get("id"), message.get("params")
                )
            if method == "textDocument/completion":
                workspace_result = self._handle_workspace_completion(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/hover":
                workspace_result = self._handle_workspace_hover(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method in {"textDocument/definition", "textDocument/references"}:
                workspace_result = self._handle_workspace_navigation(
                    method, message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/prepareRename":
                workspace_result = self._handle_workspace_prepare_rename(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result
            if method == "textDocument/rename":
                workspace_result = self._handle_workspace_rename(
                    message.get("id"), message.get("params")
                )
                if workspace_result is not None:
                    return workspace_result

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
                with suppress(WorkspaceIndexError):
                    self.workspace_symbols.remove(uri, expected=previous)
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

    def _workspace_function_query(self, params: Any):
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, offset, _ = parsed
        if semantics is None:
            return None
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        target = semantics.definition_at(offset)
        if target is not None:
            if target.kind != "function":
                return None
            return semantics, target.name
        for call_name, span in tree.calls:
            if span.start <= offset < span.end:
                return semantics, call_name
        return None

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return None
        semantics, _, _ = parsed
        if semantics is None or not isinstance(
            semantics.symbols.syntax.tree, NovaFunctionSyntax
        ):
            return None

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            items: set[tuple[str, str]] = {
                (symbol.name, symbol.kind) for symbol in semantics.symbols.symbols
            }
            for snapshot in snapshots:
                if not isinstance(snapshot.symbols.syntax.tree, NovaFunctionSyntax):
                    continue
                for symbol in snapshot.symbols.symbols:
                    if symbol.kind == "function":
                        items.add((symbol.name, symbol.kind))

            result = [
                {"label": name, "detail": kind}
                for name, kind in sorted(items, key=lambda item: (item[0], item[1]))
            ]
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

        # Preserve the generic single-document hover for locally resolved symbols.
        if semantics.definition_at(offset) is not None:
            return None

        call = next(
            (
                (call_name, span)
                for call_name, span in tree.calls
                if span.start <= offset < span.end
            ),
            None,
        )
        if call is None:
            return None
        name, call_span = call
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
                result = {
                    "contents": {
                        "kind": "plaintext",
                        "value": f"function {name}",
                    },
                    "range": self._range(source, call_span),
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

    def _handle_workspace_navigation(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Resolve Nova functions across exact current workspace snapshots.

        Returning ``None`` delegates non-Nova/local-only targets to the generic server.
        A Nova function name becomes workspace-addressable only when exactly one indexed
        function declaration owns that name, so ambiguous workspaces never guess.
        """
        query = self._workspace_function_query(params)
        if query is None:
            return None
        semantics, name = query

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
            if len(declarations) != 1:
                result: Any = [] if method == "textDocument/references" else None
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, lambda: self._result(request_id, result)
                )

            declaration = declarations[0]
            if method == "textDocument/definition":
                source = SourceText(declaration.snapshot.symbols.syntax.document.text)
                result = self._location(
                    declaration.uri, source, declaration.symbol.span
                )
            else:
                include_declaration = self._include_declaration(params)
                if include_declaration is None:
                    return self._error(request_id, -32602, "Invalid params")
                locations: list[tuple[str, int, dict[str, Any]]] = []
                if include_declaration:
                    source = SourceText(declaration.snapshot.symbols.syntax.document.text)
                    locations.append(
                        (
                            declaration.uri,
                            declaration.symbol.span.start,
                            self._location(
                                declaration.uri, source, declaration.symbol.span
                            ),
                        )
                    )
                for snapshot in snapshots:
                    snapshot_tree = snapshot.symbols.syntax.tree
                    if not isinstance(snapshot_tree, NovaFunctionSyntax):
                        continue
                    source = SourceText(snapshot.symbols.syntax.document.text)
                    for call_name, span in snapshot_tree.calls:
                        if call_name == name:
                            locations.append(
                                (
                                    snapshot.uri,
                                    span.start,
                                    self._location(snapshot.uri, source, span),
                                )
                            )
                locations.sort(key=lambda item: (item[0], item[1]))
                result = [location for _, _, location in locations]

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

    def _handle_workspace_prepare_rename(
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

        # Preserve generic prepareRename for symbols already resolved in this document.
        if semantics.definition_at(offset) is not None:
            return None

        call = next(
            (
                (call_name, span)
                for call_name, span in tree.calls
                if span.start <= offset < span.end
            ),
            None,
        )
        if call is None:
            return None
        name, call_span = call
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
                result = {
                    "range": self._range(source, call_span),
                    "placeholder": name,
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

    def _handle_workspace_rename(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        new_name = params.get("newName")
        if not isinstance(new_name, str) or not new_name:
            return self._error(request_id, -32602, "Invalid params")

        query = self._workspace_function_query(params)
        if query is None:
            return None
        semantics, name = query
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
            if len(declarations) != 1:
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots, lambda: self._result(request_id, None)
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            declaration = declarations[0]
            edits_by_uri: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            declaration_source = SourceText(
                declaration.snapshot.symbols.syntax.document.text
            )
            edits_by_uri.setdefault(declaration.uri, []).append(
                (
                    declaration.symbol.span.start,
                    {
                        "range": self._range(
                            declaration_source, declaration.symbol.span
                        ),
                        "newText": new_name,
                    },
                )
            )

            for snapshot in snapshots:
                snapshot_tree = snapshot.symbols.syntax.tree
                if not isinstance(snapshot_tree, NovaFunctionSyntax):
                    continue
                source = SourceText(snapshot.symbols.syntax.document.text)
                for call_name, span in snapshot_tree.calls:
                    if call_name != name:
                        continue
                    edits_by_uri.setdefault(snapshot.uri, []).append(
                        (
                            span.start,
                            {"range": self._range(source, span), "newText": new_name},
                        )
                    )

            changes: dict[str, list[dict[str, Any]]] = {}
            for uri in sorted(edits_by_uri):
                ordered = sorted(edits_by_uri[uri], key=lambda item: item[0])
                changes[uri] = [edit for _, edit in ordered]

            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._result(request_id, {"changes": changes}),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _include_declaration(params: Any) -> bool | None:
        if not isinstance(params, dict):
            return None
        context = params.get("context")
        if context is None:
            return False
        if not isinstance(context, dict):
            return None
        include = context.get("includeDeclaration")
        return include if isinstance(include, bool) else None

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
