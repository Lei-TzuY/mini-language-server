"""Version-bound syntax snapshots for language-independent tooling."""

from __future__ import annotations

from dataclasses import dataclass

from .documents import Document, DocumentStore


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


class SyntaxStore:
    """Publish syntax results only for the exact document snapshot that produced them.

    Parsing may happen outside this object and may race with document updates. A caller
    must retain the :class:`Document` it parsed and publish against that exact object.
    If the document changed or closed while parsing, publication is rejected so stale
    results never replace syntax for the current document.
    """

    def __init__(self, documents: DocumentStore) -> None:
        self._documents = documents
        self._snapshots: dict[str, SyntaxSnapshot] = {}

    def get(self, uri: str) -> SyntaxSnapshot | None:
        """Return syntax only when it belongs to the currently open document snapshot."""
        document = self._documents.get(uri)
        snapshot = self._snapshots.get(uri)
        if document is None or snapshot is None or snapshot.document is not document:
            return None
        return snapshot

    def publish(self, document: Document, tree: object) -> SyntaxSnapshot:
        """Publish a syntax tree if *document* is still the current open snapshot."""
        current = self._documents.get(document.uri)
        if current is not document:
            raise SyntaxError(
                f"stale syntax result for {document.uri} at version {document.version}"
            )
        snapshot = SyntaxSnapshot(document=document, tree=tree)
        self._snapshots[document.uri] = snapshot
        return snapshot

    def discard(self, uri: str) -> SyntaxSnapshot | None:
        """Discard any cached syntax for *uri*, whether current or stale."""
        return self._snapshots.pop(uri, None)
