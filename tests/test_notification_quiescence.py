from __future__ import annotations

from threading import Event, Thread

from mini_language_server import LanguageServer
from mini_language_server.server import ServerState
from mini_language_server.tracing import TraceLanguageServerMixin


class TracedLanguageServer(TraceLanguageServerMixin, LanguageServer):
    pass


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


def initialized(server: LanguageServer) -> None:
    response = server.handle(request("initialize"))
    assert response is not None


def test_shutdown_discards_stale_notifications_but_preserves_remote_cancel() -> None:
    server = LanguageServer()
    initialized(server)
    server._queue_progress("work", {"kind": "report", "message": "stale"})
    server._queue_notification(
        "textDocument/publishDiagnostics",
        {"uri": "file:///workspace/main.nova", "diagnostics": []},
    )
    earlier = server._queue_server_request("workspace/configuration")
    server.drain_server_requests()
    assert server._cancel_server_request(earlier) is True

    sent = server._queue_server_request("workspace/diagnostic/refresh")
    assert server.drain_server_requests()[0]["id"] == sent
    unsent = server._queue_server_request("workspace/inlayHint/refresh")

    assert server.handle(request("shutdown", 2)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }
    assert server.state is ServerState.SHUTDOWN
    assert server.drain_server_requests() == []
    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": sent},
        }
    ]
    assert sent not in server._pending_server_requests
    assert unsent not in server._pending_server_requests
    assert server._queue_notification("window/logMessage", {"message": "late"}) is False
    assert server.drain_notifications() == []


def test_exit_clears_notification_outbox_and_rejects_late_traffic() -> None:
    server = LanguageServer()
    initialized(server)
    server._queue_progress("work", {"kind": "begin", "title": "stale"})
    sent = server._queue_server_request("workspace/codeLens/refresh")
    server.drain_server_requests()

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.exit_code == 1
    assert sent not in server._pending_server_requests
    assert server.drain_server_requests() == []
    assert server.drain_notifications() == []
    assert server._queue_notification("window/logMessage", {"message": "late"}) is False
    server._queue_progress("work", {"kind": "end"})
    assert server.drain_notifications() == []


def test_notification_close_is_atomic_against_waiting_worker() -> None:
    server = LanguageServer()
    initialized(server)
    started = Event()
    queued: list[bool] = []

    def worker() -> None:
        started.set()
        queued.append(
            server._queue_notification(
                "textDocument/publishDiagnostics",
                {"uri": "file:///workspace/main.nova", "diagnostics": []},
            )
        )

    with server._notification_lock:
        thread = Thread(target=worker, name="late-notification")
        thread.start()
        assert started.wait(2)
        assert server.handle(request("shutdown", 2)) == {
            "jsonrpc": "2.0",
            "id": 2,
            "result": None,
        }

    thread.join(2)
    assert not thread.is_alive()
    assert queued == [False]
    assert server.drain_notifications() == []


def test_tracing_cannot_bypass_terminal_notification_gate() -> None:
    server = TracedLanguageServer()
    initialized(server)
    server.handle(notification("$/setTrace", {"value": "messages"}))
    server.drain_notifications()

    sent = server._queue_server_request("workspace/diagnostic/refresh")
    server.drain_server_requests()
    server._queue_progress("work", {"kind": "report", "message": "stale"})

    assert server.handle(request("shutdown", 2)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }
    assert server.drain_notifications() == [
        {
            "jsonrpc": "2.0",
            "method": "$/cancelRequest",
            "params": {"id": sent},
        }
    ]

    server._emit_trace("late trace")
    assert server.drain_notifications() == []


def test_trace_on_exit_is_retired_with_other_notifications() -> None:
    server = TracedLanguageServer()
    initialized(server)
    server.handle(notification("$/setTrace", {"value": "verbose"}))
    server._queue_progress("work", {"kind": "report", "message": "queued"})

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server.drain_notifications() == []
