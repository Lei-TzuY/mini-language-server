"""Language-independent LSP workspace-folder scope tracking."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from urllib import parse


_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
)


class WorkspaceFolderError(ValueError):
    """Raised when workspace-folder lifecycle data is malformed."""


class WorkspaceFolder:
    """Immutable workspace-folder value used by the session scope."""

    __slots__ = ("_name", "_uri")

    def __init__(self, uri: object, name: object) -> None:
        if not isinstance(uri, str) or not uri:
            raise WorkspaceFolderError("workspace folder URI must be a non-empty string")
        if not isinstance(name, str):
            raise WorkspaceFolderError("workspace folder name must be a string")
        self._uri = uri
        self._name = name

    @property
    def uri(self) -> str:
        return self._uri

    @property
    def name(self) -> str:
        return self._name

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, WorkspaceFolder)
            and self.uri == other.uri
            and self.name == other.name
        )

    def __hash__(self) -> int:
        return hash((self.uri, self.name))


@dataclass(frozen=True, slots=True)
class WorkspaceFolderSnapshot:
    """Immutable workspace-folder scope capture for exact derived queries."""

    scoped: bool
    folders: tuple[WorkspaceFolder, ...]
    generation: int

    def contains(self, uri: str) -> bool:
        if not self.scoped:
            return True
        return any(
            WorkspaceFolderSet._contains(folder.uri, uri)
            for folder in self.folders
        )


class WorkspaceFolderSet:
    """Track one session workspace-folder scope.

    None means legacy/unscoped mode: every open document may participate in
    workspace-wide tooling. An explicit folder mapping, including an empty one,
    means workspace-folder scoping is active.
    """

    def __init__(self) -> None:
        self._folders: (
            dict[tuple[str, str, str, str, str], WorkspaceFolder] | None
        ) = None
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

    def snapshot(self) -> WorkspaceFolderSnapshot:
        """Capture one immutable scope/generation view for derived workspace work."""
        with self._lock:
            scoped = self._folders is not None
            folders = (
                ()
                if self._folders is None
                else tuple(self._folders[uri] for uri in sorted(self._folders))
            )
            return WorkspaceFolderSnapshot(scoped, folders, self._generation)

    def configure(self, params: object) -> None:
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

    def apply_change(self, params: object) -> bool:
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
            before = tuple(
                sorted(
                    (identity, folder.uri, folder.name)
                    for identity, folder in current.items()
                )
            )

            for folder in removals:
                current.pop(self._folder_identity(folder.uri), None)
            for folder in additions:
                current[self._folder_identity(folder.uri)] = folder

            after = tuple(
                sorted(
                    (identity, folder.uri, folder.name)
                    for identity, folder in current.items()
                )
            )
            if self._folders is None or before != after:
                self._folders = current
                self._generation += 1
                return True
            self._folders = current
            return False

    def contains(self, uri: str) -> bool:
        return self.snapshot().contains(uri)

    def scope_uri_for(self, uri: str) -> str | None:
        """Return the most specific configured workspace folder containing the URI."""
        with self._lock:
            if self._folders is None:
                return None
            folders = tuple(self._folders.values())

        matches = [folder.uri for folder in folders if self._contains(folder.uri, uri)]
        if not matches:
            return None
        return max(
            matches,
            key=lambda folder_uri: len(
                self._normalized_path(folder_uri).rstrip("/")
            ),
        )

    def commit_if_current(self, generation: int, callback):
        """Run a derived workspace publication only while folder scope is unchanged."""
        if not callable(callback):
            raise WorkspaceFolderError("workspace folder commit must be callable")
        with self._lock:
            if generation != self._generation:
                raise WorkspaceFolderError("workspace folder generation changed")
            return callback()

    def _replace(self, folders: tuple[WorkspaceFolder, ...]) -> None:
        replacement = {
            self._folder_identity(folder.uri): folder
            for folder in folders
        }
        with self._lock:
            if self._folders == replacement:
                return
            self._folders = replacement
            self._generation += 1

    @staticmethod
    def _parse_folders(values: list[object]) -> tuple[WorkspaceFolder, ...]:
        parsed: list[WorkspaceFolder] = []
        seen: set[tuple[str, str, str, str, str]] = set()
        for value in values:
            if not isinstance(value, dict):
                raise WorkspaceFolderError("workspace folder entries must be objects")
            folder = WorkspaceFolder(value.get("uri"), value.get("name"))
            identity = WorkspaceFolderSet._folder_identity(folder.uri)
            if identity in seen:
                raise WorkspaceFolderError("workspace folder URIs must be unique")
            seen.add(identity)
            parsed.append(folder)
        return tuple(parsed)

    @staticmethod
    def _contains(folder_uri: str, document_uri: str) -> bool:
        try:
            folder = parse.urlsplit(folder_uri)
            document = parse.urlsplit(document_uri)
        except ValueError:
            return False
        if (
            folder.query
            or folder.fragment
            or folder.scheme.lower() != document.scheme.lower()
            or WorkspaceFolderSet._normalize_authority(folder.netloc)
            != WorkspaceFolderSet._normalize_authority(document.netloc)
        ):
            return False
        folder_path = WorkspaceFolderSet._normalize_percent_encoding(
            folder.path
        ).rstrip("/")
        document_path = WorkspaceFolderSet._normalize_percent_encoding(
            document.path
        )
        if not folder_path:
            return document_path.startswith("/")
        return document_path == folder_path or document_path.startswith(
            folder_path + "/"
        )

    @staticmethod
    def _folder_identity(uri: str) -> tuple[str, str, str, str, str]:
        """Return one RFC-safe identity key while preserving the original URI."""
        try:
            parsed = parse.urlsplit(uri)
        except ValueError:
            return ("__invalid__", uri, "", "", "")
        normalized_path = WorkspaceFolderSet._normalize_percent_encoding(
            parsed.path
        )
        if normalized_path != "/":
            normalized_path = normalized_path.rstrip("/")
        return (
            parsed.scheme.lower(),
            WorkspaceFolderSet._normalize_authority(parsed.netloc),
            normalized_path,
            WorkspaceFolderSet._normalize_percent_encoding(parsed.query),
            WorkspaceFolderSet._normalize_percent_encoding(parsed.fragment),
        )

    @staticmethod
    def _normalized_path(uri: str) -> str:
        try:
            parsed = parse.urlsplit(uri)
        except ValueError:
            return uri
        return WorkspaceFolderSet._normalize_percent_encoding(parsed.path)

    @staticmethod
    def _normalize_authority(authority: str) -> str:
        userinfo, separator, hostport = authority.rpartition("@")
        prefix = (
            WorkspaceFolderSet._normalize_percent_encoding(userinfo) + "@"
            if separator
            else ""
        )

        if hostport.startswith("["):
            closing = hostport.find("]")
            if closing < 0:
                return WorkspaceFolderSet._normalize_percent_encoding(
                    authority
                )
            host = hostport[: closing + 1]
            suffix = hostport[closing + 1 :]
        else:
            host, colon, port = hostport.rpartition(":")
            if colon:
                suffix = ":" + port
            else:
                host = hostport
                suffix = ""

        normalized_host = WorkspaceFolderSet._normalize_percent_encoding(
            host
        ).lower()
        return prefix + normalized_host + suffix

    @staticmethod
    def _normalize_percent_encoding(value: str) -> str:
        """Normalize percent triplets without decoding reserved delimiters."""
        normalized: list[str] = []
        index = 0
        while index < len(value):
            if (
                value[index] == "%"
                and index + 2 < len(value)
                and all(
                    character in "0123456789abcdefABCDEF"
                    for character in value[index + 1 : index + 3]
                )
            ):
                byte = int(value[index + 1 : index + 3], 16)
                character = chr(byte)
                if character in _UNRESERVED:
                    normalized.append(character)
                else:
                    normalized.append(f"%{byte:02X}")
                index += 3
                continue
            normalized.append(value[index])
            index += 1
        return "".join(normalized)
