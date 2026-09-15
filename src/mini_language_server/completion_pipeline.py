"""Unified exact-snapshot completion publication for the Nova product.

This module is the consolidation boundary for completion presentation. New
completion capabilities belong in this pipeline or in pure helpers; they must
not create another serial NovaProductLanguageServer subclass.
"""

from __future__ import annotations

from typing import Any

from .semantic import SemanticError
from .server import ServerState
from .source import Span
from .unary_plus_actions import NovaProductLanguageServer as _LegacyProductLanguageServer
from .workspace import WorkspaceIndexError

_COMPLETION_KIND_FUNCTION = 3
_COMPLETION_KIND_VARIABLE = 6
_COMPLETION_KIND_CONSTANT = 21


class NovaProductLanguageServer(_LegacyProductLanguageServer):
    """Nova product with one guarded completion presentation pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self._function_completion_snippets = False
        self._completion_insert_replace = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if (
            isinstance(message, dict)
            and message.get("method") == "initialize"
            and self.state is ServerState.PRE_INITIALIZE
        ):
            params = message.get("params")
            self._function_completion_snippets = (
                self._client_supports_completion_snippets(params)
            )
            self._completion_insert_replace = (
                self._client_supports_completion_insert_replace(params)
            )
        return super().handle(message)

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        semantics = None if parsed is None else parsed[0]
        offset = None if parsed is None else parsed[1]
        source = None if parsed is None else parsed[2]
        snapshots = () if semantics is None else self.workspace_symbols.snapshots()

        response = super()._handle_workspace_completion(request_id, params)
        if response is None or not isinstance(response.get("result"), list):
            return response

        insert_range = None
        replace_range = None
        if (
            self._completion_insert_replace
            and semantics is not None
            and offset is not None
            and source is not None
        ):
            text = semantics.symbols.syntax.document.text
            span = self._completion_identifier_span(text, offset)
            insert_range = self._range(source, Span(span.start, offset))
            replace_range = self._range(source, span)

        def publish() -> dict[str, Any]:
            items = response["result"]

            if self._function_completion_snippets and semantics is not None:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    label = item.get("label")
                    if not isinstance(label, str):
                        continue
                    detail = self._function_completion_detail(item)
                    if detail is None:
                        continue
                    snippet = self._function_call_snippet(label, detail)
                    if snippet is None:
                        continue
                    item["insertText"] = snippet
                    item["insertTextFormat"] = 2

            if insert_range is not None and replace_range is not None:
                for item in items:
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

            for item in items:
                if not isinstance(item, dict):
                    continue
                label = item.get("label")
                detail = item.get("detail")
                if not isinstance(label, str) or not isinstance(detail, str):
                    continue
                classification = self._completion_classification(detail)
                if classification is None:
                    continue
                kind, rank = classification
                item["kind"] = kind
                item["sortText"] = f"{rank}:{label}"

            if semantics is not None and offset is not None:
                text = semantics.symbols.syntax.document.text
                prefix = self._completion_identifier_prefix(text, offset)
                if prefix:
                    response["result"] = [
                        item
                        for item in items
                        if isinstance(item, dict)
                        and isinstance(item.get("label"), str)
                        and item["label"].startswith(prefix)
                    ]
            return response

        if semantics is None:
            return publish()
        try:
            return self.semantics.commit_if_current(
                semantics,
                lambda: self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, publish
                ),
            )
        except (SemanticError, WorkspaceIndexError):
            return self._error(request_id, -32801, "Content modified")

    def _function_completion_detail(self, item: dict[str, Any]) -> str | None:
        detail = item.get("detail")
        if isinstance(detail, str) and detail.startswith("fn "):
            return detail

        data = item.get("data")
        if not isinstance(data, dict):
            return None
        token = data.get("novaCompletionResolve")
        records = getattr(self, "_completion_resolve_records", None)
        if not isinstance(token, int) or not isinstance(records, dict):
            return None
        record = records.get(token)
        stored = getattr(record, "item", None)
        if not isinstance(stored, dict):
            return None
        detail = stored.get("detail")
        return detail if isinstance(detail, str) and detail.startswith("fn ") else None

    @staticmethod
    def _function_call_snippet(label: str, detail: str) -> str | None:
        opening = detail.find("(")
        closing = detail.rfind(")")
        if opening < 0 or closing < opening:
            return None
        parameters = detail[opening + 1 : closing].strip()
        if not parameters:
            return f"{label}()$0"

        names: list[str] = []
        for parameter in parameters.split(","):
            name, separator, _ = parameter.strip().partition(":")
            if not separator or not name:
                return None
            names.append(name.strip())
        placeholders = ", ".join(
            f"${{{index}:{name}}}" for index, name in enumerate(names, start=1)
        )
        return f"{label}({placeholders})$0"

    @staticmethod
    def _completion_identifier_span(text: str, offset: int) -> Span:
        if offset < 0 or offset > len(text):
            bounded = max(0, min(offset, len(text)))
            return Span(bounded, bounded)
        start = offset
        while start > 0 and NovaProductLanguageServer._identifier_character(
            text[start - 1]
        ):
            start -= 1
        end = offset
        while end < len(text) and NovaProductLanguageServer._identifier_character(
            text[end]
        ):
            end += 1
        return Span(start, end)

    @staticmethod
    def _identifier_character(character: str) -> bool:
        return character == "_" or character.isalnum()

    @staticmethod
    def _completion_identifier_prefix(text: str, offset: int) -> str:
        start = offset
        while start > 0:
            character = text[start - 1]
            if not (
                character == "_" or (character.isascii() and character.isalnum())
            ):
                break
            start -= 1
        return text[start:offset]

    @staticmethod
    def _completion_classification(detail: str) -> tuple[int, int] | None:
        if detail == "parameter" or detail.startswith("parameter:"):
            return _COMPLETION_KIND_VARIABLE, 0
        if detail == "variable" or detail.startswith("variable:"):
            return _COMPLETION_KIND_VARIABLE, 0
        if detail == "constant" or detail.startswith("constant:"):
            return _COMPLETION_KIND_CONSTANT, 1
        if detail == "function" or detail.startswith("fn "):
            return _COMPLETION_KIND_FUNCTION, 2
        return None

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
