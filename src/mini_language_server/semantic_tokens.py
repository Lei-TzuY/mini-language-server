"""Language-independent LSP semantic token encoding from symbol snapshots."""

from __future__ import annotations

from .source import SourceText
from .symbols import SymbolSnapshot

TOKEN_TYPES: tuple[str, ...] = (
    "namespace",
    "type",
    "class",
    "enum",
    "interface",
    "struct",
    "typeParameter",
    "parameter",
    "variable",
    "property",
    "enumMember",
    "event",
    "function",
    "method",
    "macro",
    "label",
)

_TOKEN_TYPE_INDEX = {name: index for index, name in enumerate(TOKEN_TYPES)}
_KIND_ALIASES = {
    "module": "namespace",
    "namespace": "namespace",
    "type": "type",
    "class": "class",
    "enum": "enum",
    "interface": "interface",
    "struct": "struct",
    "type_parameter": "typeParameter",
    "type-parameter": "typeParameter",
    "parameter": "parameter",
    "variable": "variable",
    "constant": "variable",
    "field": "property",
    "property": "property",
    "enum_member": "enumMember",
    "enum-member": "enumMember",
    "event": "event",
    "function": "function",
    "method": "method",
    "macro": "macro",
    "label": "label",
}


def encode_semantic_tokens(symbols: SymbolSnapshot) -> list[int]:
    """Encode deterministic single-line symbol tokens using LSP delta encoding.

    Symbols with multi-line or empty spans are omitted because LSP clients do not
    universally support multi-line semantic tokens. Unknown symbol kinds fall back
    to ``variable`` rather than leaking language-specific kinds into the protocol.
    """
    source = SourceText(symbols.syntax.document.text)
    encoded: list[int] = []
    previous_line = 0
    previous_character = 0

    for symbol in symbols.symbols:
        start, end = source.range_from_span(symbol.span)
        if start.line != end.line or end.character <= start.character:
            continue
        token_type = _KIND_ALIASES.get(symbol.kind.lower(), "variable")
        token_type_index = _TOKEN_TYPE_INDEX[token_type]
        delta_line = start.line - previous_line
        delta_start = (
            start.character - previous_character if delta_line == 0 else start.character
        )
        encoded.extend(
            [delta_line, delta_start, end.character - start.character, token_type_index, 0]
        )
        previous_line = start.line
        previous_character = start.character

    return encoded
