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
            {"capabilities": {"textDocument": {"callHierarchy": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["callHierarchyProvider"] is True


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


def prepare(
    server: WorkspaceNovaLanguageServer,
    uri: str,
    line: int,
    character: int,
    request_id: int = 2,
) -> dict:
    result = server.handle(
        request(
            "textDocument/prepareCallHierarchy",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
    )
    assert result is not None
    return result


def test_cross_file_call_hierarchy_reports_incoming_and_outgoing_calls() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target(value: Int) -> Int { value }\n")
    open_nova(
        server,
        caller_uri,
        "fn caller() {\n  target(1)\n  target(2)\n}\n",
    )

    target = prepare(server, library_uri, 0, 4)["result"]
    assert len(target) == 1
    assert target[0]["name"] == "target"
    assert target[0]["detail"] == "fn target(value: Int) -> Int"

    incoming = server.handle(
        request("callHierarchy/incomingCalls", 3, {"item": target[0]})
    )
    assert incoming is not None
    assert len(incoming["result"]) == 1
    assert incoming["result"][0]["from"]["name"] == "caller"
    assert [
        source_range["start"] for source_range in incoming["result"][0]["fromRanges"]
    ] == [
        {"line": 1, "character": 2},
        {"line": 2, "character": 2},
    ]

    caller = prepare(server, caller_uri, 0, 4, 4)["result"]
    outgoing = server.handle(
        request("callHierarchy/outgoingCalls", 5, {"item": caller[0]})
    )
    assert outgoing is not None
    assert len(outgoing["result"]) == 1
    assert outgoing["result"][0]["to"]["name"] == "target"
    assert len(outgoing["result"][0]["fromRanges"]) == 2


def test_call_hierarchy_refuses_ambiguous_targets_and_tracks_close_reopen() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    target_uri = "file:///workspace/a.nova"
    open_nova(server, target_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert len(prepare(server, caller_uri, 0, 16)["result"]) == 1

    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    assert prepare(server, caller_uri, 0, 16, 3)["result"] == []

    server.handle(
        notify(
            "textDocument/didClose",
            {"textDocument": {"uri": "file:///workspace/b.nova"}},
        )
    )
    assert len(prepare(server, caller_uri, 0, 16, 4)["result"]) == 1

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": target_uri}})
    )
    assert prepare(server, caller_uri, 0, 16, 5)["result"] == []
    open_nova(server, target_uri, "fn target() {}\n")
    assert len(prepare(server, caller_uri, 0, 16, 6)["result"]) == 1


def test_call_hierarchy_suppresses_same_version_workspace_replacement() -> None:
    server = WorkspaceNovaLanguageServer()
    initialize(server)
    target_uri = "file:///workspace/target.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, target_uri, "fn target() {}\n")
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
    result = prepare(server, caller_uri, 0, 16)
    assert result == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
