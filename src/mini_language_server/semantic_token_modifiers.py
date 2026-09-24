"""Negotiated Nova reference semantic tokens and binding modifiers."""

from __future__ import annotations

import re
from typing import Any

from .definition_links import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic_tokens import TOKEN_TYPES
from .server import ServerState
from .source import SourceText, Span

_SUPPORTED_MODIFIERS = (
    "declaration",
    "definition",
    "readonly",
    "modification",
    "defaultLibrary",
    "static",
)
_TOKEN_TYPE_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
_LOCAL_KEYWORD = re.compile(r"\b(let|var)\s+\Z")
_ASSIGNMENT_SUFFIX = re.compile(r"\s*=(?!=)")
_INTRINSIC_MEMBER = re.compile(
    r"(?P<type>UInt|Int)\s*::\s*(?P<member>MIN|MAX|from_uint|from)\b"
)
_VALID_INTRINSICS = {
    ("UInt", "MIN"),
    ("UInt", "MAX"),
    ("UInt", "from"),
    ("Int", "from_uint"),
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with exact reference tokens and negotiated modifiers."""

    def __init__(self) -> None:
        super().__init__()
        self._semantic_token_modifiers: tuple[str, ...] = ()
        self._semantic_token_refresh_support = False

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._semantic_token_modifiers = self._client_semantic_token_modifiers(
                params
            )
            self._semantic_token_refresh_support = (
                self._client_supports_semantic_tokens(params)
                and self._client_supports_semantic_token_refresh(params)
            )
            result = super().handle(message)
            if result is not None and "result" in result:
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    provider = capabilities.get("semanticTokensProvider")
                    if isinstance(provider, dict):
                        legend = provider.get("legend")
                        if isinstance(legend, dict):
                            legend["tokenModifiers"] = list(
                                self._semantic_token_modifiers
                            )
            return result
        return super().handle(message)

    def _handle_document_notification(self, method: str, params: Any) -> None:
        uri = self._document_uri(params)
        before = self.workspace_symbols.snapshots()
        super()._handle_document_notification(method, params)
        after = self.workspace_symbols.snapshots()
        if uri is None or self._same_semantic_token_workspace_identity(before, after):
            return
        other_uris = {
            snapshot.uri for snapshot in (*before, *after)
        } - {uri}
        if other_uris:
            self._queue_semantic_token_refresh()

    def _workspace_scope_changed(self, before: Any, after: Any) -> None:
        super()._workspace_scope_changed(before, after)
        self._queue_semantic_token_refresh()

    def _queue_semantic_token_refresh(self) -> None:
        if not self._semantic_token_refresh_support:
            return
        method = "workspace/semanticTokens/refresh"
        if self._has_pending_server_request(method):
            return
        self._queue_server_request(method)

    def _semantic_token_dependency_identity(
        self, uri: str
    ) -> tuple[int, tuple[tuple[str, int], ...]]:
        """Bind token delta publication to the complete exact workspace."""
        snapshots = self.workspace_symbols.snapshots()
        return (
            snapshots.generation,
            tuple((snapshot.uri, id(snapshot)) for snapshot in snapshots),
        )
    @staticmethod
    def _same_semantic_token_workspace_identity(
        left: tuple[Any, ...],
        right: tuple[Any, ...],
    ) -> bool:
        return len(left) == len(right) and all(
            old is new for old, new in zip(left, right, strict=True)
        )

    @staticmethod
    def _client_supports_semantic_tokens(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("semanticTokens"), dict)

    @staticmethod
    def _client_supports_semantic_token_refresh(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        semantic_tokens = workspace.get("semanticTokens")
        return (
            isinstance(semantic_tokens, dict)
            and semantic_tokens.get("refreshSupport") is True
        )

    @staticmethod
    def _client_semantic_token_modifiers(params: Any) -> tuple[str, ...]:
        if not isinstance(params, dict):
            return ()
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return ()
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return ()
        semantic_tokens = text_document.get("semanticTokens")
        if not isinstance(semantic_tokens, dict):
            return ()
        supported = semantic_tokens.get("tokenModifiers")
        if not isinstance(supported, list) or not all(
            isinstance(item, str) for item in supported
        ):
            return ()
        return tuple(item for item in _SUPPORTED_MODIFIERS if item in supported)

    def _modifier_bits(self, *names: str) -> int:
        bits = 0
        for name in names:
            try:
                index = self._semantic_token_modifiers.index(name)
            except ValueError:
                continue
            bits |= 1 << index
        return bits

    def _numeric_intrinsic_semantic_tokens(
        self,
        symbols,
        text: str,
        *,
        requested_span: Span | None = None,
    ) -> list[int]:
        data = super()._numeric_intrinsic_semantic_tokens(
            symbols, text, requested_span=requested_span
        )
        semantics = self.semantics.get(symbols.uri)
        if semantics is None or semantics.symbols is not symbols:
            return data

        source = self._source_text(text)
        code = self.nova_adapter.code_view(text)
        decoded = self._decode_semantic_tokens(data)
        by_identity = {
            (line, character, length): index
            for index, (line, character, length, _, _) in enumerate(decoded)
        }

        default_library = self._modifier_bits("defaultLibrary")
        static_member = self._modifier_bits("static")
        if default_library or static_member:
            for match in _INTRINSIC_MEMBER.finditer(code):
                if (match.group("type"), match.group("member")) not in _VALID_INTRINSICS:
                    continue
                for group in ("type", "member"):
                    identity = self._token_identity(
                        source,
                        Span(match.start(group), match.end(group)),
                        requested_span,
                    )
                    if identity is None:
                        continue
                    index = by_identity.get(identity)
                    if index is None:
                        continue
                    line, character, length, token_type, modifiers = decoded[index]
                    extra_modifiers = default_library
                    if group == "member":
                        extra_modifiers |= static_member
                    decoded[index] = (
                        line,
                        character,
                        length,
                        token_type,
                        modifiers | extra_modifiers,
                    )

        for symbol in symbols.symbols:
            identity = self._token_identity(source, symbol.span, requested_span)
            if identity is None:
                continue
            index = by_identity.get(identity)
            if index is None:
                continue
            line, character, length, token_type, modifiers = decoded[index]
            names = ["declaration"]
            if symbol.kind == "function":
                names.append("definition")
            if symbol.kind == "variable" and self._is_immutable_local(code, symbol.span):
                names.append("readonly")
            decoded[index] = (
                line,
                character,
                length,
                token_type,
                modifiers | self._modifier_bits(*names),
            )

        for reference in semantics.references:
            identity = self._token_identity(source, reference.span, requested_span)
            if identity is None or identity in by_identity:
                continue
            token_type = _TOKEN_TYPE_INDEX.get(reference.target.kind.lower())
            if token_type is None:
                continue
            modifiers = 0
            if reference.target.kind == "variable" and self._is_immutable_local(
                code, reference.target.span
            ):
                modifiers |= self._modifier_bits("readonly")
            if self._is_assignment_target(code, reference.span):
                modifiers |= self._modifier_bits("modification")
            decoded.append((*identity, token_type, modifiers))
            by_identity[identity] = len(decoded) - 1

        parsed = self.nova_adapter.parse(text)
        function_token_type = _TOKEN_TYPE_INDEX["function"]
        for name, span in parsed.calls:
            identity = self._token_identity(source, span, requested_span)
            if identity is None or identity in by_identity:
                continue
            declarations = self._nova_visible_function_declarations(
                semantics,
                self.workspace_symbols.snapshots(),
                name,
            )
            if len(declarations) != 1:
                continue
            decoded.append((*identity, function_token_type, 0))
            by_identity[identity] = len(decoded) - 1

        decoded.sort(key=lambda token: (token[0], token[1], token[2], token[3]))
        return self._encode_absolute_semantic_tokens(decoded)

    @staticmethod
    def _token_identity(
        source: SourceText, span: Span, requested_span: Span | None
    ) -> tuple[int, int, int] | None:
        if requested_span is not None and (
            span.end <= requested_span.start or span.start >= requested_span.end
        ):
            return None
        start, end = source.range_from_span(span)
        if start.line != end.line or end.character <= start.character:
            return None
        return start.line, start.character, end.character - start.character

    @staticmethod
    def _is_immutable_local(code: str, span: Span) -> bool:
        prefix = code[: span.start]
        match = _LOCAL_KEYWORD.search(prefix)
        return match is not None and match.group(1) == "let"

    @staticmethod
    def _is_assignment_target(code: str, span: Span) -> bool:
        return _ASSIGNMENT_SUFFIX.match(code, span.end) is not None
