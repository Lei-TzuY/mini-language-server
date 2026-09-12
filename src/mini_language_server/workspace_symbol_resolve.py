"""Exact-workspace lazy resolve for Nova workspace symbols."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .inlay_hint_resolve import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .workspace import WorkspaceIndexError


@dataclass(frozen=True, slots=True)
class _WorkspaceSymbolResolveRecord:
    workspace: tuple[Any, ...]
    symbol: dict[str, Any]


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated exact-workspace symbol resolve."""

    def __init__(self) -> None:
        super().__init__()
        self._workspace_symbol_resolve_properties: frozenset[str] = frozenset()
        self._workspace_symbol_resolve_next = 1
        self._workspace_symbol_resolve_records: dict[
            int, _WorkspaceSymbolResolveRecord
        ] = {}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._workspace_symbol_resolve_properties = (
                self._client_workspace_symbol_resolve_properties(message.get("params"))
            )
            result = super().handle(message)
            if (
                self._workspace_symbol_resolve_properties
                and result is not None
                and isinstance(result.get("result"), dict)
            ):
                capabilities = result["result"].get("capabilities")
                if (
                    isinstance(capabilities, dict)
                    and capabilities.get("workspaceSymbolProvider") is not None
                ):
                    capabilities["workspaceSymbolProvider"] = {"resolveProvider": True}
            return result

        if (
            method == "workspace/symbol"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._workspace_symbol_resolve_properties
        ):
            return self._handle_lazy_workspace_symbol(message)

        if (
            method == "workspaceSymbol/resolve"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._workspace_symbol_resolve_properties
        ):
            return self._handle_workspace_symbol_resolve(
                message.get("id"), message.get("params")
            )

        return super().handle(message)

    def _handle_lazy_workspace_symbol(
        self, message: dict[str, Any]
    ) -> dict[str, Any] | None:
        request_id = message.get("id")
        workspace = self.workspace_symbols.snapshots()
        response = super().handle(message)
        if response is None or not isinstance(response.get("result"), list):
            return response

        def publish() -> dict[str, Any]:
            result = response["result"]
            for symbol in result:
                if not isinstance(symbol, dict):
                    continue
                location = symbol.get("location")
                if not isinstance(location, dict):
                    continue
                uri = location.get("uri")
                range_value = location.get("range")
                if not isinstance(uri, str) or not isinstance(range_value, dict):
                    continue
                token = self._workspace_symbol_resolve_next
                self._workspace_symbol_resolve_next += 1
                stored = dict(symbol)
                stored["location"] = dict(location)
                data = stored.get("data")
                data = dict(data) if isinstance(data, dict) else {}
                data["novaWorkspaceSymbolResolve"] = token
                stored["data"] = data
                symbol["data"] = dict(data)
                if "location.range" in self._workspace_symbol_resolve_properties:
                    symbol["location"] = {"uri": uri}
                self._workspace_symbol_resolve_records[token] = (
                    _WorkspaceSymbolResolveRecord(workspace, stored)
                )
            while len(self._workspace_symbol_resolve_records) > 256:
                oldest = min(self._workspace_symbol_resolve_records)
                del self._workspace_symbol_resolve_records[oldest]
            return response

        try:
            return self.workspace_symbols.commit_snapshots_if_current(workspace, publish)
        except WorkspaceIndexError:
            return self._error(request_id, -32801, "Content modified")

    def _handle_workspace_symbol_resolve(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        data = params.get("data")
        if not isinstance(data, dict):
            return self._error(request_id, -32602, "Invalid params")
        token = data.get("novaWorkspaceSymbolResolve")
        if not isinstance(token, int) or isinstance(token, bool):
            return self._error(request_id, -32602, "Invalid params")
        record = self._workspace_symbol_resolve_records.get(token)
        if record is None:
            return self._error(request_id, -32602, "Invalid params")

        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            resolved = dict(params)
            if "location.range" in self._workspace_symbol_resolve_properties:
                resolved["location"] = dict(record.symbol["location"])

            def publish() -> dict[str, Any]:
                self.requests.checkpoint(context)
                return self._result(request_id, resolved)

            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    record.workspace, publish
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _client_workspace_symbol_resolve_properties(params: Any) -> frozenset[str]:
        if not isinstance(params, dict):
            return frozenset()
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return frozenset()
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return frozenset()
        symbol = workspace.get("symbol")
        if not isinstance(symbol, dict):
            return frozenset()
        resolve_support = symbol.get("resolveSupport")
        if not isinstance(resolve_support, dict):
            return frozenset()
        properties = resolve_support.get("properties")
        if not isinstance(properties, list):
            return frozenset()
        supported = {"location.range"}
        return frozenset(
            value for value in properties if isinstance(value, str) and value in supported
        )
