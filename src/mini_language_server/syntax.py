"""Version-bound syntax snapshots for language-independent tooling."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import TypeVar

from .documents import Document, DocumentError, DocumentStore


class SyntaxError(ValueError):
    """Raised when a syntax result cannot be associated with a live document."""


@dataclass(frozen=True, slots=True)
class SyntaxSnapshot:
    """An immutable association between one document snapshot and a syntax tree."""

    document: Document
    tree: object

    @property
    def uri(self) -> str:
        return self.document.uri

    @property
    def language_id(self) -> str:
        return self.document.language_id

    @property
    def version(self) -> int:
        return self.document.version


_T = TypeVar("_T")


class SyntaxStore:
    """Publish syntax results only for the exact document snapshot that produced them.

    Parsing may happen outside this object and may race with document updates. A caller
    must retain the :class:`Document` it parsed and publish against that exact object.
    Publication uses the document store's compare-and-commit boundary so a document
    transition cannot slip between the identity check and the derived-cache write.
    Derived caches can use :meth:`commit_if_current` to extend the same guarantee to
    results bound to one exact syntax snapshot.
    """

    def __init__(self, documents: DocumentStore) -> None:
        self._documents = documents
        self._snapshots: dict[str, SyntaxSnapshot] = {}
        self._lock = RLock()

    def get(self, uri: str) -> SyntaxSnapshot | None:
        """Return syntax only when it belongs to the currently open document snapshot."""
        document = self._documents.get(uri)
        with self._lock:
            snapshot = self._snapshots.get(uri)
            if document is None or snapshot is None or snapshot.document is not document:
                return None
            return snapshot

    def commit_if_current(
        self, syntax: SyntaxSnapshot, commit: Callable[[], _T]
    ) -> _T:
        """Run *commit* atomically while *syntax* remains the current snapshot."""
        if not isinstance(syntax, SyntaxSnapshot):
            raise SyntaxError("current snapshot guard requires a SyntaxSnapshot")
        if not callable(commit):
            raise SyntaxError("snapshot commit must be callable")

        def guarded_commit() -> _T:
            with self._lock:
                current = self._snapshots.get(syntax.uri)
                if current is not syntax:
                    raise SyntaxError(
                        f"stale syntax snapshot for {syntax.uri} at version {syntax.version}"
                    )
                return commit()

        try:
            return self._documents.commit_if_current(syntax.document, guarded_commit)
        except DocumentError as exc:
            raise SyntaxError(
                f"stale syntax snapshot for {syntax.uri} at version {syntax.version}"
            ) from exc

    def publish(self, document: Document, tree: object) -> SyntaxSnapshot:
        """Atomically publish a syntax tree if *document* is still current."""
        snapshot = SyntaxSnapshot(document=document, tree=tree)

        def commit() -> SyntaxSnapshot:
            with self._lock:
                self._snapshots[document.uri] = snapshot
            return snapshot

        try:
            return self._documents.commit_if_current(document, commit)
        except DocumentError as exc:
            raise SyntaxError(
                f"stale syntax result for {document.uri} at version {document.version}"
            ) from exc

    def discard(self, uri: str) -> SyntaxSnapshot | None:
        """Discard any cached syntax for *uri*, whether current or stale."""
        with self._lock:
            return self._snapshots.pop(uri, None)
