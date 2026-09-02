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
    assert response["result"]["capabilities"] == {"textDocumentSync": 2}
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


def open_document(server: LanguageServer, uri: str, text: str, version: int = 1) -> None:
    server.handle(
        notification(
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


def test_document_notifications_track_only_newer_snapshots() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "old", version=7)
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


def test_incremental_change_updates_document() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "hello world\n")

    server.handle(
        notification(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": 2},
                "contentChanges": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 6},
                            "end": {"line": 0, "character": 11},
                        },
                        "text": "Nova",
                    }
                ],
            },
        )
    )

    document = server.documents.get(uri)
    assert document is not None
    assert document.version == 2
    assert document.text == "hello Nova\n"


def test_invalid_incremental_batch_does_not_advance_version() -> None:
    server = initialized_server()
    uri = "file:///workspace/main.nova"
    open_document(server, uri, "abc")

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
                    },
                    {
                        "range": {
                            "start": {"line": 4, "character": 0},
                            "end": {"line": 4, "character": 0},
                        },
                        "text": "!",
                    },
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
    open_document(server, uri, "x")

    server.handle(notification("textDocument/didClose", {"textDocument": {"uri": uri}}))

    assert server.documents.get(uri) is None
