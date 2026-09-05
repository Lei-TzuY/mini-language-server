"""Exact-workspace Nova parameter inlay hints."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .nova import NovaFunctionSyntax
from .server import ServerState
from .source import Position, SourceError, SourceText, Span
from .unresolved_name_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final product server with exact-snapshot Nova parameter inlay hints."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/inlayHint"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_inlay_hint(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_inlay_hint(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["inlayHintProvider"] = True
        return result

    @staticmethod
    def _client_supports_inlay_hint(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("inlayHint"), dict)

    def _handle_inlay_hint(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            source_range = params.get("range")
            if uri is None or not isinstance(source_range, dict):
                return self._error(request_id, -32602, "Invalid params")
            start = source_range.get("start")
            end = source_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                return self._error(request_id, -32602, "Invalid params")

            document = self.documents.get(uri)
            if document is None:
                return self._error(request_id, -32602, "Invalid params")
            source = SourceText(document.text)
            try:
                start_offset = source.offset_at(
                    Position(line=start.get("line"), character=start.get("character"))
                )
                end_offset = source.offset_at(
                    Position(line=end.get("line"), character=end.get("character"))
                )
            except SourceError:
                return self._error(request_id, -32602, "Invalid params")
            if end_offset < start_offset:
                return self._error(request_id, -32602, "Invalid params")

            self.requests.checkpoint(context)
            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            tree = semantics.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                self.requests.checkpoint(context)
                return self._current_semantic_result(semantics, request_id, [])

            snapshots = self.workspace_symbols.snapshots()
            hints: list[tuple[int, dict[str, Any]]] = []
            for name, span in tree.calls:
                parsed = self._call_argument_spans(document.text, span.end)
                if parsed is None:
                    continue
                _, _, arguments = parsed
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) != 1:
                    continue
                parameters = self._declaration_parameter_names(declarations[0])
                for parameter, argument in zip(parameters, arguments, strict=False):
                    if not (start_offset <= argument.start < end_offset):
                        continue
                    position = self._range(source, Span(argument.start, argument.start))["start"]
                    hints.append(
                        (
                            argument.start,
                            {
                                "position": position,
                                "label": f"{parameter}:",
                                "kind": 2,
                                "paddingRight": True,
                            },
                        )
                    )

            self.requests.checkpoint(context)
            result = [hint for _, hint in sorted(hints, key=lambda item: item[0])]
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._current_semantic_result(
                        semantics, request_id, result
                    ),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _declaration_parameter_names(declaration: Any) -> tuple[str, ...]:
        tree = declaration.snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return ()
        owner = declaration.symbol.span
        return tuple(parameter.name for parameter in tree.parameters if parameter.owner == owner)

    @classmethod
    def _call_argument_spans(
        cls, text: str, name_end: int
    ) -> tuple[int, int, tuple[Span, ...]] | None:
        opening = text.find("(", name_end)
        if opening < 0 or text[name_end:opening].strip():
            return None
        closing = cls._matching_paren(text, opening)
        if closing is None:
            return None
        body_start = opening + 1
        body = text[body_start:closing]
        if not body.strip():
            return opening, closing, ()

        spans: list[Span] = []
        depth = 0
        segment_start = 0
        for index, character in enumerate(body):
            if character == "(":
                depth += 1
            elif character == ")" and depth:
                depth -= 1
            elif character == "," and depth == 0:
                span = cls._trimmed_span(body, body_start, segment_start, index)
                if span is not None:
                    spans.append(span)
                segment_start = index + 1
        span = cls._trimmed_span(body, body_start, segment_start, len(body))
        if span is not None:
            spans.append(span)
        return opening, closing, tuple(spans)

    @staticmethod
    def _trimmed_span(
        body: str, body_start: int, start: int, end: int
    ) -> Span | None:
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        if start == end:
            return None
        return Span(body_start + start, body_start + end)
