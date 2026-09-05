from __future__ import annotations

from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: WorkspaceNovaLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"hover": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["hoverProvider"] is True


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


def hover(server: WorkspaceNovaLanguageServer, uri: str, request_id: int = 2) -> dict:
    result = server.handle(
        request(
            "textDocument/hover",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 15},
            },
        )
    )
    assert result is not None
    return result


def test_workspace_hover_resolves_unique_cross_file_function() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/library.nova", "fn target() {}\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")

    assert hover(server, main_uri)["result"] == {
        "contents": {"kind": "plaintext", "value": "function target"},
        "range": {
            "start": {"line": 0, "character": 14},
            "end": {"line": 0, "character": 20},
        },
    }


def test_workspace_hover_does_not_guess_ambiguous_function() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    main_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")

    assert hover(server, main_uri)["result"] is None


def test_workspace_hover_tracks_change_and_close_reopen() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target() {}\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")
    assert hover(server, main_uri)["result"] is not None

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": library_uri, "version": 2},
                "contentChanges": [{"text": "fn other() {}\n"}],
            },
        )
    )
    assert hover(server, main_uri, 3)["result"] is None

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": library_uri}})
    )
    open_nova(server, library_uri, "fn target() {}\n")
    assert hover(server, main_uri, 4)["result"] is not None


def test_workspace_hover_suppresses_same_version_replacement() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    main_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target() {}\n")
    open_nova(server, main_uri, "fn caller() { target() }\n")
    original = server.workspace_symbols.get(library_uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(library_uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert hover(server, main_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
