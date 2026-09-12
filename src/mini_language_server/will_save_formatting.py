"""Exact-snapshot negotiated will-save formatting for Nova."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .implementation import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText, Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated formatting before save."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/willSaveWaitUntil"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_will_save_wait_until(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and isinstance(result.get("result"), dict)
            and self._client_supports_will_save_wait_until(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["textDocumentSync"] = {
                    "openClose": True,
                    "change": 2,
                    "willSaveWaitUntil": True,
                }
        return result

    @staticmethod
    def _client_supports_will_save_wait_until(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        synchronization = text_document.get("synchronization")
        return isinstance(synchronization, dict) and synchronization.get(
            "willSaveWaitUntil"
        ) is True

    def _handle_will_save_wait_until(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            assert uri is not None
            self.requests.checkpoint(context)
            document = self.documents.get(uri)
            if document is None or document.language_id != self.nova_adapter.language_id:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            formatted = self._format_nova_document(
                document.text,
                tab_size=4,
                insert_spaces=True,
            )
            self.requests.checkpoint(context)
            if formatted == document.text:
                return self._current_semantic_result(semantics, request_id, [])

            source = SourceText(document.text)
            edits = [
                {
                    "range": self._range(source, Span(0, len(document.text))),
                    "newText": formatted,
                }
            ]
            return self._current_semantic_result(semantics, request_id, edits)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
