"""Deterministic workspace indexing over exact semantic snapshot identities."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock

from .semantic import Reference, SemanticSnapshot
from .symbols import Symbol


class WorkspaceIndexError(RuntimeError):
    """Raised when a workspace index update violates snapshot identity."""


@dataclass(frozen=True, slots=True)
class WorkspaceDeclaration:
    uri: str
    snapshot: SemanticSnapshot
    symbol: Symbol


@dataclass(frozen=True, slots=True)
class WorkspaceReference:
    uri: str
    snapshot: SemanticSnapshot
    reference: Reference


class WorkspaceSymbolIndex:
    """Index current semantic snapshots without mixing superseded generations.

    Contributions are keyed by URI and retain the exact SemanticSnapshot object that
    produced them. Replacements may optionally name the expected previous snapshot,
    providing a compare-and-swap boundary for concurrent analyzers.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, SemanticSnapshot] = {}

    def get(self, uri: str) -> SemanticSnapshot | None:
        with self._lock:
            return self._snapshots.get(uri)

    def replace(
        self,
        snapshot: SemanticSnapshot,
        *,
        expected: SemanticSnapshot | None = None,
    ) -> None:
        uri = snapshot.uri
        with self._lock:
            current = self._snapshots.get(uri)
            if expected is not None and current is not expected:
                raise WorkspaceIndexError("workspace snapshot was replaced")
            self._snapshots[uri] = snapshot

    def remove(
        self, uri: str, *, expected: SemanticSnapshot | None = None
    ) -> SemanticSnapshot | None:
        with self._lock:
            current = self._snapshots.get(uri)
            if expected is not None and current is not expected:
                raise WorkspaceIndexError("workspace snapshot was replaced")
            return self._snapshots.pop(uri, None)

    def declarations(self, name: str) -> tuple[WorkspaceDeclaration, ...]:
        with self._lock:
            items = [
                WorkspaceDeclaration(uri, snapshot, symbol)
                for uri, snapshot in self._snapshots.items()
                for symbol in snapshot.symbols.symbols
                if symbol.name == name
            ]
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.uri,
                    item.symbol.span.start,
                    item.symbol.span.end,
                    item.symbol.kind,
                ),
            )
        )

    def references(self, name: str) -> tuple[WorkspaceReference, ...]:
        with self._lock:
            items = [
                WorkspaceReference(uri, snapshot, reference)
                for uri, snapshot in self._snapshots.items()
                for reference in snapshot.references
                if reference.target.name == name
            ]
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.uri,
                    item.reference.span.start,
                    item.reference.span.end,
                ),
            )
        )
