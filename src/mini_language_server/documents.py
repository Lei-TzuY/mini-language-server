"""Versioned in-memory document storage for language-independent tooling."""

from __future__ import annotations

from dataclasses import dataclass


class DocumentError(ValueError):
    """Raised when an invalid document lifecycle or version transition is requested."""


@dataclass(frozen=True, slots=True)
class Document:
    """A single immutable snapshot of an open text document."""

    uri: str
    language_id: str
    version: int
    text: str


class DocumentStore:
    """Track open document snapshots and reject stale version updates."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}

    def __len__(self) -> int:
        return len(self._documents)

    def get(self, uri: str) -> Document | None:
        return self._documents.get(uri)

    def open(self, *, uri: str, language_id: str, version: int, text: str) -> Document:
        if uri in self._documents:
            raise DocumentError(f"document already open: {uri}")
        document = self._snapshot(uri, language_id, version, text)
        self._documents[uri] = document
        return document

    def replace(self, *, uri: str, version: int, text: str) -> Document:
        current = self._documents.get(uri)
        if current is None:
            raise DocumentError(f"document is not open: {uri}")
        if not isinstance(version, int) or isinstance(version, bool):
            raise DocumentError("document version must be an integer")
        if version <= current.version:
            raise DocumentError(
                f"stale document version for {uri}: {version} <= {current.version}"
            )
        if not isinstance(text, str):
            raise DocumentError("document text must be a string")
        document = Document(uri, current.language_id, version, text)
        self._documents[uri] = document
        return document

    def close(self, uri: str) -> Document:
        try:
            return self._documents.pop(uri)
        except KeyError as exc:
            raise DocumentError(f"document is not open: {uri}") from exc

    @staticmethod
    def _snapshot(uri: str, language_id: str, version: int, text: str) -> Document:
        if not isinstance(uri, str) or not uri:
            raise DocumentError("document uri must be a non-empty string")
        if not isinstance(language_id, str):
            raise DocumentError("language id must be a string")
        if not isinstance(version, int) or isinstance(version, bool):
            raise DocumentError("document version must be an integer")
        if not isinstance(text, str):
            raise DocumentError("document text must be a string")
        return Document(uri, language_id, version, text)
