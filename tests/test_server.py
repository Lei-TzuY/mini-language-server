from mini_language_server.server import LanguageServer, ServerState


def request(method: str, request_id: int = 1, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: object | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialized_server() -> LanguageServer:
    server = LanguageServer()
    server.handle(request("initialize"))
    return server


def test_initialize_transitions_server_to_running() -> None:
    server = LanguageServer()
    response = server.handle(request("initialize", params={"capabilities": {}}))
    assert response is not None
    assert response["result"]["capabilities"] == {"textDocumentSync": 1}
    assert server.state is ServerState.RUNNING


def test_request_before_initialize_gets_server_not_initialized() -> None:
    response = LanguageServer().handle(request("textDocument/hover"))
    assert response == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": -32002, "message": "Server not initialized"},
    }


def test_notification_before_initialize_is_ignored() -> None:
    server = LanguageServer()
    assert server.handle(notification("initialized")) is None
    assert server.state is ServerState.PRE_INITIALIZE


def test_unknown_request_after_initialize_is_method_not_found() -> None:
    server = initialized_server()
    response = server.handle(request("example/unknown", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_shutdown_then_exit_is_clean() -> None:
    server = initialized_server()
    response = server.handle(request("shutdown", request_id=2))
    assert response == {"jsonrpc": "2.0", "id": 2, "result": None}
    assert server.state is ServerState.SHUTDOWN
    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0


def test_exit_without_shutdown_is_failure() -> None:
    server = initialized_server()
    server.handle(notification("exit"))
    assert server.state is ServerState.EXITED
    assert server.exit_code == 1


def test_repeated_initialize_is_rejected() -> None:
    server = initialized_server()
    response = server.handle(request("initialize", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32600, "message": "Initialize request already received"},
    }


def test_document_notifications_track_only_newer_full_sync_snapshots() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 7,
                    "text": "old",
                }
            },
        )
    )
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 8},
                "contentChanges": [{"text": "new"}],
            },
        )
    )
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 7},
                "contentChanges": [{"text": "stale"}],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 8
    assert document.text == "new"


def test_incremental_change_is_ignored_until_supported() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "abc",
                }
            },
        )
    )
    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 1},
                        },
                        "text": "z",
                    }
                ],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 1
    assert document.text == "abc"


def test_did_close_removes_document() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    server.handle(
        notification(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": "nova",
                    "version": 1,
                    "text": "x",
                }
            },
        )
    )

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))

    assert server.documents.get(uri) is None
