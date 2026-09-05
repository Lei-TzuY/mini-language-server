"""Exact-snapshot LSP document-symbol capability."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .product import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText

_SYMBOL_KINDS = {
    "file": 1,
    "module": 2,
    "namespace": 3,
    "package": 4,
    "class": 5,
    "method": 6,
    "property": 7,
    "field": 8,
    "constructor": 9,
    "enum": 10,
    "interface": 11,
    "function": 12,
    "variable": 13,
    "parameter": 13,
    "constant": 14,
    "string": 15,
    "number": 16,
    "boolean": 17,
    "array": 18,
    "object": 19,
    "key": 20,
    "null": 21,
    "enumMember": 22,
    "struct": 23,
    "event": 24,
    "operator": 25,
    "typeParameter": 26,
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Product server extended with generic exact-snapshot document symbols."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/documentSymbol"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_document_symbol(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_document_symbols(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentSymbolProvider"] = True
        return result

    @staticmethod
    def _client_supports_document_symbols(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("documentSymbol"), dict)

    def _handle_document_symbol(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None:
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            assert uri is not None
            self.requests.checkpoint(context)
            document = self.documents.get(uri)
            if document is None:
                return self._error(request_id, -32602, "Invalid params")
            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            source = SourceText(document.text)
            symbols = []
            for symbol in sorted(
                semantics.symbols.symbols,
                key=lambda item: (item.span.start, item.span.end, item.name, item.kind),
            ):
                source_range = self._range(source, symbol.span)
                symbols.append(
                    {
                        "name": symbol.name,
                        "kind": _SYMBOL_KINDS.get(symbol.kind, 13),
                        "range": source_range,
                        "selectionRange": source_range,
                    }
                )

            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, symbols)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
