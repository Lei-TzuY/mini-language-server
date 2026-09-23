from __future__ import annotations

import json
from typing import Any

from mini_language_server import LanguageServer, NovaProductLanguageServer
from mini_language_server.tracing import TraceLanguageServerMixin


class TracedLanguageServer(TraceLanguageServerMixin, LanguageServer):
    pass


class CancelResponseServer(TraceLanguageServerMixin, LanguageServer):
    def _handle_semantic_request(
        self, method: str, request_id: Any, params: Any
    ) -> dict[str, Any]:
        return self._error(request_id, -32800, "Request cancelled")


def request(method: str, request_id: int = 1, params: Any = None) -> dict[str, Any]:
    message: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
    }
    if params is not None:
        message["params"] = params
    return message


def notify(method: str, params: Any = None) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    return message


def initialize(server: LanguageServer, params: dict[str, Any] | None = None) -> None:
    response = server.handle(
        request(
            "initialize",
            1,
            {"capabilities": {}} if params is None else params,
        )
    )
    assert response is not None and "result" in response


def set_trace(server: Any, value: str) -> None:
    assert server.handle(notify("$/setTrace", {"value": value})) is None


def trace_messages(server: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in server.drain_notifications()
        if item.get("method") == "$/logTrace"
    ]


def test_messages_trace_wraps_request_and_error_response() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "messages")

    response = server.handle(request("workspace/unknown", 2, {}))

    assert response == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32601, "message": "Method not found"},
    }
    traces = trace_messages(server)
    assert [item["params"] for item in traces] == [
        {"message": "client -> server request workspace/unknown id=2"},
        {"message": "server -> client error response id=2 code=-32601"},
    ]


def test_verbose_trace_records_structure_without_source_text() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "verbose")
    secret = "fn secret_password() { return; }"

    assert server.handle(
        notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": "file:///workspace/private.nova",
                    "languageId": "nova",
                    "version": 1,
                    "text": secret,
                }
            },
        )
    ) is None

    traces = trace_messages(server)
    assert len(traces) == 1
    params = traces[0]["params"]
    assert params["message"] == "client -> server notification textDocument/didOpen"
    assert secret not in params["verbose"]
    assert "private.nova" not in params["verbose"]
    summary = json.loads(params["verbose"])
    assert summary["method"] == "textDocument/didOpen"
    assert summary["params"] == {
        "type": "object",
        "keys": ["textDocument"],
    }


def test_trace_off_stops_future_trace_notifications() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "messages")

    server.handle(notify("initialized", {}))
    assert len(trace_messages(server)) == 1

    set_trace(server, "off")
    response = server.handle(request("workspace/unknown", 2, {}))
    assert response is not None
    assert trace_messages(server) == []


def test_invalid_set_trace_value_preserves_current_mode() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "messages")

    assert server.handle(notify("$/setTrace", {"value": "everything"})) is None
    server.handle(notify("initialized", {}))

    traces = trace_messages(server)
    assert len(traces) == 1
    assert server.trace_value == "messages"


def test_progress_notification_and_server_request_are_traced() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "verbose")

    server._queue_progress("token", {"kind": "report", "message": "halfway"})
    request_id = server._queue_server_request(
        "workspace/configuration",
        {"items": [{"section": "mini-language-server.formatting"}]},
    )

    notifications = server.drain_notifications()
    assert notifications[0] == {
        "jsonrpc": "2.0",
        "method": "$/progress",
        "params": {
            "token": "token",
            "value": {"kind": "report", "message": "halfway"},
        },
    }
    assert notifications[1]["method"] == "$/logTrace"
    assert notifications[1]["params"]["message"] == (
        "server -> client notification $/progress"
    )
    assert notifications[2]["method"] == "$/logTrace"
    assert notifications[2]["params"]["message"] == (
        f"server -> client request workspace/configuration id={request_id}"
    )
    queued = server.drain_server_requests()
    assert queued == [
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "workspace/configuration",
            "params": {
                "items": [{"section": "mini-language-server.formatting"}],
            },
        }
    ]


def test_client_response_to_server_request_is_traced() -> None:
    server = TracedLanguageServer()
    initialize(server)
    set_trace(server, "messages")
    request_id = server._queue_server_request("workspace/configuration")
    server.drain_server_requests()
    trace_messages(server)

    assert server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": [],
        }
    ) is None

    traces = trace_messages(server)
    assert [item["params"]["message"] for item in traces] == [
        f"client -> server response id={request_id}"
    ]


def test_cancelled_response_code_is_visible_in_trace() -> None:
    server = CancelResponseServer()
    initialize(server)
    set_trace(server, "messages")

    response = server.handle(
        request(
            "textDocument/definition",
            7,
            {
                "textDocument": {"uri": "file:///workspace/main.nova"},
                "position": {"line": 0, "character": 0},
            },
        )
    )

    assert response is not None
    assert response["error"]["code"] == -32800
    traces = trace_messages(server)
    assert [item["params"]["message"] for item in traces] == [
        "client -> server request textDocument/definition id=7",
        "server -> client error response id=7 code=-32800",
    ]


def test_final_product_workspace_symbol_request_is_wrapped_by_trace() -> None:
    server = NovaProductLanguageServer()
    initialize(
        server,
        {
            "capabilities": {
                "workspace": {
                    "symbol": {},
                }
            }
        },
    )
    set_trace(server, "messages")

    response = server.handle(
        request(
            "workspace/symbol",
            9,
            {"query": ""},
        )
    )

    assert response is not None
    assert response["result"] == []
    traces = trace_messages(server)
    assert [item["params"]["message"] for item in traces] == [
        "client -> server request workspace/symbol id=9",
        "server -> client response id=9",
    ]
