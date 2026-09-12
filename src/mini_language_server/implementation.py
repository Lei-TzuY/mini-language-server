"""Exact-snapshot implementation navigation for the final Nova product."""

from __future__ import annotations

from typing import Any

from .declaration import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with implementation navigation over exact bindings."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")

        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            result = super().handle(message)
            if result is not None and isinstance(result.get("result"), dict):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["implementationProvider"] = True
            return result

        if (
            method == "textDocument/implementation"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            definition_request = dict(message)
            definition_request["method"] = "textDocument/definition"
            return super().handle(definition_request)

        return super().handle(message)
