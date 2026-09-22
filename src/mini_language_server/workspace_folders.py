"""Language-independent LSP workspace-folder scope tracking."""

from __future__ import annotations

import collections.abc
import dataclasses
import threading
import typing
import urllib.parse


_T = typing.TypeVar("_T")


class WorkspaceFolderError(ValueError):
    """Raised when workspace-folder lifecycle data is malformed."""


@dataclasses.dataclass(frozen=True, slots=True)
class WorkspaceFolder:
    uri: str
    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.uri, str) or not self.uri:
            raise WorkspaceFolderError("workspace folder URI must be a non-empty string")
        if not isinstance(self.name, str):
            raise WorkspaceFolderError("workspace folder name must be a string")


class WorkspaceFolderSet:
    """Track one session workspace-folder scope.

    None means legacy/unscoped mode: every open document may participate in
    workspace-wide tooling. An explicit folder mapping, including an empty one,
    means workspace-folder scoping is active.
    """

    def __init__(self) -> None:
        self._folders: dict[str, WorkspaceFolder] | None = None
        self._generation = 0
        self._lock = threading.RLock()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def scoped(self) -> bool:
        with self._lock:
            return self._folders is not None

    def folders(self) -> tuple[WorkspaceFolder, ...]:
        with self._lock:
            if self._folders is None:
                return ()
            return tuple(self._folders[uri] for uri in sorted(self._folders))

    def configure(self, params: typing.Any) -> None:
        """Initialize scope from workspaceFolders with rootUri fallback."""
        if not isinstance(params, dict):
            return

        workspace_folders = params.get("workspaceFolders")
        if isinstance(workspace_folders, list):
            parsed = self._parse_folders(workspace_folders)
            self._replace(parsed)
            return
        if workspace_folders is not None:
            raise WorkspaceFolderError("workspaceFolders must be an array or null")

        root_uri = params.get("rootUri")
        if root_uri is None:
            return
        if not isinstance(root_uri, str) or not root_uri:
            raise WorkspaceFolderError("rootUri must be a non-empty string or null")
        self._replace((WorkspaceFolder(root_uri, root_uri),))

    def apply_change(self, params: typing.Any) -> bool:
        """Apply one workspace/didChangeWorkspaceFolders notification."""
        if not isinstance(params, dict):
            raise WorkspaceFolderError("workspace folder change params must be an object")
        event = params.get("event")
        if not isinstance(event, dict):
            raise WorkspaceFolderError("workspace folder change event must be an object")
        added = event.get("added")
        removed = event.get("removed")
        if not isinstance(added, list) or not isinstance(removed, list):
            raise WorkspaceFolderError("workspace folder change lists must be arrays")

        additions = self._parse_folders(added)
        removals = self._parse_folders(removed)
        with self._lock:
            current = {} if self._folders is None else dict(self._folders)
            before = tuple(sorted(current))
            before_names = {uri: folder.name for uri, folder in current.items()}

            for folder in removals:
                current.pop(folder.uri, None)
            for folder in additions:
                current[folder.uri] = folder

            after = tuple(sorted(current))
            renamed = any(
                before_names.get(folder.uri) != folder.name
                for folder in additions
                if folder.uri in before_names
            )
            if self._folders is None or before != after or renamed:
                self._folders = current
                self._generation += 1
                return True
            self._folders = current
            return False

    def contains(self, uri: str) -> bool:
        with self._lock:
            if self._folders is None:
                return True
            folders = tuple(self._folders.values())
        return any(self._contains(folder.uri, uri) for folder in folders)

    def commit_if_current(self, generation: int, callback: collections.abc.Callable[[], _T]) -> _T:
        """Run a derived workspace publication only while folder scope is unchanged."""
        if not callable(callback):
            raise WorkspaceFolderError("workspace folder commit must be callable")
        with self._lock:
            if generation != self._generation:
                raise WorkspaceFolderError("workspace folder generation changed")
            return callback()

    def _replace(self, folders: tuple[WorkspaceFolder, ...]) -> None:
        replacement = {folder.uri: folder for folder in folders}
        with self._lock:
            if self._folders == replacement:
                return
            self._folders = replacement
            self._generation += 1

    @staticmethod
    def _parse_folders(values: list[typing.Any]) -> tuple[WorkspaceFolder, ...]:
        parsed: list[WorkspaceFolder] = []
        seen: set[str] = set()
        for value in values:
            if not isinstance(value, dict):
                raise WorkspaceFolderError("workspace folder entries must be objects")
            folder = WorkspaceFolder(value.get("uri"), value.get("name"))
            if folder.uri in seen:
                raise WorkspaceFolderError("workspace folder URIs must be unique")
            seen.add(folder.uri)
            parsed.append(folder)
        return tuple(parsed)

    @staticmethod
    def _contains(folder_uri: str, document_uri: str) -> bool:
        try:
            folder = urllib.parse.urlsplit(folder_uri)
            document = urllib.parse.urlsplit(document_uri)
        except ValueError:
            return False
        if (
            folder.scheme != document.scheme
            or folder.netloc != document.netloc
            or folder.query
            or folder.fragment
        ):
            return False
        folder_path = folder.path.rstrip("/")
        document_path = document.path
        if not folder_path:
            return document_path.startswith("/")
        return document_path == folder_path or document_path.startswith(folder_path + "/")
