from __future__ import annotations

from io import BytesIO
from threading import Event
from typing import Any

import pytest

from mini_language_server import (
    LanguageServer,
    MessageReader,
    NovaProductLanguageServer,
    ServerState,
    encode_message,
)
from mini_language_server.cancellation import RequestCancelled, StaleRequest
from mini_language_server.runtime import run_session
from mini_language_server.workspace_folders import WorkspaceFolderError
from mini_language_server.workspace_lsp import WorkspaceNovaLanguageServer


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


def initialize(
    request_id: int = 1,
    *,
    capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
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

class GatedBytesIO(BytesIO):
    def __init__(self, payload: bytes, *, gate_offset: int, gate: Event) -> None:
        super().__init__(payload)
        self._gate_offset = gate_offset
        self._gate = gate
        self._gate_passed = False

    def _wait_for_gate(self) -> None:
        if self._gate_passed or self.tell() < self._gate_offset:
            return
        assert self._gate.wait(timeout=5)
        self._gate_passed = True

    def readline(self, size: int = -1) -> bytes:
        self._wait_for_gate()
        return super().readline(size)

    def read(self, size: int = -1) -> bytes:
        self._wait_for_gate()
        return super().read(size)


class LiveCancellationServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/slow"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            context = self.requests.start(message["id"])
            try:
                self.events.append("slow-start")
                self.entered.set()
                assert context._cancelled.wait(timeout=5)
                self.events.append("slow-cancelled")
                self.requests.checkpoint(context)
                raise AssertionError("cancelled request passed checkpoint")
            except RequestCancelled:
                return self._error(message["id"], -32800, "Request cancelled")
            finally:
                self.requests.finish(context)

        if message.get("method") == "test/mutate" and "id" not in message:
            self.events.append("mutate")
            return None

        return super().handle(message)


def cancel_notification(request_id: int) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "$/cancelRequest",
        "params": {"id": request_id},
    }


def test_runtime_reads_cancel_while_request_is_actively_dispatching() -> None:
    server = LiveCancellationServer()
    first = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/slow",
            "params": {},
        },
    )
    tail = framed(
        {"jsonrpc": "2.0", "method": "test/mutate", "params": {}},
        cancel_notification(2),
        shutdown(3),
        exit_notification(),
    )
    input_stream = GatedBytesIO(
        first + tail,
        gate_offset=len(first),
        gate=server.entered,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages] == [1, 2, 3]
    assert messages[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert server.events == ["slow-start", "slow-cancelled", "mutate"]


def test_runtime_cancel_staged_before_request_start_does_not_poison_reused_id() -> None:
    server = LiveCancellationServer()
    input_stream = BytesIO(
        framed(
            initialize(),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "test/slow",
                "params": {},
            },
            cancel_notification(2),
            shutdown(3),
            exit_notification(),
        )
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0
    assert server.events[:2] == ["slow-start", "slow-cancelled"]

    context = server.requests.start(2)
    assert context.cancelled is False
    assert server.requests.finish(context) is True

class OSErrorStream(BytesIO):
    def readline(self, size: int = -1) -> bytes:
        raise OSError("stdin failed")


def test_stdio_read_error_fails_closed_in_background_reader() -> None:
    output_stream = BytesIO()

    assert run_session(
        OSErrorStream(),
        output_stream,
        server=LanguageServer(),
    ) == 1
    assert output_stream.getvalue() == b""

class LiveMutationServer(LanguageServer):
    def __init__(self, uri: str) -> None:
        super().__init__()
        self.uri = uri
        self.entered = Event()
        self.changed = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/snapshot"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            context = self.requests.start(message["id"], uri=self.uri)
            try:
                self.events.append("snapshot-start")
                self.entered.set()
                assert self.changed.wait(timeout=5)
                self.requests.checkpoint(context)
                raise AssertionError("stale request passed checkpoint")
            except StaleRequest:
                self.events.append("snapshot-stale")
                return self._error(message["id"], -32801, "Content modified")
            finally:
                self.requests.finish(context)

        if (
            message.get("method") == "test/after"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("after")
            return self._result(message["id"], {"ok": True})

        response = super().handle(message)
        if message.get("method") == "textDocument/didChange":
            self.events.append("didChange")
            self.changed.set()
        return response


def open_notification(uri: str, *, version: int, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "textDocument/didOpen",
        "params": {
            "textDocument": {
                "uri": uri,
                "languageId": "nova",
                "version": version,
                "text": text,
            }
        },
    }


def change_notification(uri: str, *, version: int, text: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "textDocument/didChange",
        "params": {
            "textDocument": {"uri": uri, "version": version},
            "contentChanges": [{"text": text}],
        },
    }


def test_runtime_dispatches_document_change_while_request_is_active() -> None:
    uri = "file:///workspace/main.nova"
    server = LiveMutationServer(uri)
    first = framed(
        initialize(),
        open_notification(uri, version=1, text="fn main() {}\n"),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/snapshot",
            "params": {},
        },
    )
    tail = framed(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "test/after",
            "params": {},
        },
        change_notification(uri, version=2, text="fn main() { let x = 1; }\n"),
        shutdown(4),
        exit_notification(),
    )
    input_stream = GatedBytesIO(
        first + tail,
        gate_offset=len(first),
        gate=server.entered,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages if "id" in message] == [
        1,
        2,
        3,
        4,
    ]
    stale = next(message for message in messages if message.get("id") == 2)
    assert stale == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert server.documents.get(uri) is not None
    assert server.documents.get(uri).version == 2
    assert server.events == [
        "snapshot-start",
        "didChange",
        "snapshot-stale",
        "after",
    ]


class WorkerFailureServer(LanguageServer):
    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/explode"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            raise RuntimeError("worker exploded")
        return super().handle(message)


def test_runtime_propagates_worker_programming_errors() -> None:
    input_stream = BytesIO(
        framed(
            initialize(),
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "test/explode",
                "params": {},
            },
        )
    )

    with pytest.raises(RuntimeError, match="worker exploded"):
        run_session(input_stream, BytesIO(), server=WorkerFailureServer())


class LiveWorkspaceMutationServer(WorkspaceNovaLanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.changed = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/workspace-snapshot"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            context = self.requests.start(message["id"])
            scope = self.workspace_folders.snapshot()
            try:
                self.events.append("workspace-start")
                self.entered.set()
                assert self.changed.wait(timeout=5)
                self.requests.checkpoint(context)
                self.workspace_folders.commit_if_current(
                    scope.generation,
                    lambda: None,
                )
                raise AssertionError("stale workspace scope passed commit guard")
            except WorkspaceFolderError:
                self.events.append("workspace-stale")
                return self._error(message["id"], -32801, "Content modified")
            finally:
                self.requests.finish(context)

        if (
            message.get("method") == "test/after-workspace"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("after-workspace")
            return self._result(message["id"], {"ok": True})

        before = self.workspace_folders.generation
        response = super().handle(message)
        if (
            message.get("method") == "workspace/didChangeWorkspaceFolders"
            and self.workspace_folders.generation != before
        ):
            self.events.append("workspace-change")
            self.changed.set()
        return response


def workspace_initialize(request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "capabilities": {
                "workspace": {"workspaceFolders": True},
            },
            "workspaceFolders": [
                {"uri": "file:///workspace/a", "name": "a"},
            ],
        },
    }


def workspace_folder_change() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "workspace/didChangeWorkspaceFolders",
        "params": {
            "event": {
                "added": [{"uri": "file:///workspace/b", "name": "b"}],
                "removed": [],
            }
        },
    }


def test_runtime_dispatches_workspace_folder_change_while_request_is_active() -> None:
    server = LiveWorkspaceMutationServer()
    first = framed(
        workspace_initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/workspace-snapshot",
            "params": {},
        },
    )
    tail = framed(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "test/after-workspace",
            "params": {},
        },
        workspace_folder_change(),
        shutdown(4),
        exit_notification(),
    )
    input_stream = GatedBytesIO(
        first + tail,
        gate_offset=len(first),
        gate=server.entered,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages if "id" in message] == [
        1,
        2,
        3,
        4,
    ]
    stale = next(message for message in messages if message.get("id") == 2)
    assert stale == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert server.workspace_folders.generation == 2
    assert [folder.uri for folder in server.workspace_folders.folders()] == [
        "file:///workspace/a",
        "file:///workspace/b",
    ]
    assert server.events == [
        "workspace-start",
        "workspace-change",
        "workspace-stale",
        "after-workspace",
    ]


class LiveFormattingConfigurationServer(NovaProductLanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.changed = Event()
        self.events: list[str] = []

    def _format_nova_document(
        self,
        text: str,
        *,
        tab_size: int,
        insert_spaces: bool,
    ) -> str:
        self.events.append("format-start")
        self.entered.set()
        assert self.changed.wait(timeout=5)
        return super()._format_nova_document(
            text,
            tab_size=tab_size,
            insert_spaces=insert_spaces,
        )

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/after-format"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("after-format")
            return self._result(message["id"], {"ok": True})

        response = super().handle(message)
        if message.get("method") == "workspace/didChangeConfiguration":
            self.events.append("configuration-change")
            self.changed.set()
        return response


def formatting_runtime_initialize(request_id: int = 1) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "initialize",
        "params": {
            "capabilities": {
                "textDocument": {
                    "synchronization": {"willSaveWaitUntil": True},
                },
                "workspace": {"configuration": True},
            }
        },
    }


def did_change_configuration() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "method": "workspace/didChangeConfiguration",
        "params": {"settings": {"ignored": True}},
    }


def test_runtime_dispatches_configuration_change_while_save_request_is_active() -> None:
    uri = "file:///workspace/main.nova"
    server = LiveFormattingConfigurationServer()
    first = framed(
        formatting_runtime_initialize(),
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        open_notification(
            uri,
            version=1,
            text="fn main() {\nreturn 1\n}\n",
        ),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "textDocument/willSaveWaitUntil",
            "params": {"textDocument": {"uri": uri}, "reason": 1},
        },
    )
    tail = framed(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "test/after-format",
            "params": {},
        },
        did_change_configuration(),
        shutdown(4),
        exit_notification(),
    )
    input_stream = GatedBytesIO(
        first + tail,
        gate_offset=len(first),
        gate=server.entered,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    stale = next(message for message in messages if message.get("id") == 2)
    assert stale == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32801, "message": "Content modified"},
    }
    assert server.events == [
        "format-start",
        "configuration-change",
        "after-format",
    ]

    server_requests = [
        message
        for message in messages
        if message.get("method") == "workspace/configuration"
    ]
    assert [message["id"] for message in server_requests] == ["server:1", "server:2"]
    assert any(
        message.get("method") == "$/cancelRequest"
        and message.get("params") == {"id": "server:1"}
        for message in messages
    )
