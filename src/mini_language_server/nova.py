"""Executable Nova adapter layered on the language-independent tooling core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .documents import Document
from .semantic import Reference, SemanticError, SemanticSnapshot
from .server import LanguageServer
from .source import Span
from .symbols import Symbol, SymbolError
from .syntax import SyntaxError

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_FUNCTION_DECLARATION = re.compile(rf"\bfn\s+({_IDENTIFIER})\s*(?=\()")
_CALL = re.compile(rf"\b({_IDENTIFIER})\s*(?=\()")


@dataclass(frozen=True, slots=True)
class NovaFunctionSyntax:
    """Minimal immutable syntax payload for function declarations and calls."""

    declarations: tuple[tuple[str, Span], ...]
    calls: tuple[tuple[str, Span], ...]


class NovaFunctionAdapter:
    """Analyze the first serious Nova slice without leaking Nova rules into core stores.

    This adapter intentionally owns a bounded subset: named ``fn`` declarations and
    identifier calls. Calls resolve only when exactly one declaration with that name
    exists, so ambiguous duplicate declarations never produce an unsafe reference.
    """

    language_id = "nova"

    @staticmethod
    def parse(text: str) -> NovaFunctionSyntax:
        declarations = tuple(
            (match.group(1), Span(match.start(1), match.end(1)))
            for match in _FUNCTION_DECLARATION.finditer(text)
        )
        declaration_spans = {span for _, span in declarations}
        calls = tuple(
            (match.group(1), Span(match.start(1), match.end(1)))
            for match in _CALL.finditer(text)
            if Span(match.start(1), match.end(1)) not in declaration_spans
        )
        return NovaFunctionSyntax(declarations=declarations, calls=calls)

    def publish(self, server: LanguageServer, document: Document) -> SemanticSnapshot:
        """Publish syntax, symbols, and semantics against one exact document object."""
        parsed = self.parse(document.text)
        syntax = server.syntax.publish(document, parsed)
        symbols = server.symbols.publish(
            syntax,
            (Symbol(name, "function", span) for name, span in parsed.declarations),
        )

        by_name: dict[str, list[Symbol]] = {}
        for symbol in symbols.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)

        references = []
        for name, span in parsed.calls:
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                references.append(Reference(span, candidates[0]))
        return server.semantics.publish(symbols, references)


class NovaLanguageServer(LanguageServer):
    """LanguageServer composition that automatically analyzes open Nova documents."""

    def __init__(self, adapter: NovaFunctionAdapter | None = None) -> None:
        super().__init__()
        self.nova_adapter = adapter or NovaFunctionAdapter()

    def _handle_document_notification(self, method: str, params: Any) -> None:
        super()._handle_document_notification(method, params)
        if method not in {"textDocument/didOpen", "textDocument/didChange"}:
            return
        uri = self._document_uri(params)
        if uri is None:
            return
        document = self.documents.get(uri)
        if document is None or document.language_id != self.nova_adapter.language_id:
            return
        try:
            self.nova_adapter.publish(self, document)
        except (SyntaxError, SymbolError, SemanticError):
            # A concurrent document/snapshot replacement won the publication race.
            # The generic stores already reject every stale parent identity.
            return
