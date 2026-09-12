"""Exact-snapshot declaration navigation for the final Nova product."""

from __future__ import annotations

from typing import Any

from .server import ServerState
from .workspace_symbol_resolve import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with declaration navigation over exact semantic bindings."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")

        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            result = super().handle(message)
            if result is not None and isinstance(result.get("result"), dict):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["declarationProvider"] = True
            return result

        if (
            method == "textDocument/declaration"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            definition_request = dict(message)
            definition_request["method"] = "textDocument/definition"
            return super().handle(definition_request)

        return super().handle(message)
