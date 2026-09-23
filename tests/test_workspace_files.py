from __future__ import annotations

from pathlib import Path

from mini_language_server.workspace_files import (
    local_path_from_file_uri,
    scan_closed_workspace_files,
)
from mini_language_server.workspace_folders import WorkspaceFolderSet


def test_local_path_from_file_uri_fails_closed_for_nonlocal_shapes(
    tmp_path: Path,
) -> None:
    local_uri = (tmp_path / "main.nova").as_uri()

    assert local_path_from_file_uri(local_uri) is not None
    assert local_path_from_file_uri("https://example.com/main.nova") is None
    assert local_path_from_file_uri(local_uri + "?rev=1") is None
    assert local_path_from_file_uri(local_uri + "#fragment") is None
    assert local_path_from_file_uri("file://remote-host/work/main.nova") is None


def test_scan_closed_workspace_files_is_bounded_and_utf8_only(
    tmp_path: Path,
) -> None:
    good = tmp_path / "a.nova"
    good.write_text("fn a() {}\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("fn ignored() {}\n", encoding="utf-8")
    (tmp_path / "large.nova").write_bytes(b"x" * 64)
    (tmp_path / "invalid.nova").write_bytes(b"\xff")

    files = scan_closed_workspace_files(
        (tmp_path.as_uri(),),
        max_file_bytes=32,
    )

    assert [(item.uri, item.text) for item in files] == [
        (good.absolute().as_uri(), "fn a() {}\n")
    ]


def test_scan_excludes_rfc_equivalent_open_uri_identity(tmp_path: Path) -> None:
    source = tmp_path / "lib~.nova"
    source.write_text("fn target() {}\n", encoding="utf-8")
    encoded_uri = source.absolute().as_uri().replace("~", "%7E")
    identity = WorkspaceFolderSet.uri_identity(encoded_uri)

    files = scan_closed_workspace_files(
        (tmp_path.as_uri(),),
        exclude_identities=frozenset({identity}),
    )

    assert files == ()


def test_scan_honors_file_limit_deterministically(tmp_path: Path) -> None:
    for name in ("c.nova", "a.nova", "b.nova"):
        (tmp_path / name).write_text(f"fn {name[0]}() {{}}\n", encoding="utf-8")

    files = scan_closed_workspace_files((tmp_path.as_uri(),), max_files=2)

    assert [Path(local_path_from_file_uri(item.uri)).name for item in files] == [
        "a.nova",
        "b.nova",
    ]
