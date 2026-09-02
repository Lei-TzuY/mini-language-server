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


def test_initialize_transitions_server_to_running() -> None:
    server = LanguageServer()
    response = server.handle(request("initialize", params={"capabilities": {}}))
    assert response is not None
    assert response["result"]["capabilities"] == {}
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
    server = LanguageServer()
    server.handle(request("initialize"))
    response = server.handle(request("example/unknown", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }


def test_shutdown_then_exit_is_clean() -> None:
    server = LanguageServer()
    server.handle(request("initialize"))
    response = server.handle(request("shutdown", request_id=2))
    assert response == {"jsonrpc": "2.0", "id": 2, "result": None}
    assert server.state is ServerState.SHUTDOWN
    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0


def test_exit_without_shutdown_is_failure() -> None:
    server = LanguageServer()
    server.handle(request("initialize"))
    server.handle(notification("exit"))
    assert server.state is ServerState.EXITED
    assert server.exit_code == 1


def test_repeated_initialize_is_rejected() -> None:
    server = LanguageServer()
    server.handle(request("initialize"))
    response = server.handle(request("initialize", request_id=2))
    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32600, "message": "Initialize request already received"},
    }
