"""Exact-workspace Nova rename safety for the final product server."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .code_lenses import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .source import SourceText
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with collision-safe workspace function rename."""

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

            if new_name == name:
                self.requests.checkpoint(context)
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._result(request_id, {"changes": {}}),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            conflicts = tuple(
                declaration
                for declaration in self.workspace_symbols.declarations(new_name)
                if declaration.symbol.kind == "function"
            )
            if conflicts:
                self.requests.checkpoint(context)
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._error(
                            request_id,
                            -32803,
                            f"Rename would conflict with existing function '{new_name}'",
                        ),
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
