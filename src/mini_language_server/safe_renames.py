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

    def _namespace_binding_target(
        self,
        importer: Any,
        snapshots: tuple[Any, ...],
        binding: Any,
    ) -> Any | None:
        """Resolve one local namespace binding to its canonical namespace object."""
        indexed = {
            WorkspaceFolderSet.uri_identity(snapshot.uri): snapshot
            for snapshot in snapshots
        }
        target_uri = self._nova_import_target_uri(
            importer.uri,
            binding.imported.path,
        )
        if target_uri is None:
            return None
        target = indexed.get(WorkspaceFolderSet.uri_identity(target_uri))
        if target is None:
            return None
        if binding.selected is None:
            return target
        return self._nova_exported_namespace_targets(
            target,
            snapshots,
        ).get(binding.selected.name)

    def _outward_namespace_rename_plan(
        self,
        semantics: Any,
        snapshots: tuple[Any, ...],
        namespace_target: tuple[Any, Any],
        new_name: str,
    ) -> tuple[dict[str, list[tuple[int, dict[str, Any]]]], str | None] | None:
        """Plan one bounded namespace API rename across exact selective edges."""
        binding, _ = namespace_target
        old_name = binding.name
        tree = semantics.symbols.syntax.tree
        if (
            not isinstance(tree, NovaFunctionSyntax)
            or not self._nova_import_namespace_is_outward(tree, old_name)
            or (
                binding.selected is not None
                and binding.selected.alias is None
            )
        ):
            return None

        canonical_target = self._namespace_binding_target(
            semantics,
            snapshots,
            binding,
        )
        if canonical_target is None:
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

        def exact_outward_namespace(snapshot: Any) -> bool:
            exported = self._nova_exported_namespace_targets(
                snapshot,
                snapshots,
            )
            return exported.get(old_name) is canonical_target

        def binding_conflict(
            snapshot: Any,
            ignored_binding: Any | None,
            ignored_export: Any | None,
        ) -> bool:
            if new_name == old_name:
                return False
            snapshot_tree = snapshot.symbols.syntax.tree
            if not isinstance(snapshot_tree, NovaFunctionSyntax):
                return True
            if new_name in {"Int", "UInt"}:
                return True
            if any(name == new_name for name, _ in snapshot_tree.declarations):
                return True
            for other in self._nova_import_namespace_bindings(
                snapshot,
                snapshots,
            ):
                if ignored_binding is not None and other is ignored_binding:
                    continue
                if other.name == new_name:
                    return True
            for imported in snapshot_tree.imports:
                if not imported.has_name_list:
                    continue
                for selected in imported.names:
                    if (
                        ignored_binding is not None
                        and ignored_binding.selected is selected
                    ):
                        continue
                    if selected.binding_name == new_name:
                        return True
            return any(
                exported is not ignored_export
                and exported.name == new_name
                for exported in snapshot_tree.exports
            )

        if binding_conflict(
            semantics,
            binding,
            origin_exports[0],
        ):
            return {}, new_name

        for span in self._nova_import_namespace_spans(
            semantics,
            binding,
        ):
            add_edit(semantics, span)
        add_edit(semantics, origin_exports[0].span)

        queue = [semantics]
        seen_modules: set[Any] = set()

        while queue:
            current = queue.pop(0)
            current_identity = WorkspaceFolderSet.uri_identity(current.uri)
            if current_identity in seen_modules:
                continue
            seen_modules.add(current_identity)
            if not exact_outward_namespace(current):
                return None

            for importer in snapshots:
                importer_tree = importer.symbols.syntax.tree
                if not isinstance(importer_tree, NovaFunctionSyntax):
                    continue

                target_imports: list[Any] = []
                for imported in importer_tree.imports:
                    if not imported.has_name_list:
                        continue
                    target_uri = self._nova_import_target_uri(
                        importer.uri,
                        imported.path,
                    )
                    if (
                        target_uri is None
                        or WorkspaceFolderSet.uri_identity(target_uri)
                        != current_identity
                    ):
                        continue
                    target_imports.append(imported)

                if not target_imports:
                    continue

                unaliased: list[tuple[Any, Any]] = []
                for imported in target_imports:
                    target_namespaces = self._nova_exported_namespace_targets(
                        current,
                        snapshots,
                    )
                    if target_namespaces.get(old_name) is not canonical_target:
                        continue
                    for selected in imported.names:
                        if selected.name != old_name:
                            continue
                        add_edit(importer, selected.span)
                        if selected.alias is None:
                            unaliased.append((imported, selected))

                if not unaliased:
                    continue
                if len(unaliased) != 1:
                    return None

                imported_syntax, selected = unaliased[0]
                matching_bindings = [
                    candidate
                    for candidate in self._nova_import_namespace_bindings(
                        importer,
                        snapshots,
                    )
                    if candidate.imported is imported_syntax
                    and candidate.selected is selected
                    and candidate.name == old_name
                ]
                if len(matching_bindings) != 1:
                    return None
                local_binding = matching_bindings[0]
                if (
                    self._namespace_binding_target(
                        importer,
                        snapshots,
                        local_binding,
                    )
                    is not canonical_target
                ):
                    return None

                matching_exports = [
                    item
                    for item in importer_tree.exports
                    if item.name == old_name
                ]
                outward = self._nova_import_namespace_is_outward(
                    importer_tree,
                    old_name,
                )
                if outward and len(matching_exports) != 1:
                    return None
                ignored_export = matching_exports[0] if outward else None
                if binding_conflict(
                    importer,
                    local_binding,
                    ignored_export,
                ):
                    return {}, new_name

                for namespace, span in importer_tree.namespace_references:
                    if namespace == old_name:
                        add_edit(importer, span)

                if not outward:
                    continue
                add_edit(importer, matching_exports[0].span)
                queue.append(importer)

        return edits_by_uri, None

    def _handle_outward_import_namespace_rename(
        self,
        request_id: Any,
        semantics: Any,
        snapshots: tuple[Any, ...],
        namespace_target: tuple[Any, Any],
        new_name: str,
    ) -> dict[str, Any]:
        """Rename one exported namespace across its exact selective graph."""
        binding, _ = namespace_target
        old_name = binding.name
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if not _is_nova_identifier(new_name):
                return self._error(request_id, -32602, "Invalid params")
            if new_name in {"Int", "UInt"}:
                return self._error(
                    request_id,
                    -32803,
                    f"Rename would conflict with reserved namespace '{new_name}'",
                )

            planned = self._outward_namespace_rename_plan(
                semantics,
                snapshots,
                namespace_target,
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
                            (
                                "Rename would conflict with existing namespace "
                                f"binding '{conflict}'"
                            ),
                        ),
                    )
                except WorkspaceIndexError:
                    return self._error(request_id, -32801, "Content modified")

            changes: dict[str, list[dict[str, Any]]] = {}
            for uri in sorted(edits_by_uri):
                ordered = sorted(
                    edits_by_uri[uri],
                    key=lambda item: item[0],
                )
                changes[uri] = [edit for _, edit in ordered]
            workspace_edit = self._workspace_edit(
                changes,
                versions=self._workspace_edit_versions(snapshots),
                annotation_label=(
                    f"Rename exported namespace '{old_name}' to '{new_name}'"
                ),
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
        namespace_target = self._nova_import_namespace_target(
            semantics,
            snapshots,
            offset,
        )
        if namespace_target is not None:
            imported, target_span = namespace_target
            if imported.selected is not None and imported.selected.alias is None:
                return super()._handle_workspace_prepare_rename(request_id, params)
            old_name = imported.name
            if self._nova_import_namespace_is_outward(tree, old_name):
                if (
                    self._outward_namespace_rename_plan(
                        semantics,
                        snapshots,
                        namespace_target,
                        old_name,
                    )
                    is None
                ):
                    return super()._handle_workspace_prepare_rename(
                        request_id,
                        params,
                    )
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

        workspace_query = self._workspace_function_query(params)
        if workspace_query is not None:
            _, query_name = workspace_query
            if "::" in query_name:
                declarations = self._nova_visible_function_declarations(
                    semantics,
                    snapshots,
                    query_name,
                )
                target_span = next(
                    (
                        span
                        for call_name, span in tree.calls
                        if call_name == query_name
                        and span.start <= offset < span.end
                    ),
                    None,
                )
                if (
                    target_span is not None
                    and len(declarations) == 1
                    and query_name.rsplit("::", 1)[1]
                    == declarations[0].symbol.name
                ):
                    try:
                        context = self.requests.start(request_id, uri=semantics.uri)
                    except RequestError:
                        return self._error(request_id, -32602, "Invalid params")
                    try:
                        self.requests.checkpoint(context)
                        result = {
                            "range": self._range(source, target_span),
                            "placeholder": declarations[0].symbol.name,
                        }
                        self.requests.checkpoint(context)
                        try:
                            return self.workspace_symbols.commit_snapshots_if_current(
                                snapshots,
                                lambda: self._result(request_id, result),
                            )
                        except WorkspaceIndexError:
                            return self._error(
                                request_id,
                                -32801,
                                "Content modified",
                            )
                    except RequestCancelled:
                        return self._error(
                            request_id,
                            -32800,
                            "Request cancelled",
                        )
                    except StaleRequest:
                        return self._error(
                            request_id,
                            -32801,
                            "Content modified",
                        )
                    finally:
                        self.requests.finish(context)

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

    def _handle_import_namespace_rename(
        self,
        request_id: Any,
        semantics: Any,
        snapshots: tuple[Any, ...],
        namespace_target: tuple[Any, Any],
        new_name: str,
    ) -> dict[str, Any]:
        """Rename one importer-local namespace binding and its exact qualifiers."""
        imported, _ = namespace_target
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return self._result(request_id, None)
        if imported.selected is not None and imported.selected.alias is None:
            return self._result(request_id, None)
        if self._nova_import_namespace_is_outward(tree, imported.name):
            return self._handle_outward_import_namespace_rename(
                request_id,
                semantics,
                snapshots,
                namespace_target,
                new_name,
            )
        old_name = imported.name

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            if not _is_nova_identifier(new_name):
                return self._error(request_id, -32602, "Invalid params")
            if new_name in {"Int", "UInt"}:
                return self._error(
                    request_id,
                    -32803,
                    f"Rename would conflict with reserved namespace '{new_name}'",
                )

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

            namespace_bindings = self._nova_import_namespace_bindings(
                semantics,
                snapshots,
            )
            namespace_conflict = any(
                other.span != imported.span and other.name == new_name
                for other in namespace_bindings
            )
            selective_conflict = any(
                selected.binding_span != imported.span
                and selected.binding_name == new_name
                for imported_syntax in tree.imports
                if imported_syntax.has_name_list
                for selected in imported_syntax.names
            )
            if namespace_conflict or selective_conflict:
                return self._error(
                    request_id,
                    -32803,
                    f"Rename would conflict with import namespace '{new_name}'",
                )

            source = self._source_text(semantics.symbols.syntax.document.text)
            edits: list[tuple[int, dict[str, Any]]] = [
                (
                    imported.span.start,
                    {
                        "range": self._range(source, imported.span),
                        "newText": new_name,
                    },
                )
            ]
            edits.extend(
                (
                    span.start,
                    {
                        "range": self._range(source, span),
                        "newText": new_name,
                    },
                )
                for namespace, span in tree.namespace_references
                if namespace == old_name
            )
            edits.sort(key=lambda item: item[0])
            workspace_edit = self._workspace_edit(
                {semantics.uri: [edit for _, edit in edits]},
                versions=self._workspace_edit_versions(snapshots),
                annotation_label=f"Rename namespace '{old_name}' to '{new_name}'",
            )

            self.requests.checkpoint(context)
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
                    namespace_target = self._nova_import_namespace_target(
                        semantics,
                        snapshots,
                        offset,
                    )
                    if namespace_target is not None:
                        return self._handle_import_namespace_rename(
                            request_id,
                            semantics,
                            snapshots,
                            namespace_target,
                            new_name,
                        )
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
