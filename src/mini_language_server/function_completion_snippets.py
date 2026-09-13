"""Exact-snapshot call snippets for Nova function completion items."""

from __future__ import annotations

from typing import Any

from .unary_plus_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated snippets for exact function completions."""

    def __init__(self) -> None:
        super().__init__()
        self._function_completion_snippets = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if isinstance(message, dict) and message.get("method") == "initialize":
            self._function_completion_snippets = self._client_supports_completion_snippets(
                message.get("params")
            )
        return super().handle(message)

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        if not self._function_completion_snippets:
            return super()._handle_workspace_completion(request_id, params)

        parsed = self._semantic_query(params)
        if parsed is None or parsed[0] is None:
            return super()._handle_workspace_completion(request_id, params)
        snapshots = self.workspace_symbols.snapshots()
        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

        def enrich() -> dict[str, Any]:
            for item in response["result"]:
                if not isinstance(item, dict):
                    continue
                label = item.get("label")
                if not isinstance(label, str):
                    continue
                detail = self._function_completion_detail(item)
                if detail is None:
                    continue
                snippet = self._function_call_snippet(label, detail)
                if snippet is None:
                    continue
                item["insertText"] = snippet
                item["insertTextFormat"] = 2
            return response

        try:
            return self.workspace_symbols.commit_snapshots_if_current(snapshots, enrich)
        except WorkspaceIndexError:
            return self._error(request_id, -32801, "Content modified")

    def _function_completion_detail(self, item: dict[str, Any]) -> str | None:
        detail = item.get("detail")
        if isinstance(detail, str) and detail.startswith("fn "):
            return detail

        data = item.get("data")
        if not isinstance(data, dict):
            return None
        token = data.get("novaCompletionResolve")
        records = getattr(self, "_completion_resolve_records", None)
        if not isinstance(token, int) or not isinstance(records, dict):
            return None
        record = records.get(token)
        stored = getattr(record, "item", None)
        if not isinstance(stored, dict):
            return None
        detail = stored.get("detail")
        return detail if isinstance(detail, str) and detail.startswith("fn ") else None

    @staticmethod
    def _function_call_snippet(label: str, detail: str) -> str | None:
        opening = detail.find("(")
        closing = detail.rfind(")")
        if opening < 0 or closing < opening:
            return None
        parameters = detail[opening + 1 : closing].strip()
        if not parameters:
            return f"{label}()$0"

        names: list[str] = []
        for parameter in parameters.split(","):
            name, separator, _ = parameter.strip().partition(":")
            if not separator or not name:
                return None
            names.append(name.strip())
        placeholders = ", ".join(
            f"${{{index}:{name}}}" for index, name in enumerate(names, start=1)
        )
        return f"{label}({placeholders})$0"
