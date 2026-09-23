"""Unified exact-snapshot completion publication for the Nova product.

This module is the consolidation boundary for completion presentation. New
completion capabilities belong in this pipeline or in pure helpers; they must
not create another serial NovaProductLanguageServer subclass.
"""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .closed_assignment_semantics import (
    NovaProductLanguageServer as _ProductLanguageServer,
)
from .nova import NovaFunctionSyntax
from .semantic import SemanticError
from .server import ServerState
from .source import Span
from .tracing import TraceLanguageServerMixin
from .uint_conversion_operand_completion import _direct_conversion_operand
from .workspace import WorkspaceIndexError

_COMPLETION_KIND_FUNCTION = 3
_COMPLETION_KIND_VARIABLE = 6
_COMPLETION_KIND_CONSTANT = 21


class NovaProductLanguageServer(TraceLanguageServerMixin, _ProductLanguageServer):
    """Nova product with one guarded completion presentation pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self._function_completion_snippets = False
        self._completion_insert_replace = False
        self._completion_list_edit_range = False
        self._inline_completion = False
        self._inline_value = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        method = message.get("method") if isinstance(message, dict) else None
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._function_completion_snippets = (
                self._client_supports_completion_snippets(params)
            )
            self._completion_insert_replace = (
                self._client_supports_completion_insert_replace(params)
            )
            self._completion_list_edit_range = (
                "editRange" in self._client_completion_list_item_defaults(params)
            )
            self._inline_completion = self._client_supports_inline_completion(params)
            self._inline_value = self._client_supports_inline_value(params)

        if (
            method == "textDocument/inlineCompletion"
            and isinstance(message, dict)
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._inline_completion
        ):
            return self._handle_inline_completion(
                message.get("id"),
                message.get("params"),
            )

        if (
            method == "textDocument/inlineValue"
            and isinstance(message, dict)
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._inline_value
        ):
            return self._handle_inline_value(
                message.get("id"),
                message.get("params"),
            )

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._inline_completion
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["inlineCompletionProvider"] = {}
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._inline_value
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["inlineValueProvider"] = True
        return result

    def _handle_inline_value(
        self,
        request_id: Any,
        params: Any,
    ) -> dict[str, Any]:
        """Return debugger variable lookups for exact visible Nova locals."""
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        context_value = params.get("context")
        viewport_value = params.get("range")
        if not isinstance(context_value, dict) or not isinstance(viewport_value, dict):
            return self._error(request_id, -32602, "Invalid params")

        frame_id = context_value.get("frameId")
        stopped_value = context_value.get("stoppedLocation")
        if (
            isinstance(frame_id, bool)
            or not isinstance(frame_id, int)
            or not isinstance(stopped_value, dict)
        ):
            return self._error(request_id, -32602, "Invalid params")

        uri = self._document_uri(params)
        if uri is None:
            return self._error(request_id, -32602, "Invalid params")
        document = self.documents.get(uri)
        if document is None or document.language_id != self.nova_adapter.language_id:
            return self._result(request_id, [])

        viewport = self._parse_range(document.text, viewport_value)
        stopped = self._parse_range(document.text, stopped_value)
        if viewport is None or stopped is None:
            return self._error(request_id, -32602, "Invalid params")

        source = self._source_text(document.text)
        viewport_span = source.span_from_range(*viewport)
        stopped_span = source.span_from_range(*stopped)
        semantics = self.semantics.get(uri)
        if (
            semantics is None
            or semantics.symbols.syntax.document is not document
            or not isinstance(semantics.symbols.syntax.tree, NovaFunctionSyntax)
        ):
            return self._result(request_id, [])

        tree = semantics.symbols.syntax.tree
        owner = self._completion_scope_owner(document.text, tree, stopped_span.start)
        try:
            request_context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(request_context)
            if owner is None:
                self.requests.checkpoint(request_context)
                return self._current_semantic_result(semantics, request_id, [])

            targets = self._inline_value_visible_targets(
                semantics,
                tree,
                owner,
                stopped_span.start,
            )
            rendered: dict[tuple[int, int, str], dict[str, Any]] = {}
            for target in targets:
                spans = [
                    target.span,
                    *(
                        reference.span
                        for reference in semantics.references
                        if reference.target is target
                    ),
                ]
                for span in spans:
                    if not (
                        viewport_span.start <= span.start
                        and span.end <= viewport_span.end
                    ):
                        continue
                    rendered[(span.start, span.end, target.name)] = {
                        "range": self._range(source, span),
                        "variableName": target.name,
                        "caseSensitiveLookup": True,
                    }

            items = [
                rendered[key]
                for key in sorted(rendered, key=lambda item: (item[0], item[1], item[2]))
            ]
            self.requests.checkpoint(request_context)

            def publish() -> dict[str, Any]:
                self.requests.checkpoint(request_context)
                return self._result(request_id, items)

            try:
                return self.semantics.commit_if_current(semantics, publish)
            except SemanticError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(request_context)

    @staticmethod
    def _inline_value_visible_targets(
        semantics: Any,
        tree: NovaFunctionSyntax,
        owner: Span,
        stopped_offset: int,
    ) -> tuple[Any, ...]:
        """Mirror Nova function-scoped local shadowing at one debugger stop."""
        parameters_by_name: dict[str, list[Any]] = {}
        locals_by_name: dict[str, list[Any]] = {}
        symbols_by_span = {symbol.span: symbol for symbol in semantics.symbols.symbols}

        for parameter in tree.parameters:
            if parameter.owner != owner:
                continue
            symbol = symbols_by_span.get(parameter.span)
            if symbol is not None and symbol.kind == "parameter":
                parameters_by_name.setdefault(parameter.name, []).append(symbol)

        for local in tree.locals:
            if local.owner != owner or local.span.end > stopped_offset:
                continue
            symbol = symbols_by_span.get(local.span)
            if symbol is not None and symbol.kind == "variable":
                locals_by_name.setdefault(local.name, []).append(symbol)

        visible: list[Any] = []
        for name in sorted(set(parameters_by_name) | set(locals_by_name)):
            locals_ = locals_by_name.get(name, [])
            if len(locals_) == 1:
                visible.append(locals_[0])
                continue
            if len(locals_) > 1:
                continue
            parameters = parameters_by_name.get(name, [])
            if len(parameters) == 1:
                visible.append(parameters[0])
        return tuple(visible)

    def _handle_inline_completion(
        self,
        request_id: Any,
        params: Any,
    ) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        context_value = params.get("context")
        if not isinstance(context_value, dict):
            return self._error(request_id, -32602, "Invalid params")
        trigger_kind = context_value.get("triggerKind")
        if (
            isinstance(trigger_kind, bool)
            or not isinstance(trigger_kind, int)
            or trigger_kind not in {1, 2}
        ):
            return self._error(request_id, -32602, "Invalid params")

        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")
        semantics, offset, source = parsed

        uri = self._document_uri(params)
        assert uri is not None
        try:
            request_context = self.requests.start(request_id, uri=uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(request_context)
            if semantics is None:
                self.requests.checkpoint(request_context)
                return self._result(request_id, [])

            text = semantics.symbols.syntax.document.text
            code = self.nova_adapter.code_view(text)
            span = self._completion_identifier_span(code, offset)
            prefix = self._completion_identifier_prefix(code, offset)
            if not prefix:
                self.requests.checkpoint(request_context)
                return self._current_semantic_result(semantics, request_id, [])

            if code[max(0, span.start - 2) : span.start] == "::":
                self.requests.checkpoint(request_context)
                return self._current_semantic_result(semantics, request_id, [])
            if _direct_conversion_operand(code, offset) is not None:
                self.requests.checkpoint(request_context)
                return self._current_semantic_result(semantics, request_id, [])

            snapshots = self.workspace_symbols.snapshots()
            candidates = self._typed_completion_items(
                semantics,
                offset,
                snapshots=snapshots,
            )
            current_identifier = code[span.start : span.end]

            ranked: dict[str, tuple[int, dict[str, Any]]] = {}
            for item in candidates:
                label = item.get("label")
                detail = item.get("detail")
                if (
                    not isinstance(label, str)
                    or not label.startswith(prefix)
                    or label == current_identifier
                    or not isinstance(detail, str)
                ):
                    continue
                classification = self._completion_classification(detail)
                rank = 3 if classification is None else classification[1]
                rendered = {
                    "insertText": label,
                    "range": self._range(source, span),
                }
                previous = ranked.get(label)
                if previous is None or rank < previous[0]:
                    ranked[label] = (rank, rendered)

            items = [
                rendered
                for _, rendered in sorted(
                    ranked.values(),
                    key=lambda entry: (
                        entry[0],
                        entry[1]["insertText"],
                    ),
                )
            ]
            if trigger_kind == 2 and items:
                items = items[:1]

            self.requests.checkpoint(request_context)

            def publish() -> dict[str, Any]:
                self.requests.checkpoint(request_context)
                return self._result(request_id, items)

            try:
                return self.semantics.commit_if_current(
                    semantics,
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        snapshots,
                        publish,
                    ),
                )
            except (SemanticError, WorkspaceIndexError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(request_context)

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
            (self._completion_insert_replace or self._completion_list_edit_range)
            and semantics is not None
            and offset is not None
            and source is not None
        ):
            text = semantics.symbols.syntax.document.text
            span = self._completion_identifier_span(text, offset)
            insert_range = self._range(source, Span(span.start, offset))
            replace_range = self._range(source, span)

        prefix = None
        if semantics is not None and offset is not None:
            text = semantics.symbols.syntax.document.text
            prefix = self._completion_identifier_prefix(text, offset)

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

            shared_edit_range: dict[str, Any] | None = None
            if (
                self._completion_list_edit_range
                and insert_range is not None
                and replace_range is not None
            ):
                shared_edit_range = (
                    {"insert": insert_range, "replace": replace_range}
                    if self._completion_insert_replace
                    else replace_range
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    label = item.get("label")
                    if not isinstance(label, str):
                        continue
                    new_text = item.pop("insertText", None)
                    if isinstance(new_text, str) and new_text != label:
                        item["textEditText"] = new_text
            elif insert_range is not None and replace_range is not None:
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

            if prefix:
                items = [
                    item
                    for item in items
                    if isinstance(item, dict)
                    and isinstance(item.get("label"), str)
                    and item["label"].startswith(prefix)
                ]

            if shared_edit_range is not None:
                response["result"] = {
                    "isIncomplete": False,
                    "itemDefaults": {"editRange": shared_edit_range},
                    "items": items,
                }
            else:
                response["result"] = items
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
    def _client_supports_inline_value(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("inlineValue"), dict)

    @staticmethod
    def _client_supports_inline_completion(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("inlineCompletion"), dict)

    @staticmethod
    def _client_completion_list_item_defaults(params: Any) -> frozenset[str]:
        """Return CompletionList defaults explicitly supported by the client."""
        if not isinstance(params, dict):
            return frozenset()
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return frozenset()
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return frozenset()
        completion = text_document.get("completion")
        if not isinstance(completion, dict):
            return frozenset()
        completion_list = completion.get("completionList")
        if not isinstance(completion_list, dict):
            return frozenset()
        defaults = completion_list.get("itemDefaults")
        if not isinstance(defaults, list):
            return frozenset()
        supported = {"editRange"}
        return frozenset(
            value
            for value in defaults
            if isinstance(value, str) and value in supported
        )

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
