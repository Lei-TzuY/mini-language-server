"""Exact-snapshot Nova function folding ranges."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .nova import NovaFunctionSyntax
from .server import ServerState
from .source import SourceText
from .typed_local_arguments import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact-snapshot function folding ranges."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/foldingRange"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_folding_range(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_folding_ranges(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["foldingRangeProvider"] = True
        return result

    @staticmethod
    def _client_supports_folding_ranges(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("foldingRange"), dict)

    def _handle_folding_range(self, request_id: Any, params: Any) -> dict[str, Any]:
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
            tree = semantics.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                self.requests.checkpoint(context)
                return self._current_semantic_result(semantics, request_id, [])

            source = SourceText(document.text)
            ranges: list[dict[str, int]] = []
            for _, owner in tree.declarations:
                opening = document.text.find("{", owner.end)
                if opening < 0:
                    continue
                closing = self.nova_adapter._matching_brace(document.text, opening)
                if closing is None:
                    continue
                start_line = source.position_at(opening).line
                closing_line = source.position_at(closing).line
                if closing_line <= start_line:
                    continue
                ranges.append({"startLine": start_line, "endLine": closing_line - 1})

            ranges.sort(key=lambda item: (item["startLine"], item["endLine"]))
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, ranges)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
