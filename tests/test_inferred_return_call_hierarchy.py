from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {"textDocument": {"callHierarchy": {}}}},
        )
    )
    assert result is not None
    assert result["result"]["capabilities"]["callHierarchyProvider"] is True


def open_nova(server: NovaProductLanguageServer, uri: str, text: str) -> None:
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
    server: NovaProductLanguageServer,
    uri: str,
    character: int,
    request_id: int = 2,
) -> dict:
    result = server.handle(
        request(
            "textDocument/prepareCallHierarchy",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": character},
            },
        )
    )
    assert result is not None
    return result


def test_call_hierarchy_surfaces_cross_file_inferred_return_details() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    library_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, library_uri, "fn target() { return 1; }\n")
    caller_text = "fn caller() { return target(); }\n"
    open_nova(server, caller_uri, caller_text)

    target = prepare(server, library_uri, 4)["result"][0]
    assert target["detail"] == "fn target() -> Int"

    incoming = server.handle(
        request("callHierarchy/incomingCalls", 3, {"item": target})
    )
    assert incoming is not None
    assert incoming["result"][0]["from"]["detail"] == "fn caller() -> Int"

    caller = prepare(server, caller_uri, 4, 4)["result"][0]
    outgoing = server.handle(
        request("callHierarchy/outgoingCalls", 5, {"item": caller})
    )
    assert outgoing is not None
    assert outgoing["result"][0]["to"]["detail"] == "fn target() -> Int"


def test_call_hierarchy_preserves_explicit_and_ambiguous_results() -> None:
    explicit = NovaProductLanguageServer()
    initialize(explicit)
    uri = "file:///workspace/explicit.nova"
    open_nova(explicit, uri, 'fn target() -> String { return "x"; }\n')
    assert prepare(explicit, uri, 4)["result"][0]["detail"] == (
        "fn target() -> String"
    )

    ambiguous = NovaProductLanguageServer()
    initialize(ambiguous)
    open_nova(
        ambiguous,
        "file:///workspace/a.nova",
        "fn target() { return 1; }\n",
    )
    open_nova(
        ambiguous,
        "file:///workspace/b.nova",
        "fn target() { return 1; }\n",
    )
    caller_uri = "file:///workspace/main.nova"
    text = "fn caller() { return target(); }\n"
    open_nova(ambiguous, caller_uri, text)
    assert prepare(ambiguous, caller_uri, text.index("target") + 1)["result"] == []


def test_inferred_call_hierarchy_recomputes_across_change_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/library.nova"
    open_nova(server, uri, "fn target() { return 1; }\n")
    assert prepare(server, uri, 4)["result"][0]["detail"] == "fn target() -> Int"

    server.handle(
        notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [{"text": 'fn target() { return "x"; }\n'}],
            },
        )
    )
    assert prepare(server, uri, 4, 3)["result"][0]["detail"] == (
        "fn target() -> String"
    )

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": uri}})
    )
    assert prepare(server, uri, 4, 4)["result"] == []

    open_nova(server, uri, "fn target() { return true; }\n")
    assert prepare(server, uri, 4, 5)["result"][0]["detail"] == (
        "fn target() -> Bool"
    )


def test_inferred_call_hierarchy_suppresses_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn target() { return 1; }\n")
    original = server.workspace_symbols.get(uri)
    assert original is not None
    real_commit = server.workspace_symbols.commit_snapshots_if_current

    def replace_then_commit(snapshots, callback):
        document = server.documents.get(uri)
        assert document is not None
        replacement = server.nova_adapter.publish(server, document)
        server.workspace_symbols.replace(replacement, expected=original)
        return real_commit(snapshots, callback)

    server.workspace_symbols.commit_snapshots_if_current = replace_then_commit  # type: ignore[method-assign]
    assert prepare(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_inferred_call_hierarchy_honors_cancellation_checkpoint() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    uri = "file:///workspace/main.nova"
    open_nova(server, uri, "fn target() { return 1; }\n")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            server.requests.cancel(context.request_id)
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert prepare(server, uri, 4) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
