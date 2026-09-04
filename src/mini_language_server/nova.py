"""Executable Nova adapter layered on the language-independent tooling core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .diagnostics import Diagnostic, DiagnosticError
from .documents import Document
from .semantic import Reference, SemanticError, SemanticSnapshot
from .server import LanguageServer, ServerState
from .source import Position, SourceError, SourceText, Span
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
    exists. Duplicate declarations, unresolved calls, and calls made ambiguous by
    duplicate declarations are surfaced through the generic diagnostic pipeline.
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
        """Publish one exact Nova snapshot chain and its deterministic diagnostics."""
        parsed = self.parse(document.text)
        syntax = server.syntax.publish(document, parsed)
        symbols = server.symbols.publish(
            syntax,
            (Symbol(name, "function", span) for name, span in parsed.declarations),
        )

        by_name: dict[str, list[Symbol]] = {}
        for symbol in symbols.symbols:
            by_name.setdefault(symbol.name, []).append(symbol)

        references: list[Reference] = []
        diagnostics: list[Diagnostic] = []

        for name, candidates in sorted(by_name.items()):
            if len(candidates) <= 1:
                continue
            for candidate in candidates:
                diagnostics.append(
                    Diagnostic(
                        candidate.span,
                        f"duplicate function declaration '{name}'",
                        code="nova.duplicate-function",
                        source="nova",
                    )
                )

        for name, span in parsed.calls:
            candidates = by_name.get(name, [])
            if len(candidates) == 1:
                references.append(Reference(span, candidates[0]))
            elif not candidates:
                diagnostics.append(
                    Diagnostic(
                        span,
                        f"unresolved function '{name}'",
                        code="nova.unresolved-function",
                        source="nova",
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        span,
                        f"ambiguous function call '{name}'",
                        code="nova.ambiguous-function",
                        source="nova",
                    )
                )

        semantic = server.semantics.publish(symbols, references)
        server.publish_diagnostics(semantic, diagnostics)
        return semantic


class NovaLanguageServer(LanguageServer):
    """LanguageServer composition that automatically analyzes open Nova documents."""

    def __init__(self, adapter: NovaFunctionAdapter | None = None) -> None:
        super().__init__()
        self.nova_adapter = adapter or NovaFunctionAdapter()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/codeAction"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_nova_code_action(message.get("id"), message.get("params"))

        result = super().handle(message)
        if method == "initialize" and result is not None and "result" in result:
            params = message.get("params")
            if self._client_supports_code_action(params):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["codeActionProvider"] = True
        return result

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

    def _handle_nova_code_action(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            source_range = params.get("range")
            action_context = params.get("context")
            if not isinstance(source_range, dict) or not isinstance(action_context, dict):
                return self._error(request_id, -32602, "Invalid params")
            start = source_range.get("start")
            end = source_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                return self._error(request_id, -32602, "Invalid params")
            only = action_context.get("only")
            if only is not None:
                if not isinstance(only, list) or not all(isinstance(item, str) for item in only):
                    return self._error(request_id, -32602, "Invalid params")
                if not any(item == "quickfix" or item.startswith("quickfix.") for item in only):
                    self.requests.checkpoint(context)
                    return self._result(request_id, [])

            uri = self._document_uri(params)
            assert uri is not None
            document = self.documents.get(uri)
            if document is None or document.language_id != self.nova_adapter.language_id:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
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
            snapshot = self.diagnostics.get(uri)
            if snapshot is None or snapshot.semantic.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            actions = self._nova_code_actions(
                uri, document, source, snapshot.diagnostics, start_offset, end_offset
            )
            self.requests.checkpoint(context)
            try:
                return self.diagnostics.commit_if_current(
                    snapshot, lambda: self._result(request_id, actions)
                )
            except DiagnosticError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _nova_code_actions(
        self,
        uri: str,
        document: Document,
        source: SourceText,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        insertion = Span(len(document.text), len(document.text))
        insertion_range = self._range(source, insertion)
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.unresolved-function":
                continue
            overlaps = (
                diagnostic.span.start <= start_offset <= diagnostic.span.end
                if start_offset == end_offset
                else diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
            )
            if not overlaps:
                continue
            name = document.text[diagnostic.span.start : diagnostic.span.end]
            if re.fullmatch(_IDENTIFIER, name) is None:
                continue
            prefix = "" if not document.text or document.text.endswith("\n") else "\n"
            actions.append(
                {
                    "title": f"Create function '{name}'",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": insertion_range,
                                    "newText": f"{prefix}fn {name}() {{}}\n",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    @staticmethod
    def _client_supports_code_action(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("codeAction"), dict)
