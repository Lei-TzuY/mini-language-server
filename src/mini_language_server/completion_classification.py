"""Semantic classification and deterministic ranking for Nova completions."""

from __future__ import annotations

from typing import Any

from .completion_insert_replace import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticError
from .workspace import WorkspaceIndexError

_COMPLETION_KIND_FUNCTION = 3
_COMPLETION_KIND_VARIABLE = 6
_COMPLETION_KIND_CONSTANT = 21


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with role-aware completion kinds and ranking."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        semantics = None if parsed is None else parsed[0]
        snapshots = () if semantics is None else self.workspace_symbols.snapshots()

        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

        def enrich() -> dict[str, Any]:
            for item in response["result"]:
                if not isinstance(item, dict):
                    continue
                label = item.get("label")
                detail = item.get("detail")
                if not isinstance(label, str) or not isinstance(detail, str):
                    continue

                classification = self._completion_classification(detail)
                if classification is None:
                    continue
                kind, rank = classification
                item["kind"] = kind
                item["sortText"] = f"{rank}:{label}"
            return response

        if semantics is None:
            return enrich()
        try:
            return self.semantics.commit_if_current(
                semantics,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, enrich
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    @staticmethod
    def _completion_classification(detail: str) -> tuple[int, int] | None:
        if detail == "parameter" or detail.startswith("parameter:"):
            return _COMPLETION_KIND_VARIABLE, 0
        if detail == "variable" or detail.startswith("variable:"):
            return _COMPLETION_KIND_VARIABLE, 0
        if detail == "constant" or detail.startswith("constant:"):
            return _COMPLETION_KIND_CONSTANT, 1
        if detail == "function" or detail.startswith("fn "):
            return _COMPLETION_KIND_FUNCTION, 2
        return None
