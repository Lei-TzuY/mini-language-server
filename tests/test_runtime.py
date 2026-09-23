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


class SequencedGatedBytesIO(BytesIO):
    def __init__(
        self,
        payload: bytes,
        *,
        gates: tuple[tuple[int, Event], ...],
    ) -> None:
        super().__init__(payload)
        self._gates = gates
        self._gate_index = 0

    def _wait_for_gate(self) -> None:
        while self._gate_index < len(self._gates):
            offset, gate = self._gates[self._gate_index]
            if self.tell() < offset:
                return
            assert gate.wait(timeout=5)
            self._gate_index += 1

    def readline(self, size: int = -1) -> bytes:
        self._wait_for_gate()
        return super().readline(size)

    def read(self, size: int = -1) -> bytes:
        self._wait_for_gate()
        return super().read(size)


class SignalingBytesIO(BytesIO):
    def __init__(self, *, signal_after_writes: int) -> None:
        super().__init__()
        self._signal_after_writes = signal_after_writes
        self._write_count = 0
        self.signaled = Event()

    def write(self, data: bytes) -> int:
        written = super().write(data)
        self._write_count += 1
        if self._write_count >= self._signal_after_writes:
            self.signaled.set()
        return written


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


class ActiveTransportAbortServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/wait-for-transport"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            context = self.requests.start(message["id"])
            try:
                self._queue_notification("test/stale-notification", {"value": 1})
                self._queue_server_request(
                    "workspace/configuration",
                    {"items": [{"section": "test"}]},
                )
                self.events.append("request-started")
                self.entered.set()
                assert context._cancelled.wait(timeout=5)
                self.events.append("request-cancelled")
                self.requests.checkpoint(context)
                raise AssertionError("transport-aborted request passed checkpoint")
            except RequestCancelled:
                return self._error(message["id"], -32800, "Request cancelled")
            finally:
                self.requests.finish(context)
        return super().handle(message)


def test_eof_aborts_active_request_and_discards_late_outbound_traffic() -> None:
    server = ActiveTransportAbortServer()
    payload = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/wait-for-transport",
            "params": {},
        },
    )
    input_stream = GatedBytesIO(
        payload,
        gate_offset=len(payload),
        gate=server.entered,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 1

    assert decoded(output_stream.getvalue()) == [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {
                "capabilities": {
                    "positionEncoding": "utf-16",
                    "textDocumentSync": 2,
                    "definitionProvider": True,
                    "referencesProvider": True,
                    "renameProvider": {"prepareProvider": True},
                    "hoverProvider": True,
                },
                "serverInfo": {
                    "name": "mini-language-server",
                    "version": "0.1.0",
                },
            },
        }
    ]
    assert server.events == ["request-started", "request-cancelled"]
    assert len(server.requests) == 0
    assert server.drain_notifications() == []
    assert server.drain_server_requests() == []


class PreStartTransportAbortServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.before_start = Event()
        self.release_start = Event()
        self.events: list[str] = []

    def abort_transport(
        self, *, active_request_id: str | int | None = None
    ) -> None:
        super().abort_transport(active_request_id=active_request_id)
        self.events.append("transport-aborted")
        self.release_start.set()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/start-after-abort"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("worker-entered")
            self.before_start.set()
            assert self.release_start.wait(timeout=5)
            context = self.requests.start(message["id"])
            try:
                self.events.append("context-started")
                self.requests.checkpoint(context)
                raise AssertionError("staged transport abort was not consumed")
            except RequestCancelled:
                self.events.append("context-cancelled")
                return self._error(message["id"], -32800, "Request cancelled")
            finally:
                self.requests.finish(context)
        return super().handle(message)


def test_eof_stages_abort_before_worker_registers_request_context() -> None:
    server = PreStartTransportAbortServer()
    payload = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/start-after-abort",
            "params": {},
        },
    )
    input_stream = GatedBytesIO(
        payload,
        gate_offset=len(payload),
        gate=server.before_start,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 1

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages] == [1]
    assert server.events == [
        "worker-entered",
        "transport-aborted",
        "context-started",
        "context-cancelled",
    ]
    assert len(server.requests) == 0


class LiveShutdownServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/wait-for-shutdown"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            context = self.requests.start(message["id"])
            try:
                self.events.append("request-started")
                self.entered.set()
                assert context._cancelled.wait(timeout=5)
                self.events.append("request-cancelled")
                self.requests.checkpoint(context)
                raise AssertionError("shutdown-retired request passed checkpoint")
            except RequestCancelled:
                return self._error(message["id"], -32800, "Request cancelled")
            finally:
                self.requests.finish(context)

        if (
            message.get("method") == "test/queued-after"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("queued-executed")
            return self._result(message["id"], {"ok": True})

        return super().handle(message)


def test_shutdown_interrupts_active_request_before_queued_client_request() -> None:
    server = LiveShutdownServer()
    first = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/wait-for-shutdown",
            "params": {},
        },
    )
    tail = framed(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "test/queued-after",
            "params": {},
        },
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
    responses = [message for message in messages if "id" in message]
    assert [message["id"] for message in responses] == [1, 2, 4, 3]
    assert responses[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert responses[2] == {"jsonrpc": "2.0", "id": 4, "result": None}
    assert responses[3] == {
        "jsonrpc": "2.0",
        "id": 3,
        "error": {"code": -32600, "message": "Server has shut down"},
    }
    assert server.events == ["request-started", "request-cancelled"]


class PreStartShutdownServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.before_start = Event()
        self.release_start = Event()
        self.events: list[str] = []

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/start-after-shutdown"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("worker-entered")
            self.before_start.set()
            assert self.release_start.wait(timeout=5)
            context = self.requests.start(message["id"])
            try:
                self.events.append("context-started")
                assert context._cancelled.wait(timeout=5)
                self.requests.checkpoint(context)
                raise AssertionError("live shutdown cancellation was not observed")
            except RequestCancelled:
                self.events.append("context-cancelled")
                return self._error(message["id"], -32800, "Request cancelled")
            finally:
                self.requests.finish(context)

        if message.get("method") == "shutdown":
            response = super().handle(message)
            self.events.append("shutdown-dispatched")
            self.release_start.set()
            return response

        return super().handle(message)


def test_shutdown_stages_cancel_when_worker_has_not_registered_context() -> None:
    server = PreStartShutdownServer()
    first = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/start-after-shutdown",
            "params": {},
        },
    )
    tail = framed(shutdown(3), exit_notification())
    input_stream = GatedBytesIO(
        first + tail,
        gate_offset=len(first),
        gate=server.before_start,
    )
    output_stream = BytesIO()

    assert run_session(input_stream, output_stream, server=server) == 0

    responses = [
        message
        for message in decoded(output_stream.getvalue())
        if "id" in message
    ]
    assert [message["id"] for message in responses] == [1, 2, 3]
    assert responses[1] == {
        "jsonrpc": "2.0",
        "id": 2,
        "error": {"code": -32800, "message": "Request cancelled"},
    }
    assert responses[2] == {"jsonrpc": "2.0", "id": 3, "result": None}
    assert server.events == [
        "worker-entered",
        "shutdown-dispatched",
        "context-started",
        "context-cancelled",
    ]

class LiveServerResponseRuntimeServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.entered = Event()
        self.response_received = Event()
        self.events: list[str] = []
        self.dependency_request_id: str | None = None
        self.dependency_result: object = None

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if message.get("method") == "initialized" and "id" not in message:
            response = super().handle(message)
            self.dependency_request_id = self._queue_server_request(
                "test/dependency",
                {"value": "needed"},
            )
            self.events.append("server-request-queued")
            return response

        if (
            message.get("method") == "test/wait-for-dependency"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("worker-started")
            self.entered.set()
            assert self.response_received.wait(timeout=5)
            self.events.append("worker-finished")
            return self._result(
                message["id"],
                {"dependency": self.dependency_result},
            )

        return super().handle(message)

    def _server_request_completed(
        self,
        request_id: str,
        method: str,
        *,
        result: Any,
        error: dict[str, Any] | None,
    ) -> None:
        super()._server_request_completed(
            request_id,
            method,
            result=result,
            error=error,
        )
        if request_id != self.dependency_request_id:
            return
        assert method == "test/dependency"
        assert error is None
        self.dependency_result = result
        self.events.append("server-response")
        self.response_received.set()


def test_runtime_delivers_sent_server_response_while_request_is_active() -> None:
    server = LiveServerResponseRuntimeServer()
    first = framed(
        initialize(),
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/wait-for-dependency",
            "params": {},
        },
    )
    dependency_response = framed(
        {
            "jsonrpc": "2.0",
            "id": "server:1",
            "result": {"value": 7},
        }
    )
    lifecycle_tail = framed(shutdown(3), exit_notification())
    output_stream = SignalingBytesIO(signal_after_writes=3)
    input_stream = SequencedGatedBytesIO(
        first + dependency_response + lifecycle_tail,
        gates=(
            (len(first), server.entered),
            (len(first) + len(dependency_response), output_stream.signaled),
        ),
    )

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages] == [
        1,
        "server:1",
        2,
        3,
    ]
    assert messages[1] == {
        "jsonrpc": "2.0",
        "id": "server:1",
        "method": "test/dependency",
        "params": {"value": "needed"},
    }
    assert messages[2] == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"dependency": {"value": 7}},
    }
    assert server.events == [
        "server-request-queued",
        "worker-started",
        "server-response",
        "worker-finished",
    ]

class WorkerCreatedServerRequestRuntimeServer(LanguageServer):
    def __init__(self) -> None:
        super().__init__()
        self.response_received = Event()
        self.events: list[str] = []
        self.dependency_request_id: str | None = None
        self.dependency_result: object = None

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        if (
            message.get("method") == "test/create-and-wait-for-dependency"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            self.events.append("worker-started")

            def own_request(request_id: str) -> None:
                self.dependency_request_id = request_id
                self.events.append("server-request-owned")

            self._queue_server_request(
                "test/worker-dependency",
                {"value": "needed"},
                on_queued=own_request,
            )
            self.events.append("worker-waiting")
            assert self.response_received.wait(timeout=5)
            self.events.append("worker-finished")
            return self._result(
                message["id"],
                {"dependency": self.dependency_result},
            )

        return super().handle(message)

    def _server_request_completed(
        self,
        request_id: str,
        method: str,
        *,
        result: Any,
        error: dict[str, Any] | None,
    ) -> None:
        super()._server_request_completed(
            request_id,
            method,
            result=result,
            error=error,
        )
        if request_id != self.dependency_request_id:
            return
        assert method == "test/worker-dependency"
        assert error is None
        self.dependency_result = result
        self.events.append("server-response")
        self.response_received.set()


def test_runtime_wakes_outbound_server_request_created_by_active_worker() -> None:
    server = WorkerCreatedServerRequestRuntimeServer()
    first = framed(
        initialize(),
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "test/create-and-wait-for-dependency",
            "params": {},
        },
    )
    dependency_response = framed(
        {
            "jsonrpc": "2.0",
            "id": "server:1",
            "result": {"value": 9},
        }
    )
    lifecycle_tail = framed(shutdown(3), exit_notification())
    output_stream = SignalingBytesIO(signal_after_writes=2)
    input_stream = GatedBytesIO(
        first + dependency_response + lifecycle_tail,
        gate_offset=len(first),
        gate=output_stream.signaled,
    )

    assert run_session(input_stream, output_stream, server=server) == 0

    messages = decoded(output_stream.getvalue())
    assert [message.get("id") for message in messages] == [
        1,
        "server:1",
        2,
        3,
    ]
    assert messages[1] == {
        "jsonrpc": "2.0",
        "id": "server:1",
        "method": "test/worker-dependency",
        "params": {"value": "needed"},
    }
    assert messages[2] == {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {"dependency": {"value": 9}},
    }
    assert server.events == [
        "worker-started",
        "server-request-owned",
        "worker-waiting",
        "server-response",
        "worker-finished",
    ]
    assert server._server_request_outbox_wakeup is None
