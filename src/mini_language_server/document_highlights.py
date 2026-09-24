"""Exact-snapshot LSP document-highlight capability."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .document_symbols import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticError
from .server import ServerState
from .workspace import WorkspaceIndexError

_DOCUMENT_HIGHLIGHT_READ = 2
_DOCUMENT_HIGHLIGHT_WRITE = 3


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Product server extended with generic exact-snapshot document highlights."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/documentHighlight"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_document_highlight(
                message.get("id"), message.get("params")
            )

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_document_highlights(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["documentHighlightProvider"] = True
        return result

    @staticmethod
    def _client_supports_document_highlights(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("documentHighlight"), dict)

    def _handle_document_highlight(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None:
            return self._error(request_id, -32602, "Invalid params")

        try:
            parsed = self._semantic_query(params)
            if parsed is None:
                return self._error(request_id, -32602, "Invalid params")

            self.requests.checkpoint(context)
            semantics, offset, source = parsed
            if semantics is None:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            target = semantics.definition_at(offset)
            self.requests.checkpoint(context)
            if target is None:
                snapshots = self.workspace_symbols.snapshots()
                namespace_target = self._nova_import_namespace_target(
                    semantics,
                    snapshots,
                    offset,
                )
                if namespace_target is None:
                    return self._current_semantic_result(semantics, request_id, [])
                imported, _ = namespace_target
                spans = self._nova_import_namespace_spans(
                    semantics,
                    imported,
                )
                highlights = [
                    {
                        "range": self._range(source, span),
                        "kind": (
                            _DOCUMENT_HIGHLIGHT_WRITE
                            if imported.span == span
                            else _DOCUMENT_HIGHLIGHT_READ
                        ),
                    }
                    for span in spans
                ]
                self.requests.checkpoint(context)
                try:
                    return self.semantics.commit_if_current(
                        semantics,
                        lambda: self.workspace_symbols.commit_snapshots_if_current(
                            snapshots,
                            lambda: self._result(request_id, highlights),
                        ),
                    )
                except (SemanticError, WorkspaceIndexError):
                    return self._error(request_id, -32801, "Content modified")

            spans = semantics.references_to(target, include_declaration=True)
            highlights = [
                {
                    "range": self._range(source, span),
                    "kind": (
                        _DOCUMENT_HIGHLIGHT_WRITE
                        if span == target.span
                        else _DOCUMENT_HIGHLIGHT_READ
                    ),
                }
                for span in spans
            ]
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, highlights)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)
