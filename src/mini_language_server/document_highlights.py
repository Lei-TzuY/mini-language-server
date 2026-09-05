"""Exact-snapshot LSP document-highlight capability."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .document_symbols import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Product server extended with generic exact-snapshot document highlights."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/documentHighlight"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_document_highlight(
                message.get("id"), message.get("params")
            )

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_document_highlights(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentHighlightProvider"] = True
        return result

    @staticmethod
    def _client_supports_document_highlights(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("documentHighlight"), dict)

    def _handle_document_highlight(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None:
            return self._error(request_id, -32602, "Invalid params")

        try:
            parsed = self._semantic_query(params)
            if parsed is None:
                return self._error(request_id, -32602, "Invalid params")

            self.requests.checkpoint(context)
            semantics, offset, source = parsed
            if semantics is None:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            target = semantics.definition_at(offset)
            self.requests.checkpoint(context)
            if target is None:
                return self._current_semantic_result(semantics, request_id, [])

            spans = semantics.references_to(target, include_declaration=True)
            highlights = [
                {"range": self._range(source, span), "kind": 1} for span in spans
            ]
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, highlights)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
