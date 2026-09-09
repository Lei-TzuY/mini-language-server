"""Exact-workspace Nova completion details for conservative inferred returns."""

from __future__ import annotations

from typing import Any

from .inferred_return_hover import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with inferred return types in completion details."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, _, _ = parsed
        if semantics is None or not isinstance(
            semantics.symbols.syntax.tree, NovaFunctionSyntax
        ):
            return super()._handle_workspace_completion(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
        response = super()._handle_workspace_completion(request_id, params)
        if response is None or "error" in response:
            return response
        result = response.get("result")
        if not isinstance(result, list):
            return response

        decorated: list[Any] = []
        for item in result:
            if not isinstance(item, dict):
                decorated.append(item)
                continue
            detail = item.get("detail")
            name = item.get("label")
            if (
                not isinstance(name, str)
                or not isinstance(detail, str)
                or not detail.startswith("fn ")
                or "->" in self.nova_adapter.code_view(detail)
            ):
                decorated.append(item)
                continue
            declarations = tuple(
                declaration
                for declaration in self.workspace_symbols.declarations(name)
                if declaration.symbol.kind == "function"
            )
            inferred = None
            if len(declarations) == 1:
                inferred = self._bounded_function_return_type(declarations[0])
            if inferred is None:
                decorated.append(item)
                continue
            enriched = dict(item)
            enriched["detail"] = f"{detail} -> {inferred}"
            decorated.append(enriched)

        try:
            return self.workspace_symbols.commit_snapshots_if_current(
                snapshots, lambda: self._result(request_id, decorated)
            )
        except WorkspaceIndexError:
            return self._error(request_id, -32801, "Content modified")
