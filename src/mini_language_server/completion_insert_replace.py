"""Negotiated insert/replace edits for exact-snapshot Nova completions."""

from __future__ import annotations

from typing import Any

from .function_completion_snippets import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticError
from .server import ServerState
from .source import Span
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with exact identifier-aware completion text edits."""

    def __init__(self) -> None:
        super().__init__()
        self._completion_insert_replace = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if (
            isinstance(message, dict)
            and message.get("method") == "initialize"
            and self.state is ServerState.PRE_INITIALIZE
        ):
            self._completion_insert_replace = (
                self._client_supports_completion_insert_replace(message.get("params"))
            )
        return super().handle(message)

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        if not self._completion_insert_replace:
            return super()._handle_workspace_completion(request_id, params)

        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_completion(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

        text = semantics.symbols.syntax.document.text
        span = self._completion_identifier_span(text, offset)
        insert_range = self._range(source, Span(span.start, offset))
        replace_range = self._range(source, span)

        def enrich() -> dict[str, Any]:
            for item in response["result"]:
                if not isinstance(item, dict):
                    continue
                label = item.get("label")
                if not isinstance(label, str):
                    continue
                new_text = item.get("insertText")
                if not isinstance(new_text, str):
                    new_text = label
                item["textEdit"] = {
                    "newText": new_text,
                    "insert": insert_range,
                    "replace": replace_range,
                }
                item.pop("insertText", None)
            return response

        try:
            return self.semantics.commit_if_current(
                semantics,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, enrich
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    @staticmethod
    def _completion_identifier_span(text: str, offset: int) -> Span:
        if offset < 0 or offset > len(text):
            return Span(max(0, min(offset, len(text))), max(0, min(offset, len(text))))
        start = offset
        while start > 0 and NovaProductLanguageServer._identifier_character(text[start - 1]):
            start -= 1
        end = offset
        while end < len(text) and NovaProductLanguageServer._identifier_character(text[end]):
            end += 1
        return Span(start, end)

    @staticmethod
    def _identifier_character(character: str) -> bool:
        return character == "_" or character.isalnum()

    @staticmethod
    def _client_supports_completion_insert_replace(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        completion = text_document.get("completion")
        if not isinstance(completion, dict):
            return False
        completion_item = completion.get("completionItem")
        if not isinstance(completion_item, dict):
            return False
        return completion_item.get("insertReplaceSupport") is True
