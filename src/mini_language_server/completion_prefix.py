"""Exact-snapshot identifier-prefix filtering for Nova completions."""

from __future__ import annotations

from typing import Any

from .completion_classification import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticError
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with exact-snapshot completion prefix filtering."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        semantics = None if parsed is None else parsed[0]
        offset = None if parsed is None else parsed[1]
        snapshots = () if semantics is None else self.workspace_symbols.snapshots()

        response = super()._handle_workspace_completion(request_id, params)
        if (
            response is None
            or not isinstance(response.get("result"), list)
            or semantics is None
            or offset is None
        ):
            return response

        def filter_items() -> dict[str, Any]:
            text = semantics.symbols.syntax.document.text
            prefix = self._completion_identifier_prefix(text, offset)
            if not prefix:
                return response
            response["result"] = [
                item
                for item in response["result"]
                if isinstance(item, dict)
                and isinstance(item.get("label"), str)
                and item["label"].startswith(prefix)
            ]
            return response

        try:
            return self.semantics.commit_if_current(
                semantics,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, filter_items
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    @staticmethod
    def _completion_identifier_prefix(text: str, offset: int) -> str:
        start = offset
        while start > 0:
            char = text[start - 1]
            if not (char == "_" or (char.isascii() and char.isalnum())):
                break
            start -= 1
        return text[start:offset]
