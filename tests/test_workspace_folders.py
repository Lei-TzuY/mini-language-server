from __future__ import annotations

import pytest

from mini_language_server.workspace_folders import (
    WorkspaceFolderError,
    WorkspaceFolderSet,
)


def test_unconfigured_workspace_scope_keeps_legacy_all_open_behavior() -> None:
    folders = WorkspaceFolderSet()

    assert not folders.scoped
    assert folders.contains("file:///anywhere/main.nova")


def test_workspace_folders_scope_uses_uri_path_boundaries() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "file:///work/app", "name": "app"},
            ]
        }
    )

    assert folders.scoped
    assert folders.contains("file:///work/app/main.nova")
    assert folders.contains("file:///work/app/nested/lib.nova")
    assert not folders.contains("file:///work/application/main.nova")
    assert not folders.contains("file:///other/main.nova")


def test_root_uri_is_bounded_fallback_when_workspace_folders_absent() -> None:
    folders = WorkspaceFolderSet()
    folders.configure({"rootUri": "file:///workspace"})

    assert folders.contains("file:///workspace/main.nova")
    assert not folders.contains("file:///outside/main.nova")


def test_folder_change_adds_and_removes_scope_with_generation() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {"workspaceFolders": [{"uri": "file:///a", "name": "a"}]}
    )
    captured = folders.generation

    assert folders.apply_change(
        {
            "event": {
                "removed": [{"uri": "file:///a", "name": "a"}],
                "added": [{"uri": "file:///b", "name": "b"}],
            }
        }
    )
    assert folders.generation > captured
    assert not folders.contains("file:///a/main.nova")
    assert folders.contains("file:///b/main.nova")


def test_folder_generation_guard_rejects_scope_change() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {"workspaceFolders": [{"uri": "file:///a", "name": "a"}]}
    )
    generation = folders.generation
    folders.apply_change(
        {
            "event": {
                "removed": [],
                "added": [{"uri": "file:///b", "name": "b"}],
            }
        }
    )

    with pytest.raises(WorkspaceFolderError, match="generation changed"):
        folders.commit_if_current(generation, lambda: None)


def test_malformed_workspace_folder_change_is_rejected() -> None:
    folders = WorkspaceFolderSet()

    with pytest.raises(WorkspaceFolderError):
        folders.apply_change({"event": {"added": [], "removed": "bad"}})

def test_scope_uri_for_returns_most_specific_nested_folder() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "file:///work/app", "name": "app"},
                {"uri": "file:///work/app/packages/core", "name": "core"},
            ]
        }
    )

    assert folders.scope_uri_for("file:///work/app/main.nova") == "file:///work/app"
    assert (
        folders.scope_uri_for("file:///work/app/packages/core/lib.nova")
        == "file:///work/app/packages/core"
    )
    assert folders.scope_uri_for("file:///outside/main.nova") is None


def test_scope_uri_for_is_none_in_legacy_unscoped_mode() -> None:
    folders = WorkspaceFolderSet()

    assert folders.contains("file:///anywhere/main.nova")
    assert folders.scope_uri_for("file:///anywhere/main.nova") is None

def test_workspace_scope_normalizes_scheme_host_and_unreserved_encoding() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "FILE://SERVER/work/%7eapp", "name": "app"},
            ]
        }
    )

    assert folders.contains("file://server/work/~app/main.nova")
    assert folders.contains("FiLe://SeRvEr/work/%7Eapp/nested/lib.nova")


def test_workspace_scope_keeps_path_case_sensitive() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "file:///Work/App", "name": "app"},
            ]
        }
    )

    assert folders.contains("file:///Work/App/main.nova")
    assert not folders.contains("file:///work/app/main.nova")


def test_workspace_scope_does_not_decode_reserved_slash() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "file:///work/app", "name": "app"},
            ]
        }
    )

    assert not folders.contains("file:///work/app%2Fnested/main.nova")
    assert not folders.contains("file:///work/app%2fnested/main.nova")


def test_equivalent_workspace_folder_uris_are_duplicate_identity() -> None:
    folders = WorkspaceFolderSet()

    with pytest.raises(WorkspaceFolderError, match="must be unique"):
        folders.configure(
            {
                "workspaceFolders": [
                    {"uri": "FILE://SERVER/work/%7eapp", "name": "left"},
                    {"uri": "file://server/work/~app/", "name": "right"},
                ]
            }
        )


def test_workspace_folder_change_removes_canonical_equivalent_uri() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "FILE://SERVER/work/%7eapp/", "name": "app"},
            ]
        }
    )

    assert folders.apply_change(
        {
            "event": {
                "added": [],
                "removed": [
                    {"uri": "file://server/work/~app", "name": "app"},
                ],
            }
        }
    )
    assert not folders.contains("file://server/work/~app/main.nova")


def test_scope_uri_for_preserves_configured_uri_spelling() -> None:
    folders = WorkspaceFolderSet()
    configured = "FILE://SERVER/work/%7eapp/"
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": configured, "name": "app"},
                {
                    "uri": "file://server/work/~app/packages/core",
                    "name": "core",
                },
            ]
        }
    )

    assert (
        folders.scope_uri_for("file://SERVER/work/~app/main.nova")
        == configured
    )
    assert (
        folders.scope_uri_for(
            "FILE://server/work/%7Eapp/packages/core/lib.nova"
        )
        == "file://server/work/~app/packages/core"
    )

def test_workspace_folder_snapshot_resolves_most_specific_scope_uri() -> None:
    folders = WorkspaceFolderSet()
    folders.configure(
        {
            "workspaceFolders": [
                {"uri": "file:///workspace", "name": "root"},
                {"uri": "file:///workspace/app", "name": "app"},
            ]
        }
    )
    snapshot = folders.snapshot()

    assert snapshot.scope_uri_for("file:///workspace/app/main.nova") == (
        "file:///workspace/app"
    )
    assert snapshot.scope_uri_for("file:///workspace/lib/main.nova") == (
        "file:///workspace"
    )


def test_unscoped_workspace_folder_snapshot_has_no_project_scope_uri() -> None:
    folders = WorkspaceFolderSet()
    snapshot = folders.snapshot()

    assert snapshot.scope_uri_for("file:///workspace/main.nova") is None
