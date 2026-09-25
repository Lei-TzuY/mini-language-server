from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params,
    }


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: NovaProductLanguageServer,
    folders: list[dict[str, str]],
) -> None:
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {"textDocument": {"completion": {}}},
                "workspaceFolders": folders,
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    version: int = 1,
) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": version,
                    "text": text,
                }
            },
        )
    )


def complete(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int,
) -> dict[str, Any]:
    response = server.handle(
        request(
            "textDocument/completion",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": len(text)},
            },
        )
    )
    assert response is not None
    return response


def labels(response: dict[str, Any]) -> list[str]:
    result = response["result"]
    assert isinstance(result, list)
    return [item["label"] for item in result]


def test_relative_import_path_completion_replaces_incomplete_token(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    target = root / "library.nova"
    target.write_text("fn target() {}\n", encoding="utf-8")
    main = root / "main.nova"
    text = "import ./li"
    server = NovaProductLanguageServer()
    initialize(server, [{"uri": root.as_uri(), "name": "workspace"}])
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=10)

    assert response["result"] == [
        {
            "label": "./library.nova",
            "kind": 17,
            "detail": "Nova module",
            "textEdit": {
                "range": {
                    "start": {"line": 0, "character": 7},
                    "end": {"line": 0, "character": len(text)},
                },
                "newText": "./library.nova",
            },
        }
    ]


def test_import_path_completion_lists_unique_named_root_prefixes(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    main = app / "main.nova"
    text = "import @sha"
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=11)

    assert response["result"] == [
        {
            "label": "@shared/",
            "kind": 19,
            "detail": "module folder",
            "textEdit": {
                "range": {
                    "start": {"line": 0, "character": 7},
                    "end": {"line": 0, "character": len(text)},
                },
                "newText": "@shared/",
            },
        }
    ]


def test_named_root_import_completion_uses_closed_workspace_targets(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    target = shared / "provider.nova"
    target.write_text("fn target() {}\n", encoding="utf-8")
    main = app / "main.nova"
    text = "import @shared/pro"
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=12)

    assert labels(response) == ["@shared/provider.nova"]
    assert response["result"][0]["textEdit"]["newText"] == "@shared/provider.nova"
    assert server.documents.get(target.as_uri()) is None


def test_workspace_root_completion_excludes_importer_itself(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    other = root / "other.nova"
    other.write_text("fn other() {}\n", encoding="utf-8")
    main = root / "main.nova"
    text = "import @/"
    server = NovaProductLanguageServer()
    initialize(server, [{"uri": root.as_uri(), "name": "workspace"}])
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=13)

    assert labels(response) == ["@/other.nova"]


def test_named_root_completion_fails_closed_for_duplicate_folder_names(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    app.mkdir()
    left.mkdir()
    right.mkdir()
    (left / "provider.nova").write_text("fn left() {}\n", encoding="utf-8")
    (right / "provider.nova").write_text("fn right() {}\n", encoding="utf-8")
    main = app / "main.nova"
    text = "import @shared/"
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "shared"},
            {"uri": right.as_uri(), "name": "shared"},
        ],
    )
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=14)

    assert response["result"] == []


def test_import_path_completion_rejects_folder_topology_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    (shared / "provider.nova").write_text("fn target() {}\n", encoding="utf-8")
    main = app / "main.nova"
    text = "import @shared/pro"
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    open_nova(server, main.as_uri(), text)
    real_commit = server.workspace_folders.commit_if_current

    def drift_then_commit(generation: int, callback: Any):
        server.workspace_folders.apply_change(
            {
                "event": {
                    "removed": [{"uri": shared.as_uri(), "name": "shared"}],
                    "added": [{"uri": shared.as_uri(), "name": "library"}],
                }
            }
        )
        return real_commit(generation, callback)

    monkeypatch.setattr(
        server.workspace_folders,
        "commit_if_current",
        drift_then_commit,
    )

    assert complete(server, main.as_uri(), text, request_id=15) == {
        "jsonrpc": "2.0",
        "id": 15,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_non_import_completion_keeps_existing_workspace_candidates(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    library = root / "library.nova"
    main = root / "main.nova"
    server = NovaProductLanguageServer()
    initialize(server, [{"uri": root.as_uri(), "name": "workspace"}])
    open_nova(server, library.as_uri(), "fn target() {}\n")
    text = "fn caller() {}"
    open_nova(server, main.as_uri(), text)

    response = complete(server, main.as_uri(), text, request_id=16)

    assert {"caller", "target"} <= set(labels(response))
