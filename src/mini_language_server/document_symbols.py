"""Exact-snapshot LSP document-symbol capability."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .nova import NovaFunctionSyntax
from .product import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText, Span

_SYMBOL_KINDS = {
    "file": 1,
    "module": 2,
    "namespace": 3,
    "package": 4,
    "class": 5,
    "method": 6,
    "property": 7,
    "field": 8,
    "constructor": 9,
    "enum": 10,
    "interface": 11,
    "function": 12,
    "variable": 13,
    "parameter": 13,
    "constant": 14,
    "string": 15,
    "number": 16,
    "boolean": 17,
    "array": 18,
    "object": 19,
    "key": 20,
    "null": 21,
    "enumMember": 22,
    "struct": 23,
    "event": 24,
    "operator": 25,
    "typeParameter": 26,
}
_FUNCTION_PREFIX = re.compile(r"\bfn\s*$")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated exact-snapshot document symbols."""

    def __init__(self) -> None:
        super().__init__()
        self._hierarchical_document_symbols = False

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._hierarchical_document_symbols = (
                self._client_supports_hierarchical_document_symbols(
                    message.get("params")
                )
            )

        if (
            method == "textDocument/documentSymbol"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_document_symbol(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_document_symbols(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentSymbolProvider"] = True
        return result

    @staticmethod
    def _client_supports_document_symbols(params: Any) -> bool:
        document_symbol = NovaProductLanguageServer._document_symbol_capability(params)
        return isinstance(document_symbol, dict)

    @staticmethod
    def _client_supports_hierarchical_document_symbols(params: Any) -> bool:
        document_symbol = NovaProductLanguageServer._document_symbol_capability(params)
        return (
            isinstance(document_symbol, dict)
            and document_symbol.get("hierarchicalDocumentSymbolSupport") is True
        )

    @staticmethod
    def _document_symbol_capability(params: Any) -> Any:
        if not isinstance(params, dict):
            return None
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return None
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return None
        return text_document.get("documentSymbol")

    def _handle_document_symbol(self, request_id: Any, params: Any) -> dict[str, Any]:
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

            source = self._source_text(document.text)
            tree = semantics.symbols.syntax.tree
            if (
                self._hierarchical_document_symbols
                and isinstance(tree, NovaFunctionSyntax)
            ):
                symbols = self._hierarchical_nova_symbols(
                    source,
                    document.text,
                    semantics.symbols.symbols,
                    tree,
                )
            else:
                symbols = self._flat_symbol_information(
                    uri,
                    source,
                    semantics.symbols.symbols,
                    tree if isinstance(tree, NovaFunctionSyntax) else None,
                )

            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, symbols)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _hierarchical_nova_symbols(
        self,
        source: SourceText,
        text: str,
        symbols: tuple[Any, ...],
        tree: NovaFunctionSyntax,
    ) -> list[dict[str, Any]]:
        symbols_by_span = {symbol.span: symbol for symbol in symbols}
        owner_names = {span: name for name, span in tree.declarations}
        members_by_owner: dict[Span, list[Any]] = {}
        for member in (*tree.parameters, *tree.locals):
            symbol = symbols_by_span.get(member.span)
            if symbol is not None:
                members_by_owner.setdefault(member.owner, []).append(symbol)

        rendered_spans: set[Span] = set()
        result: list[dict[str, Any]] = []
        for name, owner in sorted(
            tree.declarations,
            key=lambda item: (item[1].start, item[1].end, item[0]),
        ):
            symbol = symbols_by_span.get(owner)
            if symbol is None:
                continue
            rendered_spans.add(symbol.span)
            selection_range = self._range(source, symbol.span)
            children = []
            for child in sorted(
                members_by_owner.get(owner, ()),
                key=lambda item: (
                    item.span.start,
                    item.span.end,
                    item.name,
                    item.kind,
                ),
            ):
                rendered_spans.add(child.span)
                child_range = self._range(source, child.span)
                children.append(
                    {
                        "name": child.name,
                        "kind": _SYMBOL_KINDS.get(child.kind, 13),
                        "range": child_range,
                        "selectionRange": child_range,
                    }
                )
            result.append(
                {
                    "name": name,
                    "kind": _SYMBOL_KINDS.get(symbol.kind, 13),
                    "range": self._nova_function_range(source, text, owner),
                    "selectionRange": selection_range,
                    "children": children,
                }
            )

        for symbol in self._sorted_symbols(symbols):
            if symbol.span in rendered_spans:
                continue
            source_range = self._range(source, symbol.span)
            item = {
                "name": symbol.name,
                "kind": _SYMBOL_KINDS.get(symbol.kind, 13),
                "range": source_range,
                "selectionRange": source_range,
            }
            container = self._container_name(tree, symbol.span, owner_names)
            if container is not None:
                item["detail"] = f"in {container}"
            result.append(item)
        return result

    def _flat_symbol_information(
        self,
        uri: str,
        source: SourceText,
        symbols: tuple[Any, ...],
        tree: NovaFunctionSyntax | None,
    ) -> list[dict[str, Any]]:
        owner_names = (
            {span: name for name, span in tree.declarations}
            if tree is not None
            else {}
        )
        result = []
        for symbol in self._sorted_symbols(symbols):
            item: dict[str, Any] = {
                "name": symbol.name,
                "kind": _SYMBOL_KINDS.get(symbol.kind, 13),
                "location": {
                    "uri": uri,
                    "range": self._range(source, symbol.span),
                },
            }
            if tree is not None:
                container = self._container_name(
                    tree,
                    symbol.span,
                    owner_names,
                )
                if container is not None:
                    item["containerName"] = container
            result.append(item)
        return result

    def _nova_function_range(
        self,
        source: SourceText,
        text: str,
        owner: Span,
    ) -> dict[str, dict[str, int]]:
        code = self.nova_adapter.code_view(text)
        prefix = code[: owner.start]
        declaration = _FUNCTION_PREFIX.search(prefix)
        start = declaration.start() if declaration is not None else owner.start
        opening = code.find("{", owner.end)
        if opening < 0:
            return self._range(source, owner)
        closing = self.nova_adapter._matching_brace(code, opening)
        if closing is None:
            return self._range(source, owner)
        return self._range(source, Span(start, closing + 1))

    @staticmethod
    def _container_name(
        tree: NovaFunctionSyntax,
        span: Span,
        owner_names: dict[Span, str],
    ) -> str | None:
        for member in (*tree.parameters, *tree.locals):
            if member.span == span:
                return owner_names.get(member.owner)
        return None

    @staticmethod
    def _sorted_symbols(symbols: tuple[Any, ...]) -> tuple[Any, ...]:
        return tuple(
            sorted(
                symbols,
                key=lambda item: (
                    item.span.start,
                    item.span.end,
                    item.name,
                    item.kind,
                ),
            )
        )
