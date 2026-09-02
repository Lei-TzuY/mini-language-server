"""Version-bound semantic queries for language-independent tooling."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .source import Span
from .symbols import Symbol, SymbolIndex, SymbolSnapshot


class SemanticError(ValueError):
    """Raised when semantic data cannot be associated with current symbols."""


@dataclass(frozen=True, slots=True)
class Reference:
    """A source occurrence resolved to one exact indexed symbol."""

    span: Span
    target: Symbol

    def __post_init__(self) -> None:
        if not isinstance(self.span, Span):
            raise SemanticError("reference span must be a Span")
        if not isinstance(self.target, Symbol):
            raise SemanticError("reference target must be a Symbol")


@dataclass(frozen=True, slots=True)
class SemanticSnapshot:
    """Immutable resolved references derived from one exact symbol snapshot."""

    symbols: SymbolSnapshot
    references: tuple[Reference, ...]

    @property
    def uri(self) -> str:
        return self.symbols.uri

    @property
    def language_id(self) -> str:
        return self.symbols.language_id

    @property
    def version(self) -> int:
        return self.symbols.version

    def definition_at(self, offset: int) -> Symbol | None:
        """Return the resolved definition covering *offset*, if any."""
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise SemanticError("offset must be a non-negative integer")
        for reference in self.references:
            if reference.span.start <= offset < reference.span.end:
                return reference.target
        for symbol in self.symbols.symbols:
            if symbol.span.start <= offset < symbol.span.end:
                return symbol
        return None

    def references_to(
        self, target: Symbol, *, include_declaration: bool = False
    ) -> tuple[Span, ...]:
        """Return deterministic source spans resolved to *target*."""
        if not any(target is symbol for symbol in self.symbols.symbols):
            raise SemanticError("target is not part of this symbol snapshot")
        spans = tuple(
            reference.span
            for reference in self.references
            if reference.target is target
        )
        if include_declaration:
            return tuple(sorted((*spans, target.span), key=lambda span: (span.start, span.end)))
        return spans


class SemanticDatabase:
    """Publish semantic results only for the exact current symbol snapshot.

    Resolution may race with reparsing and re-indexing. Publication therefore requires
    the exact :class:`SymbolSnapshot` used for resolution to remain current. Reference
    targets must be object-identical members of that snapshot, preventing structurally
    equal symbols from an older index generation from leaking into current queries.
    """

    def __init__(self, symbols: SymbolIndex) -> None:
        self._symbols = symbols
        self._snapshots: dict[str, SemanticSnapshot] = {}

    def get(self, uri: str) -> SemanticSnapshot | None:
        """Return semantics only when derived from the current symbol snapshot."""
        symbols = self._symbols.get(uri)
        snapshot = self._snapshots.get(uri)
        if symbols is None or snapshot is None or snapshot.symbols is not symbols:
            return None
        return snapshot

    def publish(
        self, symbols: SymbolSnapshot, references: Iterable[Reference]
    ) -> SemanticSnapshot:
        """Publish deterministic references if *symbols* is still current."""
        current = self._symbols.get(symbols.uri)
        if current is not symbols:
            raise SemanticError(
                f"stale semantic result for {symbols.uri} at version {symbols.version}"
            )

        materialized = tuple(references)
        text_length = len(symbols.syntax.document.text)
        members = {id(symbol) for symbol in symbols.symbols}
        for reference in materialized:
            if not isinstance(reference, Reference):
                raise SemanticError("semantic results must contain Reference values")
            if reference.span.end > text_length:
                raise SemanticError(
                    f"reference span is outside {symbols.uri}: "
                    f"{reference.span.end} > {text_length}"
                )
            if id(reference.target) not in members:
                raise SemanticError("reference target is not part of symbol snapshot")

        ordered = tuple(
            sorted(
                materialized,
                key=lambda reference: (
                    reference.span.start,
                    reference.span.end,
                    reference.target.span.start,
                    reference.target.name,
                ),
            )
        )
        previous_end = -1
        for reference in ordered:
            if reference.span.start < previous_end:
                raise SemanticError("reference spans must not overlap")
            previous_end = reference.span.end

        snapshot = SemanticSnapshot(symbols=symbols, references=ordered)
        self._snapshots[symbols.uri] = snapshot
        return snapshot

    def discard(self, uri: str) -> SemanticSnapshot | None:
        """Discard any cached semantic snapshot for *uri*, current or stale."""
        return self._snapshots.pop(uri, None)
