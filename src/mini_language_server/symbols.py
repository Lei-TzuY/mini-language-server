"""Version-bound symbol indexing for language-independent tooling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from threading import RLock

from .source import Span
from .syntax import SyntaxError, SyntaxSnapshot, SyntaxStore


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
    that exact syntax object remains current. Publication uses the syntax store's
    compare-and-commit boundary so a syntax transition cannot slip between the
    identity check and the symbol-cache write.
    """

    def __init__(self, syntax: SyntaxStore) -> None:
        self._syntax = syntax
        self._snapshots: dict[str, SymbolSnapshot] = {}
        self._lock = RLock()

    def get(self, uri: str) -> SymbolSnapshot | None:
        """Return symbols only when derived from the currently published syntax."""
        syntax = self._syntax.get(uri)
        with self._lock:
            snapshot = self._snapshots.get(uri)
            if syntax is None or snapshot is None or snapshot.syntax is not syntax:
                return None
            return snapshot

    def publish(
        self, syntax: SyntaxSnapshot, symbols: Iterable[Symbol]
    ) -> SymbolSnapshot:
        """Atomically publish a deterministic symbol snapshot if *syntax* is current."""
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

        def commit() -> SymbolSnapshot:
            with self._lock:
                self._snapshots[syntax.uri] = snapshot
            return snapshot

        try:
            return self._syntax.commit_if_current(syntax, commit)
        except SyntaxError as exc:
            raise SymbolError(
                f"stale symbol result for {syntax.uri} at version {syntax.version}"
            ) from exc

    def discard(self, uri: str) -> SymbolSnapshot | None:
        """Discard any cached symbol snapshot for *uri*, current or stale."""
        with self._lock:
            return self._snapshots.pop(uri, None)
