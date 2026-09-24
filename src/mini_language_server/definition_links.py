"""Negotiated exact-snapshot definition LocationLinks for the final Nova product."""

from __future__ import annotations

from typing import Any

from .nova import NovaFunctionSyntax
from .semantic import SemanticError, SemanticSnapshot
from .server import ServerState
from .source import Span
from .will_save_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with negotiated precise definition links."""

    def __init__(self) -> None:
        super().__init__()
        self._definition_link_support = False

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._definition_link_support = self._client_supports_definition_links(
                message.get("params")
            )
            return super().handle(message)

        if (
            method == "textDocument/definition"
            and "id" in message
            and self.state is ServerState.RUNNING
            and self._definition_link_support
        ):
            return self._handle_definition_link(message)

        return super().handle(message)

    @staticmethod
    def _client_supports_definition_links(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        definition = text_document.get("definition")
        return isinstance(definition, dict) and definition.get("linkSupport") is True

    @staticmethod
    def _origin_span(semantics: SemanticSnapshot, offset: int) -> Span | None:
        tree = semantics.symbols.syntax.tree
        if isinstance(tree, NovaFunctionSyntax):
            for _, span in tree.calls:
                if span.start <= offset < span.end:
                    return span
        for reference in semantics.references:
            if reference.span.start <= offset < reference.span.end:
                return reference.span
        for symbol in semantics.symbols.symbols:
            if symbol.span.start <= offset < symbol.span.end:
                return symbol.span
        return None

    def _definition_link(
        self,
        location: dict[str, Any],
        semantics: SemanticSnapshot,
        offset: int,
        workspace_name: str | None,
        workspace_snapshots: tuple[SemanticSnapshot, ...] | None,
    ) -> dict[str, Any] | None:
        target_uri = location.get("uri")
        target_selection = location.get("range")
        if not isinstance(target_uri, str) or not isinstance(target_selection, dict):
            return None

        target_range = target_selection
        if workspace_name is not None and workspace_snapshots is not None:
            declarations = self._nova_function_declarations(
                semantics,
                workspace_name,
                workspace_snapshots,
            )
            if len(declarations) == 1 and declarations[0].uri == target_uri:
                declaration = declarations[0]
                target_source = self._source_text(
                    declaration.snapshot.symbols.syntax.document.text
                )
                target_range = self._range(
                    target_source, self._function_extent(declaration)
                )

        link: dict[str, Any] = {
            "targetUri": target_uri,
            "targetRange": target_range,
            "targetSelectionRange": target_selection,
        }
        origin = self._origin_span(semantics, offset)
        if origin is not None:
            source = self._source_text(semantics.symbols.syntax.document.text)
            link["originSelectionRange"] = self._range(source, origin)
        return link

    def _handle_definition_link(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        params = message.get("params")
        parsed = self._semantic_query(params)
        if parsed is None:
            return super().handle(message)
        semantics, offset, _ = parsed
        if semantics is None:
            return super().handle(message)

        workspace_query = self._workspace_function_query(params)
        workspace_name = workspace_query[1] if workspace_query is not None else None
        snapshots = (
            self.workspace_symbols.snapshots() if workspace_query is not None else None
        )

        response = super().handle(message)
        if response is None or "error" in response or response.get("result") is None:
            return response
        location = response.get("result")
        if not isinstance(location, dict):
            return response
        link = self._definition_link(
            location,
            semantics,
            offset,
            workspace_name,
            snapshots,
        )
        if link is None:
            return response
        transformed = dict(response)
        transformed["result"] = [link]

        try:
            if snapshots is not None:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, lambda: transformed
                )
            return self.semantics.commit_if_current(semantics, lambda: transformed)
        except (WorkspaceIndexError, SemanticError):
            return self._error(request_id, -32801, "Content modified")
