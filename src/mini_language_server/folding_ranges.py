"""Exact-snapshot Nova structural folding ranges."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .nova import NovaFunctionSyntax
from .server import ServerState
from .source import SourceText
from .typed_local_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_CONTROL_FLOW_KEYWORD = re.compile(r"\b(?:if|while)\b")
_ELSE_KEYWORD = re.compile(r"\belse\b")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact-snapshot structural folding ranges."""

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

    @staticmethod
    def _matching_delimiter(text: str, opening: int, left: str, right: str) -> int | None:
        depth = 0
        for index in range(opening, len(text)):
            character = text[index]
            if character == left:
                depth += 1
            elif character == right:
                depth -= 1
                if depth == 0:
                    return index
        return None

    @classmethod
    def _control_flow_openings(cls, code: str, start: int, end: int) -> tuple[int, ...]:
        openings: set[int] = set()

        for match in _CONTROL_FLOW_KEYWORD.finditer(code, start, end):
            cursor = match.end()
            while cursor < end and code[cursor].isspace():
                cursor += 1
            if cursor >= end or code[cursor] != "(":
                continue
            closing_condition = cls._matching_delimiter(code, cursor, "(", ")")
            if closing_condition is None or closing_condition >= end:
                continue
            cursor = closing_condition + 1
            while cursor < end and code[cursor].isspace():
                cursor += 1
            if cursor < end and code[cursor] == "{":
                openings.add(cursor)

        for match in _ELSE_KEYWORD.finditer(code, start, end):
            cursor = match.end()
            while cursor < end and code[cursor].isspace():
                cursor += 1
            if cursor < end and code[cursor] == "{":
                openings.add(cursor)

        return tuple(sorted(openings))

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
            code = self.nova_adapter.code_view(document.text)
            line_ranges: set[tuple[int, int]] = set()

            def add_block(opening: int, closing: int) -> None:
                start_line = source.position_at(opening).line
                closing_line = source.position_at(closing).line
                if closing_line > start_line:
                    line_ranges.add((start_line, closing_line - 1))

            for _, owner in tree.declarations:
                opening = code.find("{", owner.end)
                if opening < 0:
                    continue
                closing = self.nova_adapter._matching_brace(document.text, opening)
                if closing is None:
                    continue
                add_block(opening, closing)

                for control_opening in self._control_flow_openings(code, opening + 1, closing):
                    control_closing = self.nova_adapter._matching_brace(
                        document.text, control_opening
                    )
                    if control_closing is None or control_closing > closing:
                        continue
                    add_block(control_opening, control_closing)

            ranges = [
                {"startLine": start_line, "endLine": end_line}
                for start_line, end_line in sorted(line_ranges)
            ]
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, ranges)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
