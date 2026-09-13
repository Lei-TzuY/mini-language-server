"""Semantic classification and deterministic ranking for Nova completions."""

from __future__ import annotations

from typing import Any

from .completion_insert_replace import NovaProductLanguageServer as _NovaProductLanguageServer

_COMPLETION_KIND_FUNCTION = 3
_COMPLETION_KIND_VARIABLE = 6
_COMPLETION_KIND_CONSTANT = 21


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with role-aware completion kinds and ranking."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

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
