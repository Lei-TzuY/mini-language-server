"""Workspace-aware Nova LSP composition over exact semantic snapshots."""

from __future__ import annotations

from contextlib import suppress
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .diagnostics import Diagnostic
from .nova import NovaFunctionSyntax, NovaLanguageServer
from .server import ServerState
from .source import SourceText, Span
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
            if method == "textDocument/prepareCallHierarchy":
                return self._handle_prepare_call_hierarchy(
                    message.get("id"), message.get("params")
                )
            if method in {"callHierarchy/incomingCalls", "callHierarchy/outgoingCalls"}:
                return self._handle_call_hierarchy_calls(
                    method, message.get("id"), message.get("params")
                )
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
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                if self._client_supports_workspace_symbol(params):
                    capabilities["workspaceSymbolProvider"] = True
                if self._client_supports_call_hierarchy(params):
                    capabilities["callHierarchyProvider"] = True
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
            self._publish_workspace_diagnostics()
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
        self._publish_workspace_diagnostics()

    def _publish_workspace_diagnostics(self) -> None:
        """Reconcile Nova call diagnostics against one exact workspace snapshot set."""
        snapshots = self.workspace_symbols.snapshots()
        planned: list[tuple[Any, tuple[Diagnostic, ...]]] = []
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            current = self.diagnostics.get(snapshot.uri)
            if current is None or current.semantic is not snapshot:
                continue
            diagnostics = [
                diagnostic
                for diagnostic in current.diagnostics
                if diagnostic.code
                not in {"nova.unresolved-function", "nova.ambiguous-function"}
            ]
            for name, span in tree.calls:
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) == 0:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"unresolved function '{name}'",
                            code="nova.unresolved-function",
                            source="nova",
                        )
                    )
                elif len(declarations) > 1:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"ambiguous function call '{name}'",
                            code="nova.ambiguous-function",
                            source="nova",
                        )
                    )
            planned.append((snapshot, tuple(diagnostics)))

        def publish() -> None:
            for snapshot, diagnostics in planned:
                self.publish_diagnostics(snapshot, diagnostics)

        try:
            self.workspace_symbols.commit_snapshots_if_current(snapshots, publish)
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

    @staticmethod
    def _function_signature(declaration: Any) -> str:
        """Return the exact bounded Nova function header owning a declaration."""
        text = declaration.snapshot.symbols.syntax.document.text
        span = declaration.symbol.span
        start = text.rfind("fn", 0, span.start)
        if start < 0 or text[start + 2 : span.start].strip():
            return f"function {declaration.symbol.name}"
        opening = text.find("{", span.end)
        if opening < 0:
            return f"function {declaration.symbol.name}"
        signature = text[start:opening].strip()
        return signature or f"function {declaration.symbol.name}"

    def _function_extent(self, declaration: Any) -> Span:
        text = declaration.snapshot.symbols.syntax.document.text
        selection = declaration.symbol.span
        start = text.rfind("fn", 0, selection.start)
        opening = text.find("{", selection.end)
        if start < 0 or opening < 0:
            return selection
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return selection
        return Span(start, closing + 1)

    def _call_hierarchy_item(self, declaration: Any) -> dict[str, Any]:
        source = SourceText(declaration.snapshot.symbols.syntax.document.text)
        return {
            "name": declaration.symbol.name,
            "kind": 12,
            "detail": self._function_signature(declaration),
            "uri": declaration.uri,
            "range": self._range(source, self._function_extent(declaration)),
            "selectionRange": self._range(source, declaration.symbol.span),
            "data": {"name": declaration.symbol.name, "uri": declaration.uri},
        }

    def _call_hierarchy_declaration(self, params: Any):
        if not isinstance(params, dict):
            return None
        item = params.get("item")
        if not isinstance(item, dict):
            return None
        data = item.get("data")
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        uri = data.get("uri")
        if not isinstance(name, str) or not isinstance(uri, str):
            return None
        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function" and declaration.uri == uri
        )
        return declarations[0] if len(declarations) == 1 else None

    def _owning_function_declaration(self, snapshot: Any, offset: int):
        candidates = []
        for symbol in snapshot.symbols.symbols:
            if symbol.kind != "function":
                continue
            declaration = next(
                (
                    candidate
                    for candidate in self.workspace_symbols.declarations(symbol.name)
                    if candidate.snapshot is snapshot and candidate.symbol is symbol
                ),
                None,
            )
            if declaration is None:
                continue
            extent = self._function_extent(declaration)
            if extent.start <= offset < extent.end:
                candidates.append(declaration)
        if not candidates:
            return None
        return min(candidates, key=lambda declaration: self._function_extent(declaration).end)

    def _handle_prepare_call_hierarchy(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        query = self._workspace_function_query(params)
        if query is None:
            return self._result(request_id, [])
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
            result = [self._call_hierarchy_item(declarations[0])] if len(declarations) == 1 else []
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

    def _handle_call_hierarchy_calls(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any]:
        declaration = self._call_hierarchy_declaration(params)
        if declaration is None:
            return self._result(request_id, [])
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=declaration.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")
        try:
            self.requests.checkpoint(context)
            if method == "callHierarchy/incomingCalls":
                result = self._incoming_calls(declaration, snapshots)
            else:
                result = self._outgoing_calls(declaration)
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

    def _incoming_calls(self, declaration: Any, snapshots: tuple[Any, ...]) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, int], tuple[Any, list[Span]]] = {}
        target_name = declaration.symbol.name
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            for call_name, span in tree.calls:
                if call_name != target_name:
                    continue
                caller = self._owning_function_declaration(snapshot, span.start)
                if caller is None:
                    continue
                key = (caller.uri, caller.symbol.span.start)
                grouped.setdefault(key, (caller, []))[1].append(span)
        result = []
        for key in sorted(grouped):
            caller, spans = grouped[key]
            source = SourceText(caller.snapshot.symbols.syntax.document.text)
            result.append(
                {
                    "from": self._call_hierarchy_item(caller),
                    "fromRanges": [
                        self._range(source, span)
                        for span in sorted(spans, key=lambda item: item.start)
                    ],
                }
            )
        return result

    def _outgoing_calls(self, declaration: Any) -> list[dict[str, Any]]:
        snapshot = declaration.snapshot
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return []
        extent = self._function_extent(declaration)
        source = SourceText(snapshot.symbols.syntax.document.text)
        grouped: dict[tuple[str, int], tuple[Any, list[Span]]] = {}
        for call_name, span in tree.calls:
            if not (extent.start <= span.start < extent.end):
                continue
            targets = tuple(
                target
                for target in self.workspace_symbols.declarations(call_name)
                if target.symbol.kind == "function"
            )
            if len(targets) != 1:
                continue
            target = targets[0]
            key = (target.uri, target.symbol.span.start)
            grouped.setdefault(key, (target, []))[1].append(span)
        result = []
        for key in sorted(grouped):
            target, spans = grouped[key]
            result.append(
                {
                    "to": self._call_hierarchy_item(target),
                    "fromRanges": [
                        self._range(source, span)
                        for span in sorted(spans, key=lambda item: item.start)
                    ],
                }
            )
        return result

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
                result = {
                    "contents": {
                        "kind": "plaintext",
                        "value": self._function_signature(declarations[0]),
                    },
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

    @staticmethod
    def _client_supports_call_hierarchy(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("callHierarchy"), dict)
