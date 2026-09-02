"""Small language-independent JSON-RPC/LSP lifecycle core."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from .protocol import JsonRpcError


class ServerState(Enum):
    PRE_INITIALIZE = auto()
    RUNNING = auto()
    SHUTDOWN = auto()
    EXITED = auto()


class LanguageServer:
    """Handle the protocol lifecycle without binding to any language frontend."""

    def __init__(self) -> None:
        self.state = ServerState.PRE_INITIALIZE
        self.exit_code: int | None = None

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        is_request = "id" in message

        if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
            return self._error(request_id, -32600, "Invalid Request") if is_request else None

        method = message["method"]

        if method == "exit":
            self.exit_code = 0 if self.state is ServerState.SHUTDOWN else 1
            self.state = ServerState.EXITED
            return None

        if self.state is ServerState.EXITED:
            return None

        if self.state is ServerState.PRE_INITIALIZE:
            if method != "initialize":
                if is_request:
                    return self._error(request_id, -32002, "Server not initialized")
                return None
            if not is_request:
                return None
            self.state = ServerState.RUNNING
            return self._result(
                request_id,
                {
                    "capabilities": {},
                    "serverInfo": {"name": "mini-language-server", "version": "0.1.0"},
                },
            )

        if method == "initialize":
            if is_request:
                return self._error(request_id, -32600, "Initialize request already received")
            return None

        if method == "shutdown":
            if not is_request:
                return None
            if self.state is ServerState.SHUTDOWN:
                return self._error(request_id, -32600, "Shutdown already requested")
            self.state = ServerState.SHUTDOWN
            return self._result(request_id, None)

        if self.state is ServerState.SHUTDOWN:
            return self._error(request_id, -32600, "Server has shut down") if is_request else None

        if method == "initialized":
            return None

        if is_request:
            return self._error(request_id, -32601, "Method not found")
        return None

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        error = JsonRpcError(code, message)
        return {"jsonrpc": "2.0", "id": request_id, "error": error.as_object()}
