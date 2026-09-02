"""Versioned in-memory document storage for language-independent tooling."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
        current = self._require_newer(uri, version)
        if not isinstance(text, str):
            raise DocumentError("document text must be a string")
        document = Document(uri, current.language_id, version, text)
        self._documents[uri] = document
        return document

    def apply_changes(
        self, *, uri: str, version: int, changes: list[dict[str, Any]]
    ) -> Document:
        """Apply LSP content changes sequentially, committing only if all are valid."""
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
            start = self._position_to_offset(text, range_.get("start"))
            end = self._position_to_offset(text, range_.get("end"))
            if start > end:
                raise DocumentError("change range start is after end")
            text = text[:start] + replacement + text[end:]

        document = Document(uri, current.language_id, version, text)
        self._documents[uri] = document
        return document

    def close(self, uri: str) -> Document:
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
    def _position_to_offset(text: str, position: Any) -> int:
        if not isinstance(position, dict):
            raise DocumentError("position must be an object")
        line = position.get("line")
        character = position.get("character")
        if (
            not isinstance(line, int)
            or isinstance(line, bool)
            or line < 0
            or not isinstance(character, int)
            or isinstance(character, bool)
            or character < 0
        ):
            raise DocumentError("position line and character must be non-negative integers")

        lines = text.splitlines(keepends=True)
        if line >= len(lines):
            if line == 0 and not lines:
                line_text = ""
                line_start = 0
            elif line == len(lines) and text.endswith(("\n", "\r")):
                line_text = ""
                line_start = len(text)
            else:
                raise DocumentError("position line is outside the document")
        else:
            line_start = sum(len(part) for part in lines[:line])
            line_text = lines[line].rstrip("\r\n")

        utf16_units = 0
        for index, char in enumerate(line_text):
            if utf16_units == character:
                return line_start + index
            units = 2 if ord(char) > 0xFFFF else 1
            if utf16_units < character < utf16_units + units:
                raise DocumentError("position splits a UTF-16 surrogate pair")
            utf16_units += units
        if utf16_units == character:
            return line_start + len(line_text)
        raise DocumentError("position character is outside the line")

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
