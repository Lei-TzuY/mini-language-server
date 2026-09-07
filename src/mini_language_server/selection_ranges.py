"""Exact-snapshot lexical selection ranges for Nova documents."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .nova import NovaFunctionSyntax
from .safe_renames import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import Position, SourceError, SourceText, Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with lexical exact-snapshot selection ranges."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/selectionRange"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_selection_range(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_selection_ranges(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["selectionRangeProvider"] = True
        return result

    @staticmethod
    def _client_supports_selection_ranges(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("selectionRange"), dict)

    def _handle_selection_range(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            positions = params.get("positions")
            if not isinstance(positions, list) or not positions:
                return self._error(request_id, -32602, "Invalid params")
            assert uri is not None

            self.requests.checkpoint(context)
            document = self.documents.get(uri)
            if document is None:
                return self._error(request_id, -32602, "Invalid params")
            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            source = SourceText(document.text)
            offsets: list[int] = []
            try:
                for item in positions:
                    if not isinstance(item, dict):
                        raise SourceError("invalid position")
                    offsets.append(
                        source.offset_at(
                            Position(line=item.get("line"), character=item.get("character"))
                        )
                    )
            except (SourceError, TypeError):
                return self._error(request_id, -32602, "Invalid params")

            tree = semantics.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                self.requests.checkpoint(context)
                return self._current_semantic_result(semantics, request_id, [])

            token_spans = self._nova_token_spans(tree)
            function_spans = tuple(
                span
                for _, owner in tree.declarations
                if (span := self._nova_function_span(document.text, owner)) is not None
            )
            document_span = Span(0, len(document.text))
            ranges = [
                self._selection_range_for_offset(
                    source,
                    offset,
                    token_spans,
                    function_spans,
                    document_span,
                )
                for offset in offsets
            ]
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, ranges)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _nova_token_spans(tree: NovaFunctionSyntax) -> tuple[Span, ...]:
        spans = {span for _, span in tree.declarations}
        spans.update(span for _, span in tree.calls)
        for items in (
            tree.parameters,
            tree.parameter_references,
            tree.locals,
            tree.local_references,
            tree.unresolved_names,
        ):
            spans.update(item.span for item in items)
        return tuple(sorted(spans, key=lambda span: (span.start, span.end)))

    def _nova_function_span(self, text: str, owner: Span) -> Span | None:
        start = text.rfind("fn", 0, owner.start + 1)
        if start < 0 or text[start + 2 : owner.start].strip():
            return None
        opening = text.find("{", owner.end)
        if opening < 0:
            return None
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return None
        return Span(start, closing + 1)

    def _selection_range_for_offset(
        self,
        source: SourceText,
        offset: int,
        token_spans: tuple[Span, ...],
        function_spans: tuple[Span, ...],
        document_span: Span,
    ) -> dict[str, Any]:
        parents: list[Span] = []
        token = next(
            (span for span in token_spans if span.start <= offset < span.end),
            None,
        )
        if token is not None:
            parents.append(token)

        function = next(
            (span for span in function_spans if span.start <= offset < span.end),
            None,
        )
        if function is not None and (not parents or function != parents[-1]):
            parents.append(function)

        if not parents or parents[-1] != document_span:
            parents.append(document_span)

        result: dict[str, Any] | None = None
        for span in reversed(parents):
            current: dict[str, Any] = {"range": self._range(source, span)}
            if result is not None:
                current["parent"] = result
            result = current
        assert result is not None
        return result
