"""Deterministic workspace indexing over exact semantic snapshot identities."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import TypeVar

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


_T = TypeVar("_T")


class WorkspaceSnapshotSet(tuple):
    """Tuple-compatible exact workspace capture with one mutation generation."""

    generation: int

    def __new__(
        cls,
        snapshots: tuple[SemanticSnapshot, ...],
        generation: int,
    ):
        value = super().__new__(cls, snapshots)
        value.generation = generation
        return value


class WorkspaceSymbolIndex:
    """Index current semantic snapshots without mixing superseded generations.

    Contributions are keyed by URI and retain the exact SemanticSnapshot object that
    produced them. Replacements may optionally name the expected previous snapshot,
    providing a compare-and-swap boundary for concurrent analyzers.
    """

    def __init__(self) -> None:
        self._lock = RLock()
        self._snapshots: dict[str, SemanticSnapshot] = {}
        self._generation = 0

    def get(self, uri: str) -> SemanticSnapshot | None:
        with self._lock:
            return self._snapshots.get(uri)

    def invalidate_complete_queries(self) -> None:
        """Advance complete-workspace identity without replacing snapshots.

        Some workspace semantics depend on state outside individual semantic snapshots,
        such as the active workspace-folder topology. Callers that captured a complete
        WorkspaceSnapshotSet must become stale across those transitions even when every
        indexed snapshot object remains current. Deliberately partial subset commits
        continue to validate only their named snapshot identities.
        """
        with self._lock:
            self._generation += 1

    def snapshots(self) -> WorkspaceSnapshotSet:
        """Return exact indexed snapshots plus the current workspace generation."""
        with self._lock:
            snapshots = tuple(
                self._snapshots[uri] for uri in sorted(self._snapshots)
            )
            return WorkspaceSnapshotSet(snapshots, self._generation)

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
            if current is snapshot:
                return
            self._snapshots[uri] = snapshot
            self._generation += 1

    def remove(
        self, uri: str, *, expected: SemanticSnapshot | None = None
    ) -> SemanticSnapshot | None:
        with self._lock:
            current = self._snapshots.get(uri)
            if expected is not None and current is not expected:
                raise WorkspaceIndexError("workspace snapshot was replaced")
            removed = self._snapshots.pop(uri, None)
            if removed is not None:
                self._generation += 1
            return removed

    def declarations(self, name: str) -> tuple[WorkspaceDeclaration, ...]:
        with self._lock:
            items = [
                WorkspaceDeclaration(uri, snapshot, symbol)
                for uri, snapshot in self._snapshots.items()
                for symbol in snapshot.symbols.symbols
                if symbol.name == name
            ]
        return self._sort_declarations(items)

    def search(self, query: str) -> tuple[WorkspaceDeclaration, ...]:
        """Return deterministic case-insensitive declaration matches.

        The empty query intentionally returns every indexed declaration, matching the
        LSP workspace-symbol convention used by clients for broad symbol palettes.
        """
        needle = query.casefold()
        with self._lock:
            items = [
                WorkspaceDeclaration(uri, snapshot, symbol)
                for uri, snapshot in self._snapshots.items()
                for symbol in snapshot.symbols.symbols
                if needle in symbol.name.casefold()
            ]
        return self._sort_declarations(items)

    def commit_if_current(
        self,
        declarations: tuple[WorkspaceDeclaration, ...],
        callback: Callable[[], _T],
    ) -> _T:
        """Publish a derived workspace result only while every parent is exact-current."""
        return self._commit_subset_if_current(
            tuple(declaration.snapshot for declaration in declarations), callback
        )

    def _commit_subset_if_current(
        self,
        snapshots: tuple[SemanticSnapshot, ...],
        callback: Callable[[], _T],
    ) -> _T:
        """Guard a deliberately partial workspace query by exact parent identities."""
        with self._lock:
            for snapshot in snapshots:
                if self._snapshots.get(snapshot.uri) is not snapshot:
                    raise WorkspaceIndexError("workspace snapshot was replaced")
            return callback()

    def commit_snapshots_if_current(
        self,
        snapshots: tuple[SemanticSnapshot, ...],
        callback: Callable[[], _T],
    ) -> _T:
        """Publish only while the complete exact semantic snapshot set is unchanged."""
        captured_generation = (
            snapshots.generation
            if isinstance(snapshots, WorkspaceSnapshotSet)
            else None
        )
        expected_by_uri: dict[str, SemanticSnapshot] = {}
        for snapshot in snapshots:
            previous = expected_by_uri.get(snapshot.uri)
            if previous is not None and previous is not snapshot:
                raise WorkspaceIndexError("workspace snapshot set has conflicting URIs")
            expected_by_uri[snapshot.uri] = snapshot

        with self._lock:
            if set(self._snapshots) != set(expected_by_uri):
                raise WorkspaceIndexError("workspace snapshot set changed")
            for uri, snapshot in expected_by_uri.items():
                if self._snapshots.get(uri) is not snapshot:
                    raise WorkspaceIndexError("workspace snapshot was replaced")
            if (
                captured_generation is not None
                and captured_generation != self._generation
            ):
                raise WorkspaceIndexError("workspace snapshot generation changed")
            return callback()

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

    @staticmethod
    def _sort_declarations(
        items: list[WorkspaceDeclaration],
    ) -> tuple[WorkspaceDeclaration, ...]:
        return tuple(
            sorted(
                items,
                key=lambda item: (
                    item.uri,
                    item.symbol.span.start,
                    item.symbol.span.end,
                    item.symbol.kind,
                    item.symbol.name,
                ),
            )
        )
