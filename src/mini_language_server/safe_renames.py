"""Exact-workspace Nova rename safety for the final product server."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .code_lenses import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError
from .workspace_folders import WorkspaceFolderSet


_NOVA_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with collision-safe workspace function rename."""

    def _handle_private_import_alias_rename(
        self,
        request_id: Any,
        semantics: Any,
        snapshots: tuple[Any, ...],
        alias_target: tuple[Any, Any, Any],
        new_name: str,
    ) -> dict[str, Any]:
        """Rename one importer-private alias without touching its canonical symbol."""
        selected, _, _ = alias_target
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return self._result(request_id, None)
        old_name = selected.binding_name

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if self._nova_import_alias_is_outward(tree, old_name):
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._result(request_id, None),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            if _NOVA_IDENTIFIER.fullmatch(new_name) is None:
                return self._error(request_id, -32602, "Invalid params")

            if new_name == old_name:
                self.requests.checkpoint(context)
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._result(
                            request_id,
                            self._workspace_edit({}, versions={}),
                        ),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            visible = self._nova_visible_function_map(semantics, snapshots)
            syntax_conflict = any(
                candidate is not selected
                and candidate.binding_name == new_name
                for imported in tree.imports
                if imported.has_name_list
                for candidate in imported.names
            )
            if syntax_conflict or new_name in visible:
                self.requests.checkpoint(context)
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._error(
                            request_id,
                            -32803,
                            f"Rename would conflict with existing binding '{new_name}'",
                        ),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            alias_span = selected.alias_span
            if alias_span is None:
                return self._result(request_id, None)

            source = self._source_text(semantics.symbols.syntax.document.text)
            edits: list[tuple[int, dict[str, Any]]] = [
                (
                    alias_span.start,
                    {
                        "range": self._range(source, alias_span),
                        "newText": new_name,
                    },
                )
            ]
            for call_name, span in tree.calls:
                if call_name != old_name:
                    continue
                edits.append(
                    (
                        span.start,
                        {
                            "range": self._range(source, span),
                            "newText": new_name,
                        },
                    )
                )
            edits.sort(key=lambda item: item[0])
            workspace_edit = self._workspace_edit(
                {semantics.uri: [edit for _, edit in edits]},
                versions=self._workspace_edit_versions(snapshots),
                annotation_label=f"Rename alias '{old_name}' to '{new_name}'",
            )

            self.requests.checkpoint(context)
            if not self._closed_workspace_snapshots_current(snapshots):
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._result(request_id, workspace_edit),
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

        snapshots = self.workspace_symbols.snapshots()
        parsed = self._semantic_query(params)
        if parsed is not None:
            semantics, offset, _ = parsed
            if semantics is not None:
                tree = semantics.symbols.syntax.tree
                if isinstance(tree, NovaFunctionSyntax):
                    alias_target = self._nova_import_alias_target(
                        semantics,
                        snapshots,
                        offset,
                    )
                    if alias_target is not None:
                        return self._handle_private_import_alias_rename(
                            request_id,
                            semantics,
                            snapshots,
                            alias_target,
                            new_name,
                        )

        query = self._workspace_function_query(params)
        if query is None:
            return None
        semantics, name = query
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            name,
        )
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
            if declaration.symbol.name != name:
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots, lambda: self._result(request_id, None)
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            if new_name == name:
                self.requests.checkpoint(context)
                try:
                    empty_edit = self._workspace_edit({}, versions={})
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._result(request_id, empty_edit),
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

            edits_by_uri: dict[str, list[tuple[int, dict[str, Any]]]] = {}
            indexed = {
                WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
                for snapshot in snapshots
            }
            declaration_source = self._source_text(
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
                source = self._source_text(snapshot.symbols.syntax.document.text)

                resolved = self._nova_visible_function_declarations(
                    snapshot,
                    snapshots,
                    name,
                )
                if (
                    len(resolved) == 1
                    and resolved[0].snapshot is declaration.snapshot
                    and resolved[0].symbol is declaration.symbol
                ):
                    for call_name, span in snapshot_tree.calls:
                        if call_name != name:
                            continue
                        edits_by_uri.setdefault(snapshot.uri, []).append(
                            (
                                span.start,
                                {
                                    "range": self._range(source, span),
                                    "newText": new_name,
                                },
                            )
                        )

                for imported in snapshot_tree.imports:
                    if not imported.has_name_list:
                        continue
                    target_uri = self._nova_import_target_uri(
                        snapshot.uri,
                        imported.path,
                    )
                    if target_uri is None:
                        continue
                    target = indexed.get(
                        WorkspaceFolderSet.uri_identity(target_uri)
                    )
                    if target is None:
                        continue
                    target_visible = self._nova_visible_function_map(
                        target,
                        snapshots,
                        legacy_global=False,
                        respect_root_exports=True,
                    )
                    for selected in imported.names:
                        if selected.name != name:
                            continue
                        candidates = target_visible.get(selected.name, ())
                        if (
                            len(candidates) != 1
                            or candidates[0].snapshot is not declaration.snapshot
                            or candidates[0].symbol is not declaration.symbol
                        ):
                            continue
                        edits_by_uri.setdefault(snapshot.uri, []).append(
                            (
                                selected.span.start,
                                {
                                    "range": self._range(source, selected.span),
                                    "newText": new_name,
                                },
                            )
                        )

                visible_before_exports = self._nova_visible_function_map(
                    snapshot,
                    snapshots,
                    legacy_global=False,
                )
                for exported in snapshot_tree.exports:
                    if exported.name != name:
                        continue
                    candidates = visible_before_exports.get(exported.name, ())
                    if (
                        len(candidates) != 1
                        or candidates[0].snapshot is not declaration.snapshot
                        or candidates[0].symbol is not declaration.symbol
                    ):
                        continue
                    edits_by_uri.setdefault(snapshot.uri, []).append(
                        (
                            exported.span.start,
                            {
                                "range": self._range(source, exported.span),
                                "newText": new_name,
                            },
                        )
                    )

            changes: dict[str, list[dict[str, Any]]] = {}
            for uri in sorted(edits_by_uri):
                ordered = sorted(edits_by_uri[uri], key=lambda item: item[0])
                changes[uri] = [edit for _, edit in ordered]

            versions = self._workspace_edit_versions(snapshots)
            workspace_edit = self._workspace_edit(
                changes,
                versions=versions,
                annotation_label=f"Rename '{name}' to '{new_name}'",
            )
            self.requests.checkpoint(context)
            if not self._closed_workspace_snapshots_current(snapshots):
                self._refresh_closed_workspace_files()
                return self._error(request_id, -32801, "Content modified")
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._result(request_id, workspace_edit),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
