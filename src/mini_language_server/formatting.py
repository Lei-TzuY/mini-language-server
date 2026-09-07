"""Exact-snapshot deterministic document formatting for Nova."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .lexical_nova import LexicalNovaFunctionAdapter
from .lexical_nova import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText, Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with trivia-aware whole-document formatting."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/formatting"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_document_formatting(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_document_formatting(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentFormattingProvider"] = True
        return result

    @staticmethod
    def _client_supports_document_formatting(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("formatting"), dict)

    def _handle_document_formatting(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            options = params.get("options")
            if not isinstance(options, dict):
                return self._error(request_id, -32602, "Invalid params")
            tab_size = options.get("tabSize")
            insert_spaces = options.get("insertSpaces")
            if (
                isinstance(tab_size, bool)
                or not isinstance(tab_size, int)
                or tab_size <= 0
                or not isinstance(insert_spaces, bool)
            ):
                return self._error(request_id, -32602, "Invalid params")

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
                tab_size=tab_size,
                insert_spaces=insert_spaces,
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

    @staticmethod
    def _format_nova_document(
        text: str, *, tab_size: int, insert_spaces: bool
    ) -> str:
        """Reindent structural braces while preserving all non-leading source text.

        Structural depth is computed from the offset-preserving lexical code view, so
        braces inside comments and quoted strings never affect indentation.
        """
        code = LexicalNovaFunctionAdapter.code_view(text)
        source_lines = text.splitlines(keepends=True)
        code_lines = code.splitlines(keepends=True)
        indent_unit = " " * tab_size if insert_spaces else "\t"
        depth = 0
        result: list[str] = []

        for source_line, code_line in zip(source_lines, code_lines, strict=True):
            source_body = source_line.rstrip("\r\n")
            newline = source_line[len(source_body) :]
            code_body = code_line.rstrip("\r\n")
            stripped_source = source_body.lstrip(" \t")
            stripped_code = code_body.lstrip(" \t")

            if not stripped_source:
                result.append(newline)
            else:
                leading_closes = len(stripped_code) - len(stripped_code.lstrip("}"))
                line_depth = max(0, depth - leading_closes)
                result.append(indent_unit * line_depth + stripped_source + newline)

            depth = max(0, depth + code_body.count("{") - code_body.count("}"))

        return "".join(result)
