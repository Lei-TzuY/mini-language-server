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


def definition(
    server: WorkspaceNovaLanguageServer, uri: str, request_id: int = 2
) -> dict:
    result = server.handle(
        request(
            "textDocument/definition",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 16},
            },
        )
    )
    assert result is not None
    return result


def test_unique_workspace_function_supports_cross_file_definition_and_references() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    target = definition(server, caller_uri)
    assert target["result"]["uri"] == declaration_uri
    assert target["result"]["range"]["start"] == {"line": 0, "character": 3}

    references = server.handle(
        request(
            "textDocument/references",
            3,
            {
                "textDocument": {"uri": caller_uri},
                "position": {"line": 0, "character": 16},
                "context": {"includeDeclaration": True},
            },
        )
    )
    assert references is not None
    actual = [
        (item["uri"], item["range"]["start"]["character"])
        for item in references["result"]
    ]
    assert actual == [(declaration_uri, 3), (caller_uri, 14)]


def test_workspace_navigation_refuses_ambiguous_function_names() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert definition(server, caller_uri)["result"] is None
    references = server.handle(
        request(
            "textDocument/references",
            3,
            {
                "textDocument": {"uri": caller_uri},
                "position": {"line": 0, "character": 16},
                "context": {"includeDeclaration": False},
            },
        )
    )
    assert references is not None and references["result"] == []


def test_workspace_navigation_tracks_change_close_and_reopen() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert definition(server, caller_uri)["result"] is not None

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": declaration_uri, "version": 2},
                "contentChanges": [{"text": "fn other() {}\n"}],
            },
        )
    )
    assert definition(server, caller_uri, 3)["result"] is None

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": declaration_uri}})
    )
    open_nova(server, declaration_uri, "fn target() {}\n")
    assert definition(server, caller_uri, 4)["result"] is not None


def test_workspace_navigation_suppresses_same_version_replacement() -> None:
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
    result = definition(server, caller_uri)
    assert result == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
