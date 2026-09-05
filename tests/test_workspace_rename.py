from __future__ import annotations

from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: WorkspaceNovaLanguageServer) -> None:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None


def open_nova(server: WorkspaceNovaLanguageServer, uri: str, text: str) -> None:
    server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": text,
                }
            },
        )
    )


def rename(
    server: WorkspaceNovaLanguageServer,
    uri: str,
    request_id: int = 2,
    new_name: str = "renamed",
) -> dict:
    result = server.handle(
        request(
            "textDocument/rename",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 16},
                "newName": new_name,
            },
        )
    )
    assert result is not None
    return result


def test_workspace_rename_edits_unique_declaration_and_cross_file_calls() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    other_uri = "file:///workspace/other.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    open_nova(server, other_uri, "fn other() { target() }\n")

    result = rename(server, caller_uri)["result"]
    assert list(result["changes"]) == [declaration_uri, caller_uri, other_uri]
    assert result["changes"][declaration_uri] == [
        {
            "range": {
                "start": {"line": 0, "character": 3},
                "end": {"line": 0, "character": 9},
            },
            "newText": "renamed",
        }
    ]
    assert result["changes"][caller_uri][0]["range"]["start"] == {
        "line": 0,
        "character": 14,
    }
    assert result["changes"][other_uri][0]["range"]["start"] == {
        "line": 0,
        "character": 13,
    }


def test_workspace_rename_refuses_ambiguous_function_names() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert rename(server, caller_uri)["result"] is None


def test_workspace_rename_tracks_change_close_and_reopen() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert rename(server, caller_uri)["result"] is not None

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": declaration_uri, "version": 2},
                "contentChanges": [{"text": "fn other() {}\n"}],
            },
        )
    )
    assert rename(server, caller_uri, 3)["result"] is None

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": declaration_uri}})
    )
    open_nova(server, declaration_uri, "fn target() {}\n")
    assert rename(server, caller_uri, 4)["result"] is not None


def test_workspace_rename_suppresses_same_version_replacement() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    original = server.workspace_symbols.get(caller_uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(caller_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    result = rename(server, caller_uri)
    assert result == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
