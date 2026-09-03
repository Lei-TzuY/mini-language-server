"""Versioned in-memory document storage for language-independent tooling."""

from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Any

from .source import Position, SourceError, SourceText


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
    """Track open document snapshots with atomic lifecycle and version transitions.

    Readers and writers may run concurrently. Every state transition is serialized so
    checking the current version and publishing its replacement is one atomic action.
    This prevents a slower, lower-version change from overwriting a newer snapshot.
    """

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._lock = RLock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._documents)

    def get(self, uri: str) -> Document | None:
        with self._lock:
            return self._documents.get(uri)

    def open(self, *, uri: str, language_id: str, version: int, text: str) -> Document:
        with self._lock:
            if uri in self._documents:
                raise DocumentError(f"document already open: {uri}")
            document = self._snapshot(uri, language_id, version, text)
            self._documents[uri] = document
            return document

    def replace(self, *, uri: str, version: int, text: str) -> Document:
        with self._lock:
            current = self._require_newer(uri, version)
            if not isinstance(text, str):
                raise DocumentError("document text must be a string")
            document = Document(uri, current.language_id, version, text)
            self._documents[uri] = document
            return document

    def apply_changes(
        self, *, uri: str, version: int, changes: list[dict[str, Any]]
    ) -> Document:
        """Apply LSP content changes sequentially and commit the batch atomically."""
        with self._lock:
            current = self._require_newer(uri, version)
            if not changes:
                raise DocumentError("content changes must not be empty")

            text = current.text
            for change in changes:
                if not isinstance(change, dict) or not isinstance(change.get("text"), str):
                    raise DocumentError("each content change must contain string text")
                replacement = change["text"]
                if "range" not in change:
                    text = replacement
                    continue
                range_ = change["range"]
                if not isinstance(range_, dict):
                    raise DocumentError("change range must be an object")
                try:
                    source = SourceText(text)
                    span = source.span_from_range(
                        self._parse_position(range_.get("start")),
                        self._parse_position(range_.get("end")),
                    )
                except SourceError as exc:
                    raise DocumentError(str(exc)) from exc
                text = text[: span.start] + replacement + text[span.end :]

            document = Document(uri, current.language_id, version, text)
            self._documents[uri] = document
            return document

    def close(self, uri: str) -> Document:
        with self._lock:
            try:
                return self._documents.pop(uri)
            except KeyError as exc:
                raise DocumentError(f"document is not open: {uri}") from exc

    def _require_newer(self, uri: str, version: int) -> Document:
        current = self._documents.get(uri)
        if current is None:
            raise DocumentError(f"document is not open: {uri}")
        if not isinstance(version, int) or isinstance(version, bool):
            raise DocumentError("document version must be an integer")
        if version <= current.version:
            raise DocumentError(
                f"stale document version for {uri}: {version} <= {current.version}"
            )
        return current

    @staticmethod
    def _parse_position(position: Any) -> Position:
        if not isinstance(position, dict):
            raise DocumentError("position must be an object")
        try:
            return Position(position.get("line"), position.get("character"))
        except SourceError as exc:
            raise DocumentError(str(exc)) from exc

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
