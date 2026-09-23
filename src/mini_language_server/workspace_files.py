"""Immutable watched-workspace file snapshots with deterministic local scanning."""

from __future__ import annotations

import os
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import TypeVar


class WorkspaceFileError(RuntimeError):
    """Raised when a watched-workspace file snapshot becomes stale."""


@dataclass(frozen=True, slots=True)
class WorkspaceFileSnapshot:
    """One immutable UTF-8 workspace file snapshot."""

    uri: str
    text: str


_T = TypeVar("_T")


class WorkspaceFileStore:
    """Track exact local file snapshots independently from open LSP documents."""

    def __init__(self) -> None:
        self._snapshots: dict[str, WorkspaceFileSnapshot] = {}
        self._generation = 0
        self._lock = RLock()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def get(self, uri: str) -> WorkspaceFileSnapshot | None:
        with self._lock:
            return self._snapshots.get(uri)

    def snapshots(self) -> tuple[WorkspaceFileSnapshot, ...]:
        with self._lock:
            return tuple(self._snapshots[uri] for uri in sorted(self._snapshots))

    def load(self, uri: str) -> WorkspaceFileSnapshot | None:
        """Read one local UTF-8 file, replacing its snapshot only when content changes."""
        path = self.path_from_file_uri(uri)
        if path is None:
            return self.remove(uri)

        try:
            if not path.is_file() or path.is_symlink():
                return self.remove(uri)
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return self.remove(uri)

        canonical_uri = path.resolve().as_uri()
        if canonical_uri != uri:
            return None

        with self._lock:
            current = self._snapshots.get(uri)
            if current is not None and current.text == text:
                return current
            snapshot = WorkspaceFileSnapshot(uri=uri, text=text)
            self._snapshots[uri] = snapshot
            self._generation += 1
            return snapshot

    def remove(self, uri: str) -> WorkspaceFileSnapshot | None:
        with self._lock:
            removed = self._snapshots.pop(uri, None)
            if removed is not None:
                self._generation += 1
            return removed

    def reconcile(
        self,
        folder_uris: Iterable[str],
        *,
        suffix: str,
    ) -> tuple[WorkspaceFileSnapshot, ...]:
        """Scan all local roots deterministically and remove snapshots no longer present."""
        if not isinstance(suffix, str) or not suffix:
            raise WorkspaceFileError("workspace file suffix must be non-empty")

        discovered: set[str] = set()
        for folder_uri in sorted(set(folder_uris)):
            root = self.path_from_file_uri(folder_uri)
            if root is None:
                continue
            try:
                if not root.is_dir():
                    continue
            except OSError:
                continue

            for directory, directories, files in os.walk(root, followlinks=False):
                directory_path = Path(directory)
                directories[:] = sorted(
                    name
                    for name in directories
                    if not (directory_path / name).is_symlink()
                )
                for name in sorted(files):
                    if not name.endswith(suffix):
                        continue
                    path = directory_path / name
                    try:
                        if path.is_symlink() or not path.is_file():
                            continue
                        uri = path.resolve().as_uri()
                    except OSError:
                        continue
                    discovered.add(uri)
                    self.load(uri)

        with self._lock:
            stale = tuple(uri for uri in self._snapshots if uri not in discovered)
        for uri in stale:
            self.remove(uri)
        return self.snapshots()

    def commit_if_current(
        self,
        snapshot: WorkspaceFileSnapshot,
        callback: Callable[[], _T],
    ) -> _T:
        """Publish derived work only while the exact file snapshot remains current."""
        if not isinstance(snapshot, WorkspaceFileSnapshot):
            raise WorkspaceFileError(
                "workspace file commit requires a WorkspaceFileSnapshot"
            )
        if not callable(callback):
            raise WorkspaceFileError("workspace file commit must be callable")
        with self._lock:
            if self._snapshots.get(snapshot.uri) is not snapshot:
                raise WorkspaceFileError("workspace file snapshot was replaced")
            return callback()

    @staticmethod
    def path_from_file_uri(uri: str) -> Path | None:
        """Convert one canonical local file URI to a platform path."""
        if not isinstance(uri, str) or not uri:
            return None
        try:
            parsed = urllib.parse.urlsplit(uri)
        except ValueError:
            return None
        if parsed.scheme != "file" or parsed.query or parsed.fragment:
            return None
        if parsed.netloc not in {"", "localhost"}:
            return None

        path_text = urllib.request.url2pathname(
            urllib.parse.unquote(parsed.path)
        )
        if os.name == "nt" and path_text.startswith("/") and len(path_text) >= 3:
            if path_text[2] == ":":
                path_text = path_text[1:]
        try:
            return Path(path_text)
        except (TypeError, ValueError):
            return None
