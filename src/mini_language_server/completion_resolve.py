"""Exact-snapshot completion-item resolve for the final Nova product."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .semantic import SemanticError, SemanticSnapshot
from .server import ServerState
from .uint_conversion_operand_completion import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .workspace import WorkspaceIndexError


@dataclass(frozen=True, slots=True)
class _CompletionResolveRecord:
    semantic: SemanticSnapshot
    workspace: tuple[SemanticSnapshot, ...]
    item: dict[str, Any]


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated, exact-snapshot completion resolve."""

    def __init__(self) -> None:
        super().__init__()
        self._completion_resolve_enabled = False
        self._completion_resolve_next = 1
        self._completion_resolve_records: dict[int, _CompletionResolveRecord] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._completion_resolve_enabled = self._client_supports_completion_resolve(
                message.get("params")
            )
            result = super().handle(message)
            if (
                self._completion_resolve_enabled
                and result is not None
                and isinstance(result.get("result"), dict)
            ):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    provider = capabilities.get("completionProvider")
                    if isinstance(provider, dict):
                        provider["resolveProvider"] = True
            return result

        if (
            method == "completionItem/resolve"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._completion_resolve_enabled
        ):
            return self._handle_completion_resolve(
                message.get("id"), message.get("params")
            )
        return super().handle(message)

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        if not self._completion_resolve_enabled:
            return super()._handle_workspace_completion(request_id, params)

        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, _, _ = parsed
        if semantics is None:
            return super()._handle_workspace_completion(request_id, params)
        snapshots = self.workspace_symbols.snapshots()

        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

        def enrich() -> dict[str, Any]:
            result = response["result"]
            for item in result:
                if not isinstance(item, dict) or not isinstance(item.get("label"), str):
                    continue
                token = self._completion_resolve_next
                self._completion_resolve_next += 1
                stored = dict(item)
                data = stored.get("data")
                data = dict(data) if isinstance(data, dict) else {}
                data["novaCompletionResolve"] = token
                stored["data"] = data
                item["data"] = dict(data)
                self._completion_resolve_records[token] = _CompletionResolveRecord(
                    semantics,
                    snapshots,
                    stored,
                )
            while len(self._completion_resolve_records) > 256:
                oldest = min(self._completion_resolve_records)
                del self._completion_resolve_records[oldest]
            return response

        try:
            return self.semantics.commit_if_current(
                semantics,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, enrich
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    def _handle_completion_resolve(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        data = params.get("data")
        if not isinstance(data, dict):
            return self._error(request_id, -32602, "Invalid params")
        token = data.get("novaCompletionResolve")
        if not isinstance(token, int) or isinstance(token, bool):
            return self._error(request_id, -32602, "Invalid params")
        record = self._completion_resolve_records.get(token)
        if record is None:
            return self._error(request_id, -32602, "Invalid params")

        if self.semantics.get(record.semantic.uri) is not record.semantic:
            return self._error(request_id, -32801, "Content modified")

        try:
            context = self.requests.start(request_id, uri=record.semantic.uri)
        except RequestError:
            return self._error(request_id, -32801, "Content modified")

        try:
            self.requests.checkpoint(context)
            resolved = dict(record.item)
            resolved["documentation"] = self._completion_documentation(resolved)

            def commit() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, resolved)

            return self.semantics.commit_if_current(
                record.semantic,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    record.workspace, commit
                ),
            )
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except (StaleRequest, SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _completion_documentation(item: dict[str, Any]) -> dict[str, str]:
        label = item.get("label")
        detail = item.get("detail")
        if not isinstance(label, str):
            label = "completion"
        if isinstance(detail, str) and detail.startswith("fn "):
            value = f"```nova\n{detail}\n```"
        elif isinstance(detail, str) and ": " in detail:
            kind, type_name = detail.split(": ", 1)
            value = f"Nova {kind} `{label}` with bounded type `{type_name}`."
        elif isinstance(detail, str):
            value = f"Nova {detail} `{label}`."
        else:
            value = f"Nova completion `{label}`."
        return {"kind": "markdown", "value": value}

    @staticmethod
    def _client_supports_completion_resolve(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        completion = text_document.get("completion")
        if not isinstance(completion, dict):
            return False
        completion_item = completion.get("completionItem")
        if not isinstance(completion_item, dict):
            return False
        resolve_support = completion_item.get("resolveSupport")
        if not isinstance(resolve_support, dict):
            return False
        properties = resolve_support.get("properties")
        return isinstance(properties, list) and "documentation" in properties
