"""Small language-independent JSON-RPC/LSP lifecycle core."""

from __future__ import annotations

from enum import Enum, auto
from typing import Any

from .documents import DocumentError, DocumentStore
from .protocol import JsonRpcError
from .semantic import SemanticDatabase, SemanticSnapshot
from .source import Position, SourceError, SourceText, Span
from .symbols import SymbolIndex
from .syntax import SyntaxStore


class ServerState(Enum):
    PRE_INITIALIZE = auto()
    RUNNING = auto()
    SHUTDOWN = auto()
    EXITED = auto()


class LanguageServer:
    """Handle protocol lifecycle, documents, and version-bound semantic queries."""

    def __init__(self) -> None:
        self.state = ServerState.PRE_INITIALIZE
        self.exit_code: int | None = None
        self.documents = DocumentStore()
        self.syntax = SyntaxStore(self.documents)
        self.symbols = SymbolIndex(self.syntax)
        self.semantics = SemanticDatabase(self.symbols)

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
                    "capabilities": {
                        "textDocumentSync": 2,
                        "definitionProvider": True,
                        "referencesProvider": True,
                        "renameProvider": True,
                    },
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

        if is_request and method in {"textDocument/definition", "textDocument/references"}:
            return self._handle_semantic_request(method, request_id, message.get("params"))

        if is_request and method == "textDocument/rename":
            return self._handle_rename_request(request_id, message.get("params"))

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

    def _handle_semantic_request(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any]:
        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")

        semantics, offset, source = parsed
        if semantics is None:
            empty_result = None if method == "textDocument/definition" else []
            return self._result(request_id, empty_result)

        target = semantics.definition_at(offset)
        if target is None:
            empty_result = None if method == "textDocument/definition" else []
            return self._result(request_id, empty_result)

        if method == "textDocument/definition":
            return self._result(
                request_id,
                self._location(semantics.uri, source, target.span),
            )

        assert isinstance(params, dict)
        context = params.get("context")
        include_declaration = False
        if context is not None:
            if not isinstance(context, dict) or not isinstance(
                context.get("includeDeclaration"), bool
            ):
                return self._error(request_id, -32602, "Invalid params")
            include_declaration = context["includeDeclaration"]

        locations = [
            self._location(semantics.uri, source, span)
            for span in semantics.references_to(
                target, include_declaration=include_declaration
            )
        ]
        return self._result(request_id, locations)

    def _handle_rename_request(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        new_name = params.get("newName")
        if not isinstance(new_name, str) or not new_name:
            return self._error(request_id, -32602, "Invalid params")

        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")

        semantics, offset, source = parsed
        if semantics is None:
            return self._result(request_id, None)

        target = semantics.definition_at(offset)
        if target is None:
            return self._result(request_id, None)

        spans = semantics.references_to(target, include_declaration=True)
        previous_end = -1
        for span in spans:
            if span.start < previous_end:
                return self._error(request_id, -32603, "Unsafe overlapping rename edits")
            previous_end = span.end

        edits = [
            {"range": self._range(source, span), "newText": new_name}
            for span in spans
        ]
        return self._result(request_id, {"changes": {semantics.uri: edits}})

    def _semantic_query(
        self, params: Any
    ) -> tuple[SemanticSnapshot | None, int, SourceText] | None:
        if not isinstance(params, dict):
            return None
        text_document = params.get("textDocument")
        position = params.get("position")
        if not isinstance(text_document, dict) or not isinstance(position, dict):
            return None
        uri = text_document.get("uri")
        line = position.get("line")
        character = position.get("character")
        if not isinstance(uri, str) or not uri:
            return None
        try:
            lsp_position = Position(line=line, character=character)
        except SourceError:
            return None

        semantics = self.semantics.get(uri)
        document = self.documents.get(uri)
        if document is None:
            return None
        source = SourceText(document.text)
        try:
            offset = source.offset_at(lsp_position)
        except SourceError:
            return None
        if semantics is None:
            return None, offset, source
        if semantics.symbols.syntax.document is not document:
            return None, offset, source
        return semantics, offset, source

    @staticmethod
    def _range(source: SourceText, span: Span) -> dict[str, Any]:
        start, end = source.range_from_span(span)
        return {
            "start": {"line": start.line, "character": start.character},
            "end": {"line": end.line, "character": end.character},
        }

    @staticmethod
    def _location(uri: str, source: SourceText, span: Span) -> dict[str, Any]:
        return {"uri": uri, "range": LanguageServer._range(source, span)}

    @staticmethod
    def _result(request_id: Any, result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        error = JsonRpcError(code, message)
        return {"jsonrpc": "2.0", "id": request_id, "error": error.as_object()}
