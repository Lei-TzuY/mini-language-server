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
