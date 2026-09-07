"""Exact-snapshot trivia-aware on-type indentation for Nova."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .server import ServerState
from .source import Position, SourceError, SourceText
from .typed_local_annotations import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative current-line on-type indentation."""

    _ON_TYPE_TRIGGERS = frozenset({"}"})

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/onTypeFormatting"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_on_type_formatting(
                message.get("id"), message.get("params")
            )

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_on_type_formatting(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentOnTypeFormattingProvider"] = {
                    "firstTriggerCharacter": "}",
                }
        return result

    @staticmethod
    def _client_supports_on_type_formatting(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("onTypeFormatting"), dict)

    def _handle_on_type_formatting(
        self, request_id: Any, params: Any
    ) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            ch = params.get("ch")
            position = params.get("position")
            options = params.get("options")
            if (
                ch not in self._ON_TYPE_TRIGGERS
                or not isinstance(position, dict)
                or not isinstance(options, dict)
            ):
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

            line = position.get("line")
            character = position.get("character")
            if (
                isinstance(line, bool)
                or not isinstance(line, int)
                or isinstance(character, bool)
                or not isinstance(character, int)
            ):
                return self._error(request_id, -32602, "Invalid params")
            source = SourceText(document.text)
            try:
                source.offset_at(Position(line=line, character=character))
            except SourceError:
                return self._error(request_id, -32602, "Invalid params")

            edit = self._nova_on_type_indent_edit(
                document.text,
                line=line,
                tab_size=tab_size,
                insert_spaces=insert_spaces,
            )
            self.requests.checkpoint(context)
            result = [] if edit is None else [edit]
            return self._current_semantic_result(semantics, request_id, result)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _nova_on_type_indent_edit(
        self,
        text: str,
        *,
        line: int,
        tab_size: int,
        insert_spaces: bool,
    ) -> dict[str, Any] | None:
        code = self.nova_adapter.code_view(text)
        source_lines = text.splitlines(keepends=True)
        code_lines = code.splitlines(keepends=True)
        if line < 0 or line >= len(source_lines):
            return None

        depth = 0
        for code_line in code_lines[:line]:
            code_body = code_line.rstrip("\r\n")
            depth = max(0, depth + code_body.count("{") - code_body.count("}"))

        source_body = source_lines[line].rstrip("\r\n")
        code_body = code_lines[line].rstrip("\r\n")
        stripped_source = source_body.lstrip(" \t")
        if not stripped_source:
            return None
        stripped_code = code_body.lstrip(" \t")
        leading_closes = len(stripped_code) - len(stripped_code.lstrip("}"))
        line_depth = max(0, depth - leading_closes)
        indent_unit = " " * tab_size if insert_spaces else "\t"
        desired = indent_unit * line_depth
        current = source_body[: len(source_body) - len(stripped_source)]
        if desired == current:
            return None
        return {
            "range": {
                "start": {"line": line, "character": 0},
                "end": {"line": line, "character": len(current)},
            },
            "newText": desired,
        }
