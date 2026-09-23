from __future__ import annotations

from mini_language_server import LanguageServer, NovaProductLanguageServer, ServerState


def request(method: str, request_id: int = 1, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def notification(method: str, params: dict | None = None) -> dict:
    message = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(server: LanguageServer) -> None:
    response = server.handle(request("initialize"))
    assert response is not None
    assert "result" in response
    assert server.state is ServerState.RUNNING


def test_exit_request_before_initialize_is_rejected_without_terminating() -> None:
    server = LanguageServer()

    assert server.handle(request("exit", 7)) == {
        "jsonrpc": "2.0",
        "id": 7,
        "error": {
            "code": -32600,
            "message": "Exit must be a notification",
        },
    }
    assert server.state is ServerState.PRE_INITIALIZE
    assert server.exit_code is None


def test_exit_request_while_running_is_rejected_without_quiescing_session() -> None:
    server = LanguageServer()
    initialize(server)
    assert server._queue_notification("example/queued", {"value": 1})

    assert server.handle(request("exit", 8)) == {
        "jsonrpc": "2.0",
        "id": 8,
        "error": {
            "code": -32600,
            "message": "Exit must be a notification",
        },
    }
    assert server.state is ServerState.RUNNING
    assert server.exit_code is None
    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "example/queued",
            "params": {"value": 1},
        }
    ]
    assert server._queue_notification("example/after-rejection")


def test_exit_request_after_shutdown_does_not_replace_required_exit_notification() -> None:
    server = LanguageServer()
    initialize(server)
    assert server.handle(request("shutdown", 2)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }

    assert server.handle(request("exit", 9)) == {
        "jsonrpc": "2.0",
        "id": 9,
        "error": {
            "code": -32600,
            "message": "Exit must be a notification",
        },
    }
    assert server.state is ServerState.SHUTDOWN
    assert server.exit_code is None

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0


def test_initialized_request_is_rejected_but_notification_remains_valid() -> None:
    server = LanguageServer()
    initialize(server)

    assert server.handle(request("initialized", 10)) == {
        "jsonrpc": "2.0",
        "id": 10,
        "error": {
            "code": -32600,
            "message": "Initialized must be a notification",
        },
    }
    assert server.state is ServerState.RUNNING

    assert server.handle(notification("initialized")) is None
    assert server.state is ServerState.RUNNING


def test_duplicate_exit_is_terminally_idempotent_after_clean_shutdown() -> None:
    server = LanguageServer()
    initialize(server)
    server.handle(request("shutdown", 2))

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 0


def test_exit_before_shutdown_remains_failure_and_duplicate_exit_preserves_failure() -> None:
    server = LanguageServer()
    initialize(server)

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 1

    assert server.handle(notification("exit")) is None
    assert server.exit_code == 1


def test_final_product_cannot_bypass_exit_request_shape_guard() -> None:
    server = NovaProductLanguageServer()
    initialize(server)

    assert server.handle(request("exit", 11)) == {
        "jsonrpc": "2.0",
        "id": 11,
        "error": {
            "code": -32600,
            "message": "Exit must be a notification",
        },
    }
    assert server.state is ServerState.RUNNING
    assert server.exit_code is None
