"""Small language-independent JSON-RPC/LSP lifecycle core."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from .documents import DocumentError, DocumentStore
from .protocol import JsonRpcError


class ServerState(Enum):
    PRE_INITIALIZE = auto()
    RUNNING = auto()
    SHUTDOWN = auto()
    EXITED = auto()


class LanguageServer:
    """Handle protocol lifecycle and versioned document notifications."""

    def __init__(self) -> None:
        self.state = ServerState.PRE_INITIALIZE
        self.exit_code: int | None = None
        self.documents = DocumentStore()

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
                    "capabilities": {"textDocumentSync": 2},
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

        if not is_request and method.startswith("textDocument/"):
            self._handle_document_notification(method, message.get("params"))
            return None

        if is_request:
            return self._error(request_id, -32601, "Method not found")
        return None

    def _handle_document_notification(self, method: str, params: Any) -> None:
        if not isinstance(params, dict):
            return
        try:
            if method == "textDocument/didOpen":
                text_document = params.get("textDocument")
                if not isinstance(text_document, dict):
                    return
                self.documents.open(
                    uri=text_document.get("uri"),
                    language_id=text_document.get("languageId"),
                    version=text_document.get("version"),
                    text=text_document.get("text"),
                )
            elif method == "textDocument/didChange":
                text_document = params.get("textDocument")
                changes = params.get("contentChanges")
                if not isinstance(text_document, dict) or not isinstance(changes, list):
                    return
                self.documents.apply_changes(
                    uri=text_document.get("uri"),
                    version=text_document.get("version"),
                    changes=changes,
                )
            elif method == "textDocument/didClose":
                text_document = params.get("textDocument")
                if not isinstance(text_document, dict):
                    return
                uri = text_document.get("uri")
                if isinstance(uri, str):
                    self.documents.close(uri)
        except DocumentError:
            return

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        error = JsonRpcError(code, message)
        return {"jsonrpc": "2.0", "id": request_id, "error": error.as_object()}
