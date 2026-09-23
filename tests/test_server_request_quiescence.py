from __future__ import annotations

from threading import Event, Thread

from mini_language_server import LanguageServer, NovaProductLanguageServer
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


def initialized(server: LanguageServer, params: object | None = None) -> None:
    response = server.handle(request("initialize", params=params))
    assert response is not None


def test_shutdown_closes_server_request_channel() -> None:
    server = LanguageServer()
    initialized(server)

    assert server.handle(request("shutdown", 2)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }
    assert server.state is ServerState.SHUTDOWN
    assert server._queue_server_request("workspace/diagnostic/refresh") is None
    assert server._pending_server_requests == {}
    assert server.drain_server_requests() == []


def test_exit_closes_server_request_channel() -> None:
    server = LanguageServer()
    initialized(server)

    assert server.handle(notification("exit")) is None
    assert server.state is ServerState.EXITED
    assert server._queue_server_request("workspace/codeLens/refresh") is None
    assert server._pending_server_requests == {}
    assert server.drain_server_requests() == []


def test_server_request_close_is_atomic_against_waiting_worker() -> None:
    server = LanguageServer()
    initialized(server)
    started = Event()
    queued: list[str | None] = []

    def worker() -> None:
        started.set()
        queued.append(server._queue_server_request("workspace/inlayHint/refresh"))

    with server._server_request_lock:
        thread = Thread(target=worker, name="late-server-request")
        thread.start()
        assert started.wait(2)
        assert server.handle(request("shutdown", 2)) == {
            "jsonrpc": "2.0",
            "id": 2,
            "result": None,
        }

    thread.join(2)
    assert not thread.is_alive()
    assert queued == [None]
    assert server._pending_server_requests == {}
    assert server.drain_server_requests() == []


def test_shutdown_waits_for_inflight_server_response_consumer() -> None:
    completion_started = Event()
    release_completion = Event()
    shutdown_done = Event()
    order: list[str] = []

    class BlockingServer(LanguageServer):
        def _server_request_completed(
            self,
            request_id: str,
            method: str,
            *,
            result: object,
            error: dict[str, object] | None,
        ) -> None:
            order.append("completion-start")
            completion_started.set()
            assert release_completion.wait(2)
            order.append("completion-end")

    server = BlockingServer()
    initialized(server)
    request_id = server._queue_server_request("workspace/configuration")
    assert request_id is not None
    server.drain_server_requests()

    def respond() -> None:
        server.handle({"jsonrpc": "2.0", "id": request_id, "result": []})

    response_thread = Thread(target=respond, name="server-response")
    response_thread.start()
    assert completion_started.wait(2)

    def shutdown() -> None:
        server.handle(request("shutdown", 2))
        order.append("shutdown")
        shutdown_done.set()

    shutdown_thread = Thread(target=shutdown, name="shutdown")
    shutdown_thread.start()
    assert not shutdown_done.wait(0.1)

    release_completion.set()
    response_thread.join(2)
    shutdown_thread.join(2)

    assert not response_thread.is_alive()
    assert not shutdown_thread.is_alive()
    assert shutdown_done.is_set()
    assert order == ["completion-start", "completion-end", "shutdown"]
    assert server.state is ServerState.SHUTDOWN
    assert server._pending_server_requests == {}


def test_tracing_does_not_observe_rejected_server_request() -> None:
    server = TracedLanguageServer()
    initialized(server)
    server.handle(notification("$/setTrace", {"value": "messages"}))
    server.drain_notifications()
    server.handle(request("shutdown", 2))
    server.drain_notifications()

    assert server._queue_server_request("workspace/diagnostic/refresh") is None
    assert server.drain_server_requests() == []
    assert server.drain_notifications() == []


def test_formatting_consumer_does_not_record_rejected_request_metadata() -> None:
    server = NovaProductLanguageServer()
    initialized(
        server,
        {
            "capabilities": {
                "workspace": {
                    "configuration": True,
                    "didChangeConfiguration": {"dynamicRegistration": True},
                }
            }
        },
    )
    server.handle(notification("initialized", {}))
    server.drain_server_requests()

    assert server.handle(request("shutdown", 2)) == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": None,
    }

    server._formatting_configuration_registration_attempted = False
    server._formatting_configuration_registration_request = None
    server._formatting_configuration_requests.clear()
    server._queue_formatting_configuration_registration()
    server._queue_formatting_configuration()

    assert server._formatting_configuration_registration_request is None
    assert server._formatting_configuration_requests == {}
    assert server.drain_server_requests() == []
