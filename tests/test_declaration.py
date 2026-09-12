from __future__ import annotations

from typing import Any

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> dict[str, Any]:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None
    return result


def open_nova(
    server: NovaProductLanguageServer,
    uri: str,
    text: str,
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


def declaration(
    server: NovaProductLanguageServer,
    uri: str,
    request_id: int = 2,
) -> dict[str, Any]:
    result = server.handle(
        request(
            "textDocument/declaration",
            request_id,
            {
                "textDocument": {"uri": uri},
                "position": {"line": 0, "character": 16},
            },
        )
    )
    assert result is not None
    return result


def test_initialize_advertises_declaration_navigation() -> None:
    capabilities = initialize(NovaProductLanguageServer())["result"]["capabilities"]
    assert capabilities["declarationProvider"] is True


def test_declaration_reuses_exact_cross_file_binding() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    result = declaration(server, caller_uri)

    assert result["result"] == {
        "uri": declaration_uri,
        "range": {
            "start": {"line": 0, "character": 3},
            "end": {"line": 0, "character": 9},
        },
    }


def test_declaration_refuses_ambiguous_workspace_function() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/a.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/b.nova", "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert declaration(server, caller_uri)["result"] is None


def test_declaration_tracks_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert declaration(server, caller_uri)["result"] is not None

    server.handle(
        notify("textDocument/didClose", {"textDocument": {"uri": declaration_uri}})
    )
    assert declaration(server, caller_uri, 3)["result"] is None

    open_nova(server, declaration_uri, "fn target() {}\n", version=1)
    assert declaration(server, caller_uri, 4)["result"] is not None


def test_declaration_rejects_same_version_workspace_replacement() -> None:
    server = NovaProductLanguageServer()
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

    assert declaration(server, caller_uri) == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_declaration_honors_request_cancellation_before_publication() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    declaration_uri = "file:///workspace/library.nova"
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, declaration_uri, "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    real_checkpoint = server.requests.checkpoint
    checkpoints = 0

    def cancel_before_second_checkpoint(context) -> None:
        nonlocal checkpoints
        checkpoints += 1
        if checkpoints == 2:
            server.handle(notify("$/cancelRequest", {"id": 9}))
        real_checkpoint(context)

    server.requests.checkpoint = cancel_before_second_checkpoint  # type: ignore[method-assign]

    assert declaration(server, caller_uri, 9) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
