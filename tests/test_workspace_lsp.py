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

def initialize_with_folders(
    server: WorkspaceNovaLanguageServer,
    folders: list[dict[str, str]],
) -> dict:
    result = server.handle(
        request(
            "initialize",
            1,
            {
                "capabilities": {
                    "workspace": {
                        "symbol": {},
                        "workspaceFolders": True,
                    }
                },
                "workspaceFolders": folders,
            },
        )
    )
    assert result is not None
    return result


def diagnostic_codes(server: WorkspaceNovaLanguageServer, uri: str) -> list[str]:
    snapshot = server.diagnostics.get(uri)
    assert snapshot is not None
    return [
        diagnostic.code
        for diagnostic in snapshot.diagnostics
        if diagnostic.code is not None
    ]


def test_workspace_folder_capability_and_initial_scope_are_negotiated() -> None:
    server = WorkspaceNovaLanguageServer()
    response = initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )

    assert response["result"]["capabilities"]["workspace"]["workspaceFolders"] == {
        "supported": True,
        "changeNotifications": True,
    }

    open_nova(server, "file:///workspace/a/in.nova", "fn inside() {}\n")
    open_nova(server, "file:///workspace/b/out.nova", "fn outside() {}\n")

    result = server.handle(request("workspace/symbol", 2, {"query": ""}))
    assert result is not None
    assert [
        (item["name"], item["location"]["uri"])
        for item in result["result"]
    ] == [("inside", "file:///workspace/a/in.nova")]


def test_workspace_folder_add_and_remove_rebind_open_cross_file_calls() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )
    provider = "file:///workspace/b/provider.nova"
    caller = "file:///workspace/a/caller.nova"
    open_nova(server, provider, "fn helper() {}\n")
    open_nova(server, caller, "fn main() { helper() }\n")

    assert "nova.unresolved-function" in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is None

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": "file:///workspace/b", "name": "b"}],
                    "removed": [],
                }
            },
        )
    )

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is server.semantics.get(provider)

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [],
                    "removed": [{"uri": "file:///workspace/b", "name": "b"}],
                }
            },
        )
    )

    assert "nova.unresolved-function" in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is None

    server.handle(
        notify(
            "workspace/didChangeWorkspaceFolders",
            {
                "event": {
                    "added": [{"uri": "file:///workspace/b", "name": "b"}],
                    "removed": [],
                }
            },
        )
    )

    assert "nova.unresolved-function" not in diagnostic_codes(server, caller)
    assert server.workspace_symbols.get(provider) is server.semantics.get(provider)


def test_out_of_scope_document_keeps_local_semantics_but_not_workspace_membership() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/a", "name": "a"}],
    )
    uri = "file:///outside/main.nova"
    open_nova(server, uri, "fn local() {}\n")

    assert server.semantics.get(uri) is not None
    assert server.workspace_symbols.get(uri) is None

    result = server.handle(request("workspace/symbol", 2, {"query": "local"}))
    assert result is not None
    assert result["result"] == []


def test_workspace_folder_path_boundary_does_not_match_similar_prefix() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize_with_folders(
        server,
        [{"uri": "file:///workspace/app", "name": "app"}],
    )
    open_nova(server, "file:///workspace/app/main.nova", "fn inside() {}\n")
    open_nova(
        server,
        "file:///workspace/application/main.nova",
        "fn prefix_collision() {}\n",
    )

    result = server.handle(request("workspace/symbol", 2, {"query": ""}))
    assert result is not None
    assert [item["name"] for item in result["result"]] == ["inside"]
