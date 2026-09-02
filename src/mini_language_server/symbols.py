"""Version-bound symbol indexing for language-independent tooling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .source import Span
from .syntax import SyntaxSnapshot, SyntaxStore


class SymbolError(ValueError):
    """Raised when symbol data cannot be associated with current syntax."""


@dataclass(frozen=True, slots=True)
class Symbol:
    """A language-independent named symbol anchored to a source span."""

    name: str
    kind: str
    span: Span

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise SymbolError("symbol name must be a non-empty string")
        if not isinstance(self.kind, str) or not self.kind:
            raise SymbolError("symbol kind must be a non-empty string")
        if not isinstance(self.span, Span):
            raise SymbolError("symbol span must be a Span")


@dataclass(frozen=True, slots=True)
class SymbolSnapshot:
    """An immutable symbol index derived from one exact syntax snapshot."""

    syntax: SyntaxSnapshot
    symbols: tuple[Symbol, ...]

    @property
    def uri(self) -> str:
        return self.syntax.uri

    @property
    def language_id(self) -> str:
        return self.syntax.language_id

    @property
    def version(self) -> int:
        return self.syntax.version

    def named(self, name: str) -> tuple[Symbol, ...]:
        """Return all symbols with *name* in deterministic source order."""
        return tuple(symbol for symbol in self.symbols if symbol.name == name)


class SymbolIndex:
    """Publish symbol results only for the exact current syntax snapshot.

    Symbol extraction may race with reparsing or document updates. Callers retain the
    :class:`SyntaxSnapshot` used for extraction and publication succeeds only while
    that exact syntax object remains current. This also invalidates symbols when a
    parser republishes syntax for an otherwise unchanged document snapshot.
    """

    def __init__(self, syntax: SyntaxStore) -> None:
        self._syntax = syntax
        self._snapshots: dict[str, SymbolSnapshot] = {}

    def get(self, uri: str) -> SymbolSnapshot | None:
        """Return symbols only when derived from the currently published syntax."""
        syntax = self._syntax.get(uri)
        snapshot = self._snapshots.get(uri)
        if syntax is None or snapshot is None or snapshot.syntax is not syntax:
            return None
        return snapshot

    def publish(
        self, syntax: SyntaxSnapshot, symbols: Iterable[Symbol]
    ) -> SymbolSnapshot:
        """Publish a deterministic symbol snapshot if *syntax* is still current."""
        current = self._syntax.get(syntax.uri)
        if current is not syntax:
            raise SymbolError(
                f"stale symbol result for {syntax.uri} at version {syntax.version}"
            )

        materialized = tuple(symbols)
        text_length = len(syntax.document.text)
        for symbol in materialized:
            if not isinstance(symbol, Symbol):
                raise SymbolError("symbol results must contain Symbol values")
            if symbol.span.end > text_length:
                raise SymbolError(
                    f"symbol span is outside {syntax.uri}: {symbol.span.end} > {text_length}"
                )

        ordered = tuple(
            sorted(
                materialized,
                key=lambda symbol: (
                    symbol.span.start,
                    symbol.span.end,
                    symbol.name,
                    symbol.kind,
                ),
            )
        )
        snapshot = SymbolSnapshot(syntax=syntax, symbols=ordered)
        self._snapshots[syntax.uri] = snapshot
        return snapshot

    def discard(self, uri: str) -> SymbolSnapshot | None:
        """Discard any cached symbol snapshot for *uri*, current or stale."""
        return self._snapshots.pop(uri, None)
