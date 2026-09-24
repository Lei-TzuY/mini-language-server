"""Exact-workspace Nova rename safety for the final product server."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .code_lenses import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError
from .workspace_folders import WorkspaceFolderSet


def _is_nova_identifier(value: str) -> bool:
    """Match the adapter's bounded ASCII identifier grammar."""
    if not value:
        return False
    first = value[0]
    if not (first == "_" or "A" <= first <= "Z" or "a" <= first <= "z"):
        return False
    return all(
        character == "_"
        or "A" <= character <= "Z"
        or "a" <= character <= "z"
        or "0" <= character <= "9"
        for character in value[1:]
    )


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with collision-safe workspace function rename."""

    @staticmethod
    def _same_workspace_declaration(left: Any, right: Any) -> bool:
        return left.snapshot is right.snapshot and left.symbol is right.symbol

    def _outward_alias_rename_plan(
        self,
        semantics: Any,
        snapshots: tuple[Any, ...],
        alias_target: tuple[Any, Any, Any],
        new_name: str,
    ) -> tuple[dict[str, list[tuple[int, dict[str, Any]]]], str | None] | None:
        """Plan one bounded alias API rename across explicit and implicit exports."""
        selected, declaration, _ = alias_target
        old_name = selected.binding_name
        tree = semantics.symbols.syntax.tree
        if (
            not isinstance(tree, NovaFunctionSyntax)
            or not tree.has_export_list
            or selected.alias_span is None
        ):
            return None

        origin_exports = [
            item for item in tree.exports if item.name == old_name
        ]
        if len(origin_exports) != 1:
            return None

        edits_by_uri: dict[str, list[tuple[int, dict[str, Any]]]] = {}
        seen_edits: set[tuple[str, int, int]] = set()

        def add_edit(snapshot: Any, span: Any) -> None:
            if new_name == old_name:
                return
            key = (snapshot.uri, span.start, span.end)
            if key in seen_edits:
                return
            seen_edits.add(key)
            source = self._source_text(snapshot.symbols.syntax.document.text)
            edits_by_uri.setdefault(snapshot.uri, []).append(
                (
                    span.start,
                    {
                        "range": self._range(source, span),
                        "newText": new_name,
                    },
                )
            )

        def exact_binding(snapshot: Any, name: str) -> bool:
            visible = self._nova_visible_function_map(
                snapshot,
                snapshots,
                legacy_global=False,
            )
            candidates = visible.get(name, ())
            return (
                len(candidates) == 1
                and self._same_workspace_declaration(candidates[0], declaration)
            )

        def outward_binding(snapshot: Any) -> bool:
            visible = self._nova_visible_function_map(
                snapshot,
                snapshots,
                legacy_global=False,
                respect_root_exports=True,
            )
            candidates = visible.get(old_name, ())
            return (
                len(candidates) == 1
                and self._same_workspace_declaration(candidates[0], declaration)
            )

        def binding_conflict(snapshot: Any, ignored: frozenset[int]) -> bool:
            if new_name == old_name:
                return False
            snapshot_tree = snapshot.symbols.syntax.tree
            if not isinstance(snapshot_tree, NovaFunctionSyntax):
                return True
            if any(name == new_name for name, _ in snapshot_tree.declarations):
                return True
            for imported in snapshot_tree.imports:
                if not imported.has_name_list:
                    continue
                for candidate in imported.names:
                    if id(candidate) in ignored:
                        continue
                    if candidate.binding_name == new_name:
                        return True
            visible = self._nova_visible_function_map(snapshot, snapshots)
            return new_name in visible

        if binding_conflict(semantics, frozenset({id(selected)})):
            return {}, new_name
        if new_name != old_name and any(
            item.name == new_name for item in tree.exports
        ):
            return {}, new_name

        add_edit(semantics, selected.alias_span)
        add_edit(semantics, origin_exports[0].span)
        for call_name, span in tree.calls:
            if call_name == old_name:
                add_edit(semantics, span)

        queue = [semantics]
        seen_modules: set[Any] = set()
        unaliased_owner: dict[Any, Any] = {}

        while queue:
            current = queue.pop(0)
            current_identity = WorkspaceFolderSet.uri_identity(current.uri)
            if current_identity in seen_modules:
                continue
            seen_modules.add(current_identity)
            if not outward_binding(current):
                return None

            for importer in snapshots:
                importer_identity = WorkspaceFolderSet.uri_identity(importer.uri)
                importer_tree = importer.symbols.syntax.tree
                if not isinstance(importer_tree, NovaFunctionSyntax):
                    continue

                matching_unaliased: list[Any] = []
                matching_selected: list[Any] = []
                saw_bare_edge = False
                for imported in importer_tree.imports:
                    target_uri = self._nova_import_target_uri(
                        importer.uri,
                        imported.path,
                    )
                    if target_uri is None:
                        continue
                    if (
                        WorkspaceFolderSet.uri_identity(target_uri)
                        != current_identity
                    ):
                        continue
                    if not imported.has_name_list:
                        saw_bare_edge = True
                        continue
                    for candidate in imported.names:
                        if candidate.name != old_name:
                            continue
                        matching_selected.append(candidate)
                        if candidate.alias is None:
                            matching_unaliased.append(candidate)

                if not matching_selected and not saw_bare_edge:
                    continue

                for candidate in matching_selected:
                    add_edit(importer, candidate.span)

                propagates_unaliased = saw_bare_edge or bool(matching_unaliased)
                if not propagates_unaliased:
                    continue
                if len(matching_unaliased) > 1:
                    return None
                previous_owner = unaliased_owner.get(importer_identity)
                if previous_owner is not None and previous_owner != current_identity:
                    return None
                unaliased_owner[importer_identity] = current_identity

                if not exact_binding(importer, old_name):
                    return None
                ignored = frozenset(id(item) for item in matching_unaliased)
                if binding_conflict(importer, ignored):
                    return {}, new_name

                for call_name, span in importer_tree.calls:
                    if call_name == old_name:
                        add_edit(importer, span)

                if importer_tree.has_export_list:
                    matching_exports = [
                        item
                        for item in importer_tree.exports
                        if item.name == old_name
                    ]
                    if not matching_exports:
                        continue
                    if len(matching_exports) != 1:
                        return None
                    if new_name != old_name and any(
                        item.name == new_name
                        for item in importer_tree.exports
                        if item is not matching_exports[0]
                    ):
                        return {}, new_name
                    add_edit(importer, matching_exports[0].span)
                queue.append(importer)

        return edits_by_uri, None

    def _handle_outward_import_alias_rename(
        self,
        request_id: Any,
        semantics: Any,
        snapshots: tuple[Any, ...],
        alias_target: tuple[Any, Any, Any],
        new_name: str,
    ) -> dict[str, Any]:
        """Rename one outward alias across a bounded explicit/implicit graph."""
        selected, _, _ = alias_target
        old_name = selected.binding_name

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if not _is_nova_identifier(new_name):
                return self._error(request_id, -32602, "Invalid params")

            planned = self._outward_alias_rename_plan(
                semantics,
                snapshots,
                alias_target,
                new_name,
            )
            if planned is None:
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._result(request_id, None),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            edits_by_uri, conflict = planned
            if conflict is not None:
                try:
                    return self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        lambda: self._error(
                            request_id,
                            -32803,
                            f"Rename would conflict with existing binding '{conflict}'",
                        ),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            changes: dict[str, list[dict[str, Any]]] = {}
            for uri in sorted(edits_by_uri):
                ordered = sorted(edits_by_uri[uri], key=lambda item: item[0])
                changes[uri] = [edit for _, edit in ordered]
            workspace_edit = self._workspace_edit(
                changes,
                versions=self._workspace_edit_versions(snapshots),
                annotation_label=f"Rename exported alias '{old_name}' to '{new_name}'",
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

    def _handle_workspace_prepare_rename(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_prepare_rename(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_prepare_rename(request_id, params)
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return super()._handle_workspace_prepare_rename(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
        alias_target = self._nova_import_alias_target(
            semantics,
            snapshots,
            offset,
        )
        if alias_target is None:
            return super()._handle_workspace_prepare_rename(request_id, params)

        selected, _, target_span = alias_target
        old_name = selected.binding_name
        if not self._nova_import_alias_is_outward(tree, old_name):
            return super()._handle_workspace_prepare_rename(request_id, params)
        if (
            self._outward_alias_rename_plan(
                semantics,
                snapshots,
                alias_target,
                old_name,
            )
            is None
        ):
            return super()._handle_workspace_prepare_rename(request_id, params)

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")
        try:
            self.requests.checkpoint(context)
            result = {
                "range": self._range(source, target_span),
                "placeholder": old_name,
            }
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._result(request_id, result),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

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

            if not _is_nova_identifier(new_name):
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
                        if self._nova_import_alias_is_outward(
                            tree,
                            alias_target[0].binding_name,
                        ):
                            return self._handle_outward_import_alias_rename(
                                request_id,
                                semantics,
                                snapshots,
                                alias_target,
                                new_name,
                            )
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
        semantics, query_name = query
        declarations = self._nova_visible_function_declarations(
            semantics,
            snapshots,
            query_name,
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
            name = declaration.symbol.name
            if "::" in query_name:
                if query_name.rsplit("::", 1)[1] != name:
                    try:
                        return self.workspace_symbols.commit_snapshots_if_current(
                            snapshots, lambda: self._result(request_id, None)
                        )
                    except WorkspaceIndexError:
                        return self._error(request_id, -32801, "Content modified")
            elif name != query_name:
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

                for call_name, span in snapshot_tree.calls:
                    if "::" not in call_name:
                        continue
                    if call_name.rsplit("::", 1)[1] != name:
                        continue
                    qualified = self._nova_visible_function_declarations(
                        snapshot,
                        snapshots,
                        call_name,
                    )
                    if (
                        len(qualified) != 1
                        or qualified[0].snapshot is not declaration.snapshot
                        or qualified[0].symbol is not declaration.symbol
                    ):
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
