from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mini_language_server import NovaProductLanguageServer
from mini_language_server.nova import NovaFunctionSyntax


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
    *,
    folders: list[dict[str, str]] | None = None,
    will_rename: bool = False,
    document_changes: bool = False,
) -> None:
    workspace: dict[str, Any] = {}
    if folders is not None:
        workspace["workspaceFolders"] = True
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    params: dict[str, Any] = {"capabilities": {"workspace": workspace}}
    if folders is not None:
        params["workspaceFolders"] = folders
    response = server.handle(request("initialize", 1, params))
    assert response is not None
    if folders is not None:
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


def will_rename(
    server: NovaProductLanguageServer,
    old_uri: str,
    new_uri: str,
    *,
    request_id: int,
) -> dict[str, Any] | None:
    return server.handle(
        request(
            "workspace/willRenameFiles",
            request_id,
            {"files": [{"oldUri": old_uri, "newUri": new_uri}]},
        )
    )


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        item.code
        for item in snapshot.diagnostics
        if item.code is not None
    }


def test_import_syntax_is_top_level_and_trivia_safe() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import ./dep.nova;\n"
        "// import ./comment.nova;\n"
        "fn main() {\n"
        "  import ./nested.nova;\n"
        '  let text = "import ./string.nova;"\n'
        "}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert isinstance(tree, NovaFunctionSyntax)
    assert [(item.path, text[item.span.start : item.span.end]) for item in tree.imports] == [
        ("./dep.nova", "./dep.nova")
    ]


def test_open_import_diagnostic_tracks_exact_workspace_membership() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    importer_uri = "file:///workspace/main.nova"
    target_uri = "file:///workspace/dep.nova"
    source = "import ./dep.nova;\nfn main() {}\n"

    open_nova(server, importer_uri, source)
    assert "nova.unresolved-import" in diagnostic_codes(server, importer_uri)

    open_nova(server, target_uri, "fn dep() {}\n")
    assert "nova.unresolved-import" not in diagnostic_codes(server, importer_uri)

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": target_uri}}))
    assert "nova.unresolved-import" in diagnostic_codes(server, importer_uri)


def test_closed_import_diagnostic_uses_detached_workspace(tmp_path: Path) -> None:
    importer = tmp_path / "main.nova"
    target = tmp_path / "dep.nova"
    importer.write_text("import ./dep.nova;\nfn main() {}\n")
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
    )
    importer_uri = importer.as_uri()

    first = server.handle(
        request(
            "textDocument/diagnostic",
            10,
            {"textDocument": {"uri": importer_uri}},
        )
    )
    assert first is not None
    assert [item["code"] for item in first["result"]["items"]] == [
        "nova.unresolved-import"
    ]

    target.write_text("fn dep() {}\n")
    assert server._sync_closed_workspace_files() is True

    second = server.handle(
        request(
            "textDocument/diagnostic",
            11,
            {"textDocument": {"uri": importer_uri}},
        )
    )
    assert second is not None
    assert all(
        item["code"] != "nova.unresolved-import"
        for item in second["result"]["items"]
    )


def test_will_rename_rewrites_open_import_with_exact_version(tmp_path: Path) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    new_target = tmp_path / "renamed.nova"
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
        will_rename=True,
        document_changes=True,
    )
    importer_uri = (tmp_path / "main.nova").as_uri()
    source = "import ./dep.nova;\nfn main() {}\n"
    open_nova(server, importer_uri, source, version=7)

    response = will_rename(
        server,
        target.as_uri(),
        new_target.as_uri(),
        request_id=20,
    )

    assert response is not None
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": importer_uri, "version": 7},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 17},
                    },
                    "newText": "./renamed.nova",
                }
            ],
        }
    ]


def test_will_rename_rewrites_closed_import_with_null_version(tmp_path: Path) -> None:
    importer = tmp_path / "main.nova"
    target = tmp_path / "dep.nova"
    importer.write_text("import ./dep.nova;\nfn main() {}\n")
    target.write_text("fn dep() {}\n")
    new_target = tmp_path / "renamed.nova"
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
        will_rename=True,
        document_changes=True,
    )

    response = will_rename(
        server,
        target.as_uri(),
        new_target.as_uri(),
        request_id=21,
    )

    assert response is not None
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": importer.as_uri(), "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 17},
                    },
                    "newText": "./renamed.nova",
                }
            ],
        }
    ]


def test_will_rename_rebases_import_when_importer_moves_directory(
    tmp_path: Path,
) -> None:
    importer = tmp_path / "main.nova"
    target_dir = tmp_path / "lib"
    target_dir.mkdir()
    target = target_dir / "dep.nova"
    destination_dir = tmp_path / "app"
    destination_dir.mkdir()
    importer.write_text("import ./lib/dep.nova;\nfn main() {}\n")
    target.write_text("fn dep() {}\n")
    destination = destination_dir / "main.nova"
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
        will_rename=True,
        document_changes=True,
    )

    response = will_rename(
        server,
        importer.as_uri(),
        destination.as_uri(),
        request_id=22,
    )

    assert response is not None
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": importer.as_uri(), "version": None},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 21},
                    },
                    "newText": "../lib/dep.nova",
                }
            ],
        }
    ]


def test_will_rename_rejects_import_target_cross_authority(tmp_path: Path) -> None:
    target = tmp_path / "dep.nova"
    target.write_text("fn dep() {}\n")
    importer_uri = (tmp_path / "main.nova").as_uri()
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
        will_rename=True,
    )
    open_nova(server, importer_uri, "import ./dep.nova;\nfn main() {}\n")

    response = will_rename(
        server,
        target.as_uri(),
        "file://remote-host/workspace/dep.nova",
        request_id=23,
    )

    assert response is not None
    assert response["error"]["code"] == -32803
    assert "cannot preserve Nova import" in response["error"]["message"]


def test_will_rename_rejects_closed_importer_disk_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    importer = tmp_path / "main.nova"
    target = tmp_path / "dep.nova"
    importer.write_text("import ./dep.nova;\nfn main() {}\n")
    target.write_text("fn dep() {}\n")
    new_target = tmp_path / "renamed.nova"
    server = NovaProductLanguageServer()
    initialize(
        server,
        folders=[{"uri": tmp_path.as_uri(), "name": "workspace"}],
        will_rename=True,
    )
    original = server.workspace_symbols.commit_snapshots_if_current

    def drift_then_commit(snapshots, commit):
        importer.write_text("import ./dep.nova;\nfn changed() {}\n")
        return original(snapshots, commit)

    monkeypatch.setattr(
        server.workspace_symbols,
        "commit_snapshots_if_current",
        drift_then_commit,
    )

    response = will_rename(
        server,
        target.as_uri(),
        new_target.as_uri(),
        request_id=24,
    )

    assert response == {
        "jsonrpc": "2.0",
        "id": 24,
        "error": {"code": -32801, "message": "Content modified"},
    }
