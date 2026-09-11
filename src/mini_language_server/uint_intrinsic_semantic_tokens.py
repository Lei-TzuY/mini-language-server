"""Exact-snapshot semantic tokens for implemented Nova numeric intrinsics."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .semantic_tokens import TOKEN_TYPES, encode_semantic_tokens
from .source import SourceText, Span
from .uint_intrinsic_signature_help import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .workspace import WorkspaceIndexError

_INTRINSIC_MEMBER = re.compile(
    r"(?P<type>UInt|Int)\s*::\s*(?P<member>MIN|MAX|from_uint|from)\b"
)
_VALID_INTRINSICS = {
    ("UInt", "MIN"): "enumMember",
    ("UInt", "MAX"): "enumMember",
    ("UInt", "from"): "method",
    ("Int", "from_uint"): "method",
}
_TOKEN_TYPE_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with semantic tokens for implemented numeric intrinsics."""

    def _handle_semantic_tokens_request(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any]:
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

            requested_span = None
            if method == "textDocument/semanticTokens/range":
                requested_span = self._semantic_tokens_range(params, document.text)
                if requested_span is None:
                    return self._error(request_id, -32602, "Invalid params")

            semantics = self.semantics.get(uri)
            if semantics is None or semantics.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, {"data": []})

            snapshots = self.workspace_symbols.snapshots()
            data = self._numeric_intrinsic_semantic_tokens(
                semantics.symbols,
                document.text,
                requested_span=requested_span,
            )
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._current_semantic_result(
                        semantics, request_id, {"data": data}
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

    def _numeric_intrinsic_semantic_tokens(
        self,
        symbols,
        text: str,
        *,
        requested_span: Span | None = None,
    ) -> list[int]:
        source = SourceText(text)
        data = encode_semantic_tokens(symbols, requested_span=requested_span)
        decoded = self._decode_semantic_tokens(data)
        occupied = {(line, character, length) for line, character, length, _, _ in decoded}

        code = self.nova_adapter.code_view(text)
        for match in _INTRINSIC_MEMBER.finditer(code):
            key = (match.group("type"), match.group("member"))
            token_type = _VALID_INTRINSICS.get(key)
            if token_type is None:
                continue
            for group, kind in (("type", "type"), ("member", token_type)):
                span = Span(match.start(group), match.end(group))
                if requested_span is not None and (
                    span.end <= requested_span.start or span.start >= requested_span.end
                ):
                    continue
                start, end = source.range_from_span(span)
                if start.line != end.line or end.character <= start.character:
                    continue
                token = (
                    start.line,
                    start.character,
                    end.character - start.character,
                    _TOKEN_TYPE_INDEX[kind],
                    0,
                )
                identity = token[:3]
                if identity in occupied:
                    continue
                occupied.add(identity)
                decoded.append(token)

        decoded.sort(key=lambda token: (token[0], token[1], token[2], token[3]))
        return self._encode_absolute_semantic_tokens(decoded)

    @staticmethod
    def _decode_semantic_tokens(
        data: list[int],
    ) -> list[tuple[int, int, int, int, int]]:
        decoded: list[tuple[int, int, int, int, int]] = []
        line = 0
        character = 0
        for index in range(0, len(data), 5):
            delta_line, delta_start, length, token_type, modifiers = data[index : index + 5]
            line += delta_line
            character = character + delta_start if delta_line == 0 else delta_start
            decoded.append((line, character, length, token_type, modifiers))
        return decoded

    @staticmethod
    def _encode_absolute_semantic_tokens(
        tokens: list[tuple[int, int, int, int, int]],
    ) -> list[int]:
        encoded: list[int] = []
        previous_line = 0
        previous_character = 0
        for line, character, length, token_type, modifiers in tokens:
            delta_line = line - previous_line
            delta_start = character - previous_character if delta_line == 0 else character
            encoded.extend([delta_line, delta_start, length, token_type, modifiers])
            previous_line = line
            previous_character = character
        return encoded
