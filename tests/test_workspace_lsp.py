from __future__ import annotations

import pytest

from mini_language_server.workspace import WorkspaceIndexError
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(
    server: WorkspaceNovaLanguageServer, *, workspace_symbol: bool = True
) -> dict:
    workspace = {"symbol": {}} if workspace_symbol else {}
    result = server.handle(
        request("initialize", 1, {"capabilities": {"workspace": workspace}})
    )
    assert result is not None
    return result


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


def test_workspace_symbol_capability_is_negotiated() -> None:
    enabled = initialize(WorkspaceNovaLanguageServer())
    assert enabled["result"]["capabilities"]["workspaceSymbolProvider"] is True

    disabled = initialize(WorkspaceNovaLanguageServer(), workspace_symbol=False)
    assert "workspaceSymbolProvider" not in disabled["result"]["capabilities"]


def test_workspace_symbol_search_is_deterministic_across_open_nova_documents() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    open_nova(server, "file:///workspace/z.nova", "fn Zebra() {}\n")
    open_nova(server, "file:///workspace/a.nova", "fn alpha() {}\nfn beta() {}\n")

    result = server.handle(request("workspace/symbol", 2, {"query": "a"}))
    assert result is not None
    assert [(item["name"], item["location"]["uri"]) for item in result["result"]] == [
        ("alpha", "file:///workspace/a.nova"),
        ("beta", "file:///workspace/a.nova"),
        ("Zebra", "file:///workspace/z.nova"),
    ]
    assert all(item["kind"] == 12 for item in result["result"])


def test_workspace_replacement_and_close_remove_superseded_contributions() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn old() {}\n")

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": "fn current() {}\n"}],
            },
        )
    )
    old = server.handle(request("workspace/symbol", 2, {"query": "old"}))
    current = server.handle(request("workspace/symbol", 3, {"query": "current"}))
    assert old is not None and old["result"] == []
    assert current is not None
    assert [item["name"] for item in current["result"]] == ["current"]

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": uri}}))
    closed = server.handle(request("workspace/symbol", 4, {"query": "current"}))
    assert closed is not None and closed["result"] == []


def test_workspace_symbol_suppresses_same_version_replacement() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
    original = server.workspace_symbols.get(uri)
    assert original is not None

    real_commit = server.workspace_symbols.commit_if_current

    def replace_then_commit(declarations, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(declarations, callback)

    server.workspace_symbols.commit_if_current = replace_then_commit  # type: ignore[method-assign]
    result = server.handle(request("workspace/symbol", 2, {"query": "run"}))
    assert result == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_workspace_index_commit_rejects_superseded_parent() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn run() {}\n")
    declarations = server.workspace_symbols.search("run")
    original = server.workspace_symbols.get(uri)
    document = server.documents.get(uri)
    assert original is not None and document is not None
    replacement = server.nova_adapter.publish(server, document)
    server.workspace_symbols.replace(replacement, expected=original)

    with pytest.raises(WorkspaceIndexError, match="replaced"):
        server.workspace_symbols.commit_if_current(declarations, lambda: None)
