"""Exact-workspace Nova rename safety for the final product server."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .code_lenses import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError
from .workspace_folders import WorkspaceFolderSet


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
        snapshots = self.workspace_symbols.snapshots()
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

            declaration = declarations[0]
            edits_by_uri: dict[str, list[tuple[int, dict[str, Any]]]] = {}
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

            indexed = {
                WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
                for snapshot in snapshots
            }

            def targets_declaration(
                candidates: tuple[Any, ...],
            ) -> bool:
                return (
                    len(candidates) == 1
                    and candidates[0].snapshot is declaration.snapshot
                    and candidates[0].symbol is declaration.symbol
                )

            for snapshot in snapshots:
                snapshot_tree = snapshot.symbols.syntax.tree
                if not isinstance(snapshot_tree, NovaFunctionSyntax):
                    continue
                source = self._source_text(snapshot.symbols.syntax.document.text)

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
                    target_candidates = self._nova_outward_function_map(
                        target,
                        snapshots,
                    ).get(name, ())
                    if not targets_declaration(target_candidates):
                        continue
                    for selected in imported.names:
                        if selected.name != name:
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

                export_candidates = self._nova_visible_function_map(
                    snapshot,
                    snapshots,
                    legacy_global=False,
                ).get(name, ())
                if targets_declaration(export_candidates):
                    candidate = export_candidates[0]
                    private_spans = frozenset(snapshot_tree.private_declarations)
                    valid_export = not (
                        candidate.snapshot is snapshot
                        and candidate.symbol.span in private_spans
                    )
                    if valid_export:
                        for exported in snapshot_tree.exports:
                            if exported.name != name:
                                continue
                            edits_by_uri.setdefault(snapshot.uri, []).append(
                                (
                                    exported.span.start,
                                    {
                                        "range": self._range(
                                            source,
                                            exported.span,
                                        ),
                                        "newText": new_name,
                                    },
                                )
                            )

                resolved = self._nova_visible_function_declarations(
                    snapshot,
                    snapshots,
                    name,
                )
                if (
                    len(resolved) != 1
                    or resolved[0].snapshot is not declaration.snapshot
                    or resolved[0].symbol is not declaration.symbol
                ):
                    continue
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
