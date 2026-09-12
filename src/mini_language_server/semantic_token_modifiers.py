"""Negotiated Nova reference semantic tokens and binding modifiers."""

from __future__ import annotations

import re
from typing import Any

from .definition_links import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic_tokens import TOKEN_TYPES
from .server import ServerState
from .source import SourceText, Span

_SUPPORTED_MODIFIERS = ("declaration", "readonly", "modification")
_TOKEN_TYPE_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
_LOCAL_KEYWORD = re.compile(r"\b(let|var)\s+\Z")
_ASSIGNMENT_SUFFIX = re.compile(r"\s*=(?!=)")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with exact reference tokens and negotiated modifiers."""

    def __init__(self) -> None:
        super().__init__()
        self._semantic_token_modifiers: tuple[str, ...] = ()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            self._semantic_token_modifiers = self._client_semantic_token_modifiers(
                message.get("params")
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

        source = SourceText(text)
        code = self.nova_adapter.code_view(text)
        decoded = self._decode_semantic_tokens(data)
        by_identity = {
            (line, character, length): index
            for index, (line, character, length, _, _) in enumerate(decoded)
        }

        for symbol in symbols.symbols:
            identity = self._token_identity(source, symbol.span, requested_span)
            if identity is None:
                continue
            index = by_identity.get(identity)
            if index is None:
                continue
            line, character, length, token_type, modifiers = decoded[index]
            names = ["declaration"]
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
