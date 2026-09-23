from __future__ import annotations

from pathlib import Path

from mini_language_server import NovaProductLanguageServer
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize_workspace(
    server: WorkspaceNovaLanguageServer,
    root: Path,
    *,
    document_changes: bool = False,
    did_rename: bool = False,
) -> None:
    workspace: dict = {
        "symbol": {},
        "workspaceFolders": True,
    }
    if document_changes:
        workspace["workspaceEdit"] = {"documentChanges": True}
    if did_rename:
        workspace["fileOperations"] = {"didRename": True}

    response = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {"workspace": workspace},
                "workspaceFolders": [{"uri": root.as_uri(), "name": "workspace"}],
            },
        )
    )
    assert response is not None
    server.handle(notify("initialized", {}))


def open_nova(
    server: WorkspaceNovaLanguageServer,
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


def call_position(text: str, name: str) -> dict[str, int]:
    return {"line": 0, "character": text.index(name) + 1}


def definition(
    server: WorkspaceNovaLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int = 2,
) -> dict:
    response = server.handle(
        request(
            "textDocument/definition",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": call_position(text, "target"),
            },
        )
    )
    assert response is not None
    return response


def hover(
    server: WorkspaceNovaLanguageServer,
    uri: str,
    text: str,
    *,
    request_id: int,
) -> dict:
    response = server.handle(
        request(
            "textDocument/hover",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": call_position(text, "target"),
            },
        )
    )
    assert response is not None
    return response


def test_initialized_indexes_closed_file_without_open_document_ownership(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library.nova"
    library.write_text(
        "fn target(flag: Bool) -> Bool { flag }\n",
        encoding="utf-8",
    )
    server = WorkspaceNovaLanguageServer()
    initialize_workspace(server, tmp_path)

    library_uri = library.absolute().as_uri()
    assert server.documents.get(library_uri) is None
    assert server.semantics.get(library_uri) is None
    assert server.diagnostics.get(library_uri) is None
    assert server.workspace_symbols.get(library_uri) is not None

    caller = tmp_path / "main.nova"
    caller_uri = caller.absolute().as_uri()
    caller_text = "fn main() { target(true) }\n"
    open_nova(server, caller_uri, caller_text)

    result = definition(server, caller_uri, caller_text)
    assert result["result"]["uri"] == library_uri
    assert result["result"]["range"] == {
        "start": {"line": 0, "character": 3},
        "end": {"line": 0, "character": 9},
    }

    symbols = server.handle(request("workspace/symbol", 3, {"query": "target"}))
    assert symbols is not None
    assert [(item["name"], item["location"]["uri"]) for item in symbols["result"]] == [
        ("target", library_uri)
    ]


def test_open_buffer_overrides_closed_disk_snapshot_and_close_restores_disk(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library.nova"
    library.write_text(
        "fn target(flag: Bool) -> Bool { flag }\n",
        encoding="utf-8",
    )
    server = WorkspaceNovaLanguageServer()
    initialize_workspace(server, tmp_path)

    caller_uri = (tmp_path / "main.nova").absolute().as_uri()
    caller_text = "fn main() { target(true) }\n"
    open_nova(server, caller_uri, caller_text)

    library_uri = library.absolute().as_uri()
    open_nova(
        server,
        library_uri,
        "fn target(flag: Int) -> Int { flag }\n",
        version=4,
    )
    declarations = [
        item
        for item in server.workspace_symbols.declarations("target")
        if item.symbol.kind == "function"
    ]
    assert len(declarations) == 1
    assert declarations[0].uri == library_uri
    assert hover(server, caller_uri, caller_text, request_id=4)["result"]["contents"][
        "value"
    ] == "fn target(flag: Int) -> Int"

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": library_uri}})
    )

    assert server.documents.get(library_uri) is None
    assert hover(server, caller_uri, caller_text, request_id=5)["result"]["contents"][
        "value"
    ] == "fn target(flag: Bool) -> Bool"


def test_rfc_equivalent_open_uri_does_not_duplicate_closed_disk_identity(
    tmp_path: Path,
) -> None:
    library = tmp_path / "lib~.nova"
    library.write_text("fn target() {}\n", encoding="utf-8")
    server = WorkspaceNovaLanguageServer()
    initialize_workspace(server, tmp_path)

    disk_uri = library.absolute().as_uri()
    open_uri = disk_uri.replace("~", "%7E")
    open_nova(server, open_uri, "fn target(flag: Bool) { flag }\n")

    declarations = [
        item
        for item in server.workspace_symbols.declarations("target")
        if item.symbol.kind == "function"
    ]
    assert [(item.uri, item.snapshot.symbols.syntax.document.text) for item in declarations] == [
        (open_uri, "fn target(flag: Bool) { flag }\n")
    ]


def test_closed_file_rename_notification_rescans_disk_index(tmp_path: Path) -> None:
    old_path = tmp_path / "old.nova"
    new_path = tmp_path / "new.nova"
    old_path.write_text("fn target() {}\n", encoding="utf-8")
    server = WorkspaceNovaLanguageServer()
    initialize_workspace(server, tmp_path, did_rename=True)

    caller_uri = (tmp_path / "main.nova").absolute().as_uri()
    caller_text = "fn main() { target() }\n"
    open_nova(server, caller_uri, caller_text)

    old_uri = old_path.absolute().as_uri()
    new_uri = new_path.absolute().as_uri()
    old_path.rename(new_path)
    server.handle(
        notify(
            "workspace/didRenameFiles",
            {"files": [{"oldUri": old_uri, "newUri": new_uri}]},
        )
    )

    assert server.workspace_symbols.get(old_uri) is None
    assert server.workspace_symbols.get(new_uri) is not None
    assert definition(server, caller_uri, caller_text, request_id=6)["result"]["uri"] == (
        new_uri
    )


def test_dynamic_workspace_folder_scope_adds_and_removes_closed_files(
    tmp_path: Path,
) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    provider = second / "provider.nova"
    provider.write_text("fn target() {}\n", encoding="utf-8")

    server = WorkspaceNovaLanguageServer()
    initialize_workspace(server, first)
    provider_uri = provider.absolute().as_uri()
    assert server.workspace_symbols.get(provider_uri) is None

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": second.as_uri(), "name": "second"}],
                    "removed": [],
                }
            },
        )
    )
    assert server.workspace_symbols.get(provider_uri) is not None

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [{"uri": second.as_uri(), "name": "second"}],
                }
            },
        )
    )
    assert server.workspace_symbols.get(provider_uri) is None


def test_product_versioned_rename_uses_null_version_for_closed_file(
    tmp_path: Path,
) -> None:
    library = tmp_path / "library.nova"
    library.write_text("fn target() {}\n", encoding="utf-8")
    server = NovaProductLanguageServer()
    initialize_workspace(server, tmp_path, document_changes=True)

    caller_uri = (tmp_path / "main.nova").absolute().as_uri()
    caller_text = "fn caller() { target() }\n"
    open_nova(server, caller_uri, caller_text, version=7)

    response = server.handle(
        request(
            "textDocument/rename",
            9,
            {
                "textDocument": {"uri": caller_uri},
                "position": call_position(caller_text, "target"),
                "newName": "renamed",
            },
        )
    )
    assert response is not None
    result = response["result"]
    assert "changes" not in result
    versions = {
        item["textDocument"]["uri"]: item["textDocument"]["version"]
        for item in result["documentChanges"]
    }
    assert versions == {
        caller_uri: 7,
        library.absolute().as_uri(): None,
    }
