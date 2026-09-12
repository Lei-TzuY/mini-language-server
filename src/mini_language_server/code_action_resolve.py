"""Exact-snapshot code-action resolve for the final Nova product."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .completion_resolve import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import DiagnosticError, DiagnosticSnapshot
from .semantic import SemanticSnapshot
from .server import ServerState
from .workspace import WorkspaceIndexError


@dataclass(frozen=True, slots=True)
class _CodeActionResolveRecord:
    diagnostic: DiagnosticSnapshot
    workspace: tuple[SemanticSnapshot, ...]
    action: dict[str, Any]


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated, exact-snapshot code-action resolve."""

    def __init__(self) -> None:
        super().__init__()
        self._code_action_resolve_edit = False
        self._code_action_resolve_next = 1
        self._code_action_resolve_records: dict[int, _CodeActionResolveRecord] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._code_action_resolve_edit = self._client_supports_code_action_resolve_edit(
                message.get("params")
            )
            result = super().handle(message)
            if (
                self._code_action_resolve_edit
                and result is not None
                and isinstance(result.get("result"), dict)
            ):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict) and capabilities.get(
                    "codeActionProvider"
                ) is not None:
                    capabilities["codeActionProvider"] = {"resolveProvider": True}
            return result

        if (
            method == "codeAction/resolve"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._code_action_resolve_edit
        ):
            return self._handle_code_action_resolve(
                message.get("id"), message.get("params")
            )

        if (
            method == "textDocument/codeAction"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._code_action_resolve_edit
        ):
            return self._handle_lazy_code_action(message)

        return super().handle(message)

    def _handle_lazy_code_action(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        params = message.get("params")
        uri = self._document_uri(params)
        snapshot = self.diagnostics.get(uri) if uri is not None else None
        snapshots = self.workspace_symbols.snapshots()

        response = super().handle(message)
        if (
            snapshot is None
            or response is None
            or not isinstance(response.get("result"), list)
        ):
            return response

        def enrich() -> dict[str, Any]:
            for action in response["result"]:
                if not isinstance(action, dict) or not isinstance(action.get("edit"), dict):
                    continue
                token = self._code_action_resolve_next
                self._code_action_resolve_next += 1
                stored = dict(action)
                data = stored.get("data")
                data = dict(data) if isinstance(data, dict) else {}
                data["novaCodeActionResolve"] = token
                stored["data"] = data
                action["data"] = dict(data)
                action.pop("edit", None)
                self._code_action_resolve_records[token] = _CodeActionResolveRecord(
                    snapshot,
                    snapshots,
                    stored,
                )
            while len(self._code_action_resolve_records) > 256:
                oldest = min(self._code_action_resolve_records)
                del self._code_action_resolve_records[oldest]
            return response

        try:
            return self.diagnostics.commit_if_current(
                snapshot,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, enrich
                ),
            )
        except (DiagnosticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    def _handle_code_action_resolve(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        data = params.get("data")
        if not isinstance(data, dict):
            return self._error(request_id, -32602, "Invalid params")
        token = data.get("novaCodeActionResolve")
        if not isinstance(token, int) or isinstance(token, bool):
            return self._error(request_id, -32602, "Invalid params")
        record = self._code_action_resolve_records.get(token)
        if record is None:
            return self._error(request_id, -32602, "Invalid params")

        uri = record.diagnostic.semantic.uri
        if self.diagnostics.get(uri) is not record.diagnostic:
            return self._error(request_id, -32801, "Content modified")

        try:
            context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32801, "Content modified")

        try:
            self.requests.checkpoint(context)
            resolved = dict(params)
            edit = record.action.get("edit")
            if isinstance(edit, dict):
                resolved["edit"] = edit

            def commit() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, resolved)

            return self.diagnostics.commit_if_current(
                record.diagnostic,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    record.workspace, commit
                ),
            )
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except (StaleRequest, DiagnosticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _client_supports_code_action_resolve_edit(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        code_action = text_document.get("codeAction")
        if not isinstance(code_action, dict):
            return False
        resolve_support = code_action.get("resolveSupport")
        if not isinstance(resolve_support, dict):
            return False
        properties = resolve_support.get("properties")
        if not isinstance(properties, list):
            return False
        return any(value == "edit" for value in properties if isinstance(value, str))
