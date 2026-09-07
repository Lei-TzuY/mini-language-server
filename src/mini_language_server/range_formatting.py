"""Exact-snapshot trivia-aware range formatting for Nova."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .on_type_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import Position, SourceError, SourceText


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative range-scoped indentation formatting."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/rangeFormatting"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_range_formatting(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_range_formatting(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentRangeFormattingProvider"] = True
        return result

    @staticmethod
    def _client_supports_range_formatting(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("rangeFormatting"), dict)

    def _handle_range_formatting(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            options = params.get("options")
            requested_range = params.get("range")
            if not isinstance(options, dict) or not isinstance(requested_range, dict):
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

            parsed_range = self._parse_range(document.text, requested_range)
            if parsed_range is None:
                return self._error(request_id, -32602, "Invalid params")
            start, end = parsed_range
            edits = self._nova_range_indent_edits(
                document.text,
                start=start,
                end=end,
                tab_size=tab_size,
                insert_spaces=insert_spaces,
            )
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, edits)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _parse_range(text: str, value: dict[str, Any]) -> tuple[Position, Position] | None:
        start_value = value.get("start")
        end_value = value.get("end")
        if not isinstance(start_value, dict) or not isinstance(end_value, dict):
            return None

        def parse_position(raw: dict[str, Any]) -> Position | None:
            line = raw.get("line")
            character = raw.get("character")
            if (
                isinstance(line, bool)
                or not isinstance(line, int)
                or isinstance(character, bool)
                or not isinstance(character, int)
            ):
                return None
            return Position(line=line, character=character)

        start = parse_position(start_value)
        end = parse_position(end_value)
        if start is None or end is None:
            return None
        source = SourceText(text)
        try:
            start_offset = source.offset_at(start)
            end_offset = source.offset_at(end)
        except SourceError:
            return None
        if start_offset > end_offset:
            return None
        return start, end

    def _nova_range_indent_edits(
        self,
        text: str,
        *,
        start: Position,
        end: Position,
        tab_size: int,
        insert_spaces: bool,
    ) -> list[dict[str, Any]]:
        """Reindent only leading whitespace spans fully contained in the range."""
        code = self.nova_adapter.code_view(text)
        source_lines = text.splitlines(keepends=True)
        code_lines = code.splitlines(keepends=True)
        indent_unit = " " * tab_size if insert_spaces else "\t"
        depth = 0
        edits: list[dict[str, Any]] = []

        for line, (source_line, code_line) in enumerate(
            zip(source_lines, code_lines, strict=True)
        ):
            source_body = source_line.rstrip("\r\n")
            code_body = code_line.rstrip("\r\n")
            stripped_source = source_body.lstrip(" \t")
            stripped_code = code_body.lstrip(" \t")
            leading_closes = len(stripped_code) - len(stripped_code.lstrip("}"))
            line_depth = max(0, depth - leading_closes)

            if stripped_source:
                current = source_body[: len(source_body) - len(stripped_source)]
                desired = indent_unit * line_depth
                edit_start = Position(line=line, character=0)
                edit_end = Position(line=line, character=len(current))
                if (
                    desired != current
                    and self._position_at_or_after(edit_start, start)
                    and self._position_at_or_before(edit_end, end)
                ):
                    edits.append(
                        {
                            "range": {
                                "start": {"line": line, "character": 0},
                                "end": {"line": line, "character": len(current)},
                            },
                            "newText": desired,
                        }
                    )

            depth = max(0, depth + code_body.count("{") - code_body.count("}"))

        return edits

    @staticmethod
    def _position_at_or_after(position: Position, boundary: Position) -> bool:
        return (position.line, position.character) >= (boundary.line, boundary.character)

    @staticmethod
    def _position_at_or_before(position: Position, boundary: Position) -> bool:
        return (position.line, position.character) <= (boundary.line, boundary.character)
