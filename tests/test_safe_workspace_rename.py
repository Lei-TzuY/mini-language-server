from __future__ import annotations

from mini_language_server import NovaProductLanguageServer


def request(method: str, request_id: int, params: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def notify(method: str, params: dict) -> dict:
    return {"jsonrpc": "2.0", "method": method, "params": params}


def initialize(server: NovaProductLanguageServer) -> None:
    result = server.handle(request("initialize", 1, {"capabilities": {}}))
    assert result is not None


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


def rename(
    server: NovaProductLanguageServer,
    uri: str,
    new_name: str,
    request_id: int = 2,
) -> dict:
    response = server.handle(
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
    assert response is not None
    return response


def test_product_rename_rejects_existing_workspace_function_name() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/target.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/existing.nova", "fn existing() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert rename(server, caller_uri, "existing") == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {
            "code": -32803,
            "message": "Rename would conflict with existing function 'existing'",
        },
    }


def test_product_same_name_workspace_rename_is_empty_edit() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/target.nova", "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")

    assert rename(server, caller_uri, "target")["result"] == {"changes": {}}


def test_product_rename_conflict_tracks_close_and_reopen() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    conflict_uri = "file:///workspace/existing.nova"
    open_nova(server, "file:///workspace/target.nova", "fn target() {}\n")
    open_nova(server, conflict_uri, "fn existing() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    assert "error" in rename(server, caller_uri, "existing")

    server.handle(notify("textDocument/didClose", {"textDocument": {"uri": conflict_uri}}))
    assert "changes" in rename(server, caller_uri, "existing", 3)["result"]

    open_nova(server, conflict_uri, "fn existing() {}\n")
    assert rename(server, caller_uri, "existing", 4)["error"]["code"] == -32803


def test_product_rename_conflict_suppresses_same_version_replacement() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/target.nova", "fn target() {}\n")
    open_nova(server, "file:///workspace/existing.nova", "fn existing() {}\n")
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
    assert rename(server, caller_uri, "existing") == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }


def test_product_workspace_rename_remains_cancellable() -> None:
    server = NovaProductLanguageServer()
    initialize(server)
    caller_uri = "file:///workspace/main.nova"
    open_nova(server, "file:///workspace/target.nova", "fn target() {}\n")
    open_nova(server, caller_uri, "fn caller() { target() }\n")
    real_checkpoint = server.requests.checkpoint
    cancelled = False

    def cancel_then_checkpoint(context):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            assert server.requests.cancel(context.request_id) is True
        real_checkpoint(context)

    server.requests.checkpoint = cancel_then_checkpoint  # type: ignore[method-assign]
    assert rename(server, caller_uri, "renamed") == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
