"""Bounded local workspace-file discovery for closed-file indexing."""

from __future__ import annotations

import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

MAX_CLOSED_WORKSPACE_FILES = 2048
MAX_CLOSED_WORKSPACE_FILE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ClosedWorkspaceFile:
    uri: str
    text: str


def local_path_from_file_uri(uri: str) -> Path | None:
    """Return one local path for a supported file URI, otherwise fail closed."""
    if not isinstance(uri, str) or not uri:
        return None
    try:
        parsed = urllib.parse.urlsplit(uri)
    except ValueError:
        return None
    if (
        parsed.scheme != "file"
        or parsed.query
        or parsed.fragment
        or parsed.netloc not in {"", "localhost"}
    ):
        return None
    path_text = urllib.request.url2pathname(urllib.parse.unquote(parsed.path))
    if not path_text:
        return None
    return Path(path_text)


def read_closed_workspace_file(
    uri: str,
    *,
    max_file_bytes: int = MAX_CLOSED_WORKSPACE_FILE_BYTES,
) -> ClosedWorkspaceFile | None:
    """Read one bounded UTF-8 Nova file from a local file URI."""
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")
    path = local_path_from_file_uri(uri)
    if path is None:
        return None
    try:
        if path.is_symlink() or not path.is_file():
            return None
        if path.stat().st_size > max_file_bytes:
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
    return ClosedWorkspaceFile(path.resolve().as_uri(), text)


def scan_closed_workspace_files(
    folder_uris: tuple[str, ...],
    *,
    exclude_uris: frozenset[str] = frozenset(),
    max_files: int = MAX_CLOSED_WORKSPACE_FILES,
    max_file_bytes: int = MAX_CLOSED_WORKSPACE_FILE_BYTES,
) -> tuple[ClosedWorkspaceFile, ...]:
    """Discover deterministic bounded closed Nova files under local workspace roots."""
    if max_files <= 0:
        raise ValueError("max_files must be positive")
    if max_file_bytes <= 0:
        raise ValueError("max_file_bytes must be positive")

    candidates: dict[str, Path] = {}
    for folder_uri in folder_uris:
        root = local_path_from_file_uri(folder_uri)
        if root is None:
            continue
        try:
            if not root.is_dir():
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
                resolved = path.resolve()
                uri = resolved.as_uri()
            except OSError:
                continue
            if uri in exclude_uris:
                continue
            candidates.setdefault(uri, resolved)
        if len(candidates) >= max_files:
            break

    files: list[ClosedWorkspaceFile] = []
    for uri in sorted(candidates):
        item = read_closed_workspace_file(uri, max_file_bytes=max_file_bytes)
        if item is not None:
            files.append(item)
    return tuple(files)
