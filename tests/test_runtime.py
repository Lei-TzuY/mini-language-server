from __future__ import annotations

from io import BytesIO
from typing import Any

from mini_language_server import LanguageServer, MessageReader, ServerState, encode_message
from mini_language_server.runtime import run_session


def framed(*messages: dict[str, Any]) -> bytes:
    return b"".join(encode_message(message) for message in messages)


def decoded(payload: bytes) -> list[dict[str, Any]]:
    reader = MessageReader(BytesIO(payload))
    messages: list[dict[str, Any]] = []
    while True:
        message = reader.read()
        if message is None:
            return messages
        messages.append(message)


def initialize(request_id: int = 1, *, capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {"capabilities": capabilities or {}},
    }


def shutdown(request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "shutdown",
        "params": None,
    }


def exit_notification() -> dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "exit"}


def test_clean_stdio_session_returns_zero_and_writes_lifecycle_responses() -> None:
    input_stream = BytesIO(
        framed(
            initialize(),
            {"jsonrpc": "2.0", "method": "initialized", "params": {}},
            shutdown(2),
            exit_notification(),
        )
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=LanguageServer()) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages] == [1, 2]
    assert messages[0]["result"]["serverInfo"]["name"] == "mini-language-server"
    assert messages[1] == {"jsonrpc": "2.0", "id": 2, "result": None}


class BatchServer(LanguageServer):
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/work"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self._queue_notification(
                "$/progress",
                {"token": "work", "value": {"kind": "report", "message": "running"}},
            )
            self._queue_server_request(
                "workspace/configuration",
                {"items": [{"section": "mini-language-server"}]},
            )
            return self._result(message["id"], {"done": True})
        return super().handle(message)


def test_runtime_flushes_notifications_then_server_requests_then_response() -> None:
    input_stream = BytesIO(
        framed(
            initialize(),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "test/work",
                "params": {},
            },
            {
                "jsonrpc": "2.0",
                "id": "server:1",
                "result": [{"enabled": True}],
            },
            shutdown(3),
            exit_notification(),
        )
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=BatchServer()) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("method") for message in messages] == [
        None,
        "$/progress",
        "workspace/configuration",
        None,
        None,
    ]
    assert messages[1]["params"]["token"] == "work"
    assert messages[2]["id"] == "server:1"
    assert messages[3] == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"done": True},
    }
    assert messages[4] == {"jsonrpc": "2.0", "id": 3, "result": None}


class ExitQueueServer(LanguageServer):
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("method") == "exit":
            self._queue_notification("test/stale", {"value": 1})
        return super().handle(message)


def test_exit_quiescence_discards_queued_notification_before_transport_flush() -> None:
    input_stream = BytesIO(framed(initialize(), exit_notification()))
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=ExitQueueServer()) == 1

    messages = decoded(output_stream.getvalue())
    assert len(messages) == 1
    assert messages[0]["id"] == 1


def test_eof_before_exit_is_transport_failure_after_flushing_prior_response() -> None:
    input_stream = BytesIO(framed(initialize()))
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=LanguageServer()) == 1

    messages = decoded(output_stream.getvalue())
    assert len(messages) == 1
    assert messages[0]["id"] == 1


def test_framing_failure_is_fail_closed_without_json_rpc_guessing() -> None:
    output_stream = BytesIO()

    assert run_session(
        BytesIO(b"Content-Length: nope\r\n\r\n"),
        output_stream,
        server=LanguageServer(),
    ) == 1
    assert output_stream.getvalue() == b""


def test_default_runtime_instantiates_final_nova_product() -> None:
    input_stream = BytesIO(
        framed(
            initialize(
                capabilities={
                    "textDocument": {
                        "signatureHelp": {},
                    }
                }
            ),
            shutdown(2),
            exit_notification(),
        )
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream) == 0

    capabilities = decoded(output_stream.getvalue())[0]["result"]["capabilities"]
    assert capabilities["signatureHelpProvider"] == {
        "triggerCharacters": ["(", ","],
    }
