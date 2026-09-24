"""Bounded local filesystem discovery for closed Nova workspace files."""

from __future__ import annotations

import os
import stat
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .workspace_folders import WorkspaceFolderSet

MAX_CLOSED_WORKSPACE_FILES = 2048
MAX_CLOSED_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024

WorkspaceUriIdentity = tuple[str, str, str, str, str]


@dataclass(frozen=True, slots=True)
class LocalWorkspacePathEvidence:
    """One exact local path-entry observation used by file-operation preflight."""

    uri: str
    identity: WorkspaceUriIdentity
    kind: str
    signature: tuple[int, int, int, int, int, int] | None

    @property
    def exists(self) -> bool:
        return self.kind != "missing"


@dataclass(frozen=True, slots=True)
class LocalWorkspaceMutationEvidence:
    """One exact local parent-directory mutation observation."""

    uri: str
    identity: WorkspaceUriIdentity
    parent_kind: str
    parent_signature: tuple[int, int, int, int, int, int] | None
    parent_write_search: bool

    @property
    def can_mutate_parent(self) -> bool:
        return self.parent_kind == "directory" and self.parent_write_search


@dataclass(frozen=True, slots=True)
class ClosedWorkspaceFile:
    """One bounded UTF-8 Nova source discovered outside the open-document store."""

    uri: str
    identity: WorkspaceUriIdentity
    text: str


def local_path_from_file_uri(uri: str) -> Path | None:
    """Map one supported local file URI to a path without inventing host semantics."""
    if not isinstance(uri, str) or not uri:
        return None
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return None
    if (
        parsed.scheme.lower() != "file"
        or parsed.query
        or parsed.fragment
        or parsed.netloc.lower() not in {"", "localhost"}
    ):
        return None

    path_text = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    if not path_text:
        return None
    return Path(path_text)


def probe_local_workspace_path(uri: str) -> LocalWorkspacePathEvidence | None:
    """Capture one supported local path entry without following symlink targets."""
    path = local_path_from_file_uri(uri)
    if path is None:
        return None
    identity = WorkspaceFolderSet.uri_identity(uri)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return LocalWorkspacePathEvidence(
            uri=uri,
            identity=identity,
            kind="missing",
            signature=None,
        )
    except OSError:
        return None

    mode = metadata.st_mode
    if stat.S_ISREG(mode):
        kind = "file"
    elif stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    else:
        kind = "other"
    return LocalWorkspacePathEvidence(
        uri=uri,
        identity=identity,
        kind=kind,
        signature=(
            mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_ino,
            metadata.st_dev,
        ),
    )


def probe_local_workspace_mutation(
    uri: str,
) -> LocalWorkspaceMutationEvidence | None:
    """Capture immediate-parent mutation feasibility for one supported local URI."""
    path = local_path_from_file_uri(uri)
    if path is None:
        return None
    identity = WorkspaceFolderSet.uri_identity(uri)
    parent = path.parent
    try:
        metadata = parent.lstat()
    except OSError:
        return None

    mode = metadata.st_mode
    if stat.S_ISDIR(mode):
        kind = "directory"
    elif stat.S_ISLNK(mode):
        kind = "symlink"
    elif stat.S_ISREG(mode):
        kind = "file"
    else:
        kind = "other"
    return LocalWorkspaceMutationEvidence(
        uri=uri,
        identity=identity,
        parent_kind=kind,
        parent_signature=(
            mode,
            metadata.st_size,
            metadata.st_mtime_ns,
            metadata.st_ctime_ns,
            metadata.st_ino,
            metadata.st_dev,
        ),
        parent_write_search=(
            kind == "directory"
            and os.access(parent, os.W_OK | os.X_OK)
        ),
    )


def read_closed_workspace_file(
    uri: str,
    *,
    max_file_bytes: int = MAX_CLOSED_WORKSPACE_FILE_BYTES,
) -> ClosedWorkspaceFile | None:
    """Read one bounded UTF-8 Nova file from a supported local file URI."""
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
    path = local_path_from_file_uri(uri)
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        stat = path.stat()
        if stat.st_size > max_file_bytes:
            return None
        payload = path.read_bytes()
    except OSError:
        return None
    if len(payload) > max_file_bytes:
        return None
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return ClosedWorkspaceFile(
        uri=uri,
        identity=WorkspaceFolderSet.uri_identity(uri),
        text=text,
    )


def scan_closed_workspace_files(
    folder_uris: tuple[str, ...],
    *,
    exclude_identities: frozenset[WorkspaceUriIdentity] = frozenset(),
    max_files: int = MAX_CLOSED_WORKSPACE_FILES,
    max_file_bytes: int = MAX_CLOSED_WORKSPACE_FILE_BYTES,
) -> tuple[ClosedWorkspaceFile, ...]:
    """Discover deterministic bounded closed Nova files under local workspace roots."""
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")

    candidates: dict[WorkspaceUriIdentity, str] = {}
    for folder_uri in folder_uris:
        root = local_path_from_file_uri(folder_uri)
        if root is None:
            continue
        try:
            if root.is_symlink() or not root.is_dir():
                continue
            paths = tuple(sorted(root.rglob("*.nova"), key=lambda path: str(path)))
        except OSError:
            continue

        for path in paths:
            if len(candidates) >= max_files:
                break
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                uri = path.absolute().as_uri()
            except (OSError, ValueError):
                continue
            identity = WorkspaceFolderSet.uri_identity(uri)
            if identity in exclude_identities:
                continue
            candidates.setdefault(identity, uri)
        if len(candidates) >= max_files:
            break

    files: list[ClosedWorkspaceFile] = []
    for identity in sorted(candidates):
        item = read_closed_workspace_file(
            candidates[identity],
            max_file_bytes=max_file_bytes,
        )
        if item is not None:
            files.append(item)
    return tuple(files)
