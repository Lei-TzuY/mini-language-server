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
    folders: list[dict[str, str]],
    *,
    document_links: bool = False,
    will_rename: bool = False,
    document_changes: bool = False,
) -> None:
    text_document: dict[str, Any] = {}
    if document_links:
        text_document["documentLink"] = {}
    workspace: dict[str, Any] = {"workspaceFolders": True}
    if will_rename:
        workspace["fileOperations"] = {"willRename": True}
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "textDocument": text_document,
                    "workspace": workspace,
                },
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


def diagnostic_codes(server: NovaProductLanguageServer, uri: str) -> set[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return {
        item.code
        for item in snapshot.diagnostics
        if item.code is not None
    }


def test_parser_accepts_named_roots_for_all_module_edge_shapes() -> None:
    server = NovaProductLanguageServer()
    text = (
        "import @shared/lib/dep.nova;\n"
        "import { target } from @api/provider.nova;\n"
        "import * as sdk from @sdk/provider.nova;\n"
        "export * from @facade/provider.nova;\n"
        "fn main() {}\n"
    )

    tree = server.nova_adapter.parse(text)

    assert isinstance(tree, NovaFunctionSyntax)
    assert [item.path for item in tree.imports] == [
        "@shared/lib/dep.nova",
        "@api/provider.nova",
        "@sdk/provider.nova",
    ]
    assert tree.imports[1].has_name_list is True
    assert tree.imports[2].namespace == "sdk"
    assert [item.path for item in tree.wildcard_exports] == [
        "@facade/provider.nova"
    ]


def test_named_root_resolves_cross_folder_visibility_and_document_link(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    provider = shared / "provider.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        document_links=True,
    )
    caller = app / "main.nova"
    source = (
        "import { target } from @shared/provider.nova;\n"
        "fn caller() { target(); }\n"
    )
    open_nova(server, caller.as_uri(), source)

    assert "nova.unresolved-import" not in diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-function" not in diagnostic_codes(server, caller.as_uri())

    links = server.handle(
        request(
            "textDocument/documentLink",
            10,
            {"textDocument": {"uri": caller.as_uri()}},
        )
    )
    assert links is not None
    assert links["result"] == [
        {
            "range": {
                "start": {"line": 0, "character": 23},
                "end": {"line": 0, "character": 44},
            },
            "target": provider.as_uri(),
        }
    ]


def test_named_root_namespace_and_wildcard_export_share_module_graph(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    provider = shared / "provider.nova"
    provider.write_text(
        "fn target() {}\nexport { target };\n",
        encoding="utf-8",
    )
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    facade = app / "facade.nova"
    root = app / "root.nova"
    namespace_user = app / "namespace.nova"
    open_nova(
        server,
        facade.as_uri(),
        "export * from @shared/provider.nova;\n",
    )
    open_nova(
        server,
        root.as_uri(),
        "import ./facade.nova;\nfn root() { target(); }\n",
    )
    open_nova(
        server,
        namespace_user.as_uri(),
        "import * as api from @shared/provider.nova;\n"
        "fn root() { api::target(); }\n",
    )

    assert "nova.unresolved-export-target" not in diagnostic_codes(
        server,
        facade.as_uri(),
    )
    assert "nova.unresolved-function" not in diagnostic_codes(server, root.as_uri())
    assert "nova.unresolved-function" not in diagnostic_codes(
        server,
        namespace_user.as_uri(),
    )


def test_named_roots_participate_in_cross_folder_import_cycles(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    left = app / "left.nova"
    right = shared / "right.nova"
    open_nova(
        server,
        left.as_uri(),
        "import @shared/right.nova;\nfn left() {}\n",
    )
    open_nova(
        server,
        right.as_uri(),
        "import @app/left.nova;\nfn right() {}\n",
    )

    assert "nova.import-cycle" in diagnostic_codes(server, left.as_uri())
    assert "nova.import-cycle" in diagnostic_codes(server, right.as_uri())


def test_named_root_is_fail_closed_when_folder_name_is_ambiguous(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    left = tmp_path / "left"
    right = tmp_path / "right"
    app.mkdir()
    left.mkdir()
    right.mkdir()
    (left / "provider.nova").write_text("fn target() {}\n", encoding="utf-8")
    (right / "provider.nova").write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": left.as_uri(), "name": "shared"},
            {"uri": right.as_uri(), "name": "shared"},
        ],
    )
    caller = app / "main.nova"
    open_nova(
        server,
        caller.as_uri(),
        "import @shared/provider.nova;\nfn caller() { target(); }\n",
    )

    assert "nova.unresolved-import" in diagnostic_codes(server, caller.as_uri())
    assert "nova.unresolved-function" in diagnostic_codes(server, caller.as_uri())


def test_named_root_rebinds_on_workspace_folder_name_change(
    tmp_path: Path,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    (shared / "provider.nova").write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    caller = app / "main.nova"
    open_nova(
        server,
        caller.as_uri(),
        "import @shared/provider.nova;\nfn caller() { target(); }\n",
    )
    assert "nova.unresolved-import" not in diagnostic_codes(server, caller.as_uri())

    before = server.workspace_folders.generation
    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "removed": [{"uri": shared.as_uri(), "name": "shared"}],
                    "added": [{"uri": shared.as_uri(), "name": "library"}],
                }
            },
        )
    )

    assert server.workspace_folders.generation > before
    assert "nova.unresolved-import" in diagnostic_codes(server, caller.as_uri())


def test_named_root_rejects_parent_escape(tmp_path: Path) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
    )
    caller = app / "main.nova"
    open_nova(
        server,
        caller.as_uri(),
        "import @shared/../outside.nova;\nfn caller() {}\n",
    )

    assert "nova.unresolved-import" in diagnostic_codes(server, caller.as_uri())


def test_will_rename_preserves_named_root_spelling(tmp_path: Path) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    provider = shared / "provider.nova"
    renamed = shared / "renamed.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        will_rename=True,
        document_changes=True,
    )
    caller = app / "main.nova"
    source = "import @shared/provider.nova;\nfn caller() { target(); }\n"
    open_nova(server, caller.as_uri(), source, version=7)

    response = server.handle(
        request(
            "workspace/willRenameFiles",
            20,
            {
                "files": [
                    {
                        "oldUri": provider.as_uri(),
                        "newUri": renamed.as_uri(),
                    }
                ]
            },
        )
    )

    assert response is not None
    assert response["result"]["documentChanges"] == [
        {
            "textDocument": {"uri": caller.as_uri(), "version": 7},
            "edits": [
                {
                    "range": {
                        "start": {"line": 0, "character": 7},
                        "end": {"line": 0, "character": 28},
                    },
                    "newText": "@shared/renamed.nova",
                }
            ],
        }
    ]


def test_named_root_rename_rejects_folder_topology_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = tmp_path / "app"
    shared = tmp_path / "shared"
    app.mkdir()
    shared.mkdir()
    provider = shared / "provider.nova"
    renamed = shared / "renamed.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize(
        server,
        [
            {"uri": app.as_uri(), "name": "app"},
            {"uri": shared.as_uri(), "name": "shared"},
        ],
        will_rename=True,
        document_changes=True,
    )
    caller = app / "main.nova"
    open_nova(
        server,
        caller.as_uri(),
        "import @shared/provider.nova;\nfn caller() { target(); }\n",
        version=8,
    )
    original = server._nova_import_rename_changes

    def rename_root_during_planning(snapshots, renames):
        changes = original(snapshots, renames)
        server.handle(
            notify(
                "workspace/didChangeWorkspaceFolders",
                {
                    "event": {
                        "removed": [
                            {"uri": shared.as_uri(), "name": "shared"}
                        ],
                        "added": [
                            {"uri": shared.as_uri(), "name": "library"}
                        ],
                    }
                },
            )
        )
        return changes

    monkeypatch.setattr(
        server,
        "_nova_import_rename_changes",
        rename_root_during_planning,
    )

    assert server.handle(
        request(
            "workspace/willRenameFiles",
            21,
            {
                "files": [
                    {
                        "oldUri": provider.as_uri(),
                        "newUri": renamed.as_uri(),
                    }
                ]
            },
        )
    ) == {
        "jsonrpc": "2.0",
        "id": 21,
        "error": {"code": -32801, "message": "Content modified"},
    }
