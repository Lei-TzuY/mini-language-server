"""Trivia-aware Nova lexical masking for the final product adapter."""

from __future__ import annotations

from .nova import NovaFunctionAdapter, NovaFunctionSyntax
from .selection_ranges import NovaProductLanguageServer as _NovaProductLanguageServer


class LexicalNovaFunctionAdapter(NovaFunctionAdapter):
    """Ignore comments and quoted strings while preserving exact source offsets.

    The bounded Nova parser is intentionally regex-backed, so it needs an explicit
    lexical view before structural scans. Trivia is replaced with spaces while
    newlines and total length are preserved, keeping every published span anchored to
    the exact original document snapshot.
    """

    @staticmethod
    def code_view(text: str) -> str:
        chars = list(text)
        index = 0
        length = len(text)

        def mask(start: int, end: int) -> None:
            for offset in range(start, end):
                if chars[offset] not in {"\n", "\r"}:
                    chars[offset] = " "

        while index < length:
            if text.startswith("//", index):
                end = text.find("\n", index + 2)
                if end < 0:
                    end = length
                mask(index, end)
                index = end
                continue

            if text.startswith("/*", index):
                closing = text.find("*/", index + 2)
                end = length if closing < 0 else closing + 2
                mask(index, end)
                index = end
                continue

            quote = text[index]
            if quote in {'"', "'"}:
                start = index
                index += 1
                escaped = False
                while index < length:
                    character = text[index]
                    if escaped:
                        escaped = False
                    elif character == "\\":
                        escaped = True
                    elif character == quote:
                        index += 1
                        break
                    index += 1
                mask(start, index)
                continue

            index += 1

        return "".join(chars)

    @classmethod
    def parse(cls, text: str) -> NovaFunctionSyntax:
        """Parse only executable code while retaining offsets into *text*."""
        return NovaFunctionAdapter.parse(cls.code_view(text))

    @staticmethod
    def _matching_brace(text: str, opening: int) -> int | None:
        """Match structural braces without letting trivia terminate a function body."""
        return NovaFunctionAdapter._matching_brace(
            LexicalNovaFunctionAdapter.code_view(text), opening
        )


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with trivia-aware lexical semantics."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = LexicalNovaFunctionAdapter()
