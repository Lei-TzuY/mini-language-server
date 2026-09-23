"""Executable cancellation-aware stdio host for the final Nova language server."""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, BinaryIO

from .completion_pipeline import NovaProductLanguageServer
from .protocol import FramingError, MessageReader, encode_message
from .server import LanguageServer, ServerState

_TRANSPORT_FAILURE = object()


def _write_batch(
    output_stream: BinaryIO,
    messages: tuple[dict[str, object], ...],
) -> None:
    if not messages:
        return
    for message in messages:
        output_stream.write(encode_message(message))
    output_stream.flush()


def _drain_after_dispatch(
    server: LanguageServer,
    response: dict[str, object] | None,
) -> tuple[dict[str, object], ...]:
    """Drain one deterministic transport batch after an inbound message.

    Notifications are emitted before tracked server-to-client requests, and a direct
    response is emitted last. This keeps progress/cancellation/trace notifications
    generated while handling a request ahead of that request's terminal response.
    """
    batch: list[dict[str, object]] = []
    batch.extend(server.drain_notifications())
    batch.extend(server.drain_server_requests())
    if response is not None:
        batch.append(response)
    return tuple(batch)


def _client_request_id(message: dict[str, Any]) -> str | int | None:
    request_id = message.get("id")
    if (
        isinstance(message.get("method"), str)
        and isinstance(request_id, str | int)
        and not isinstance(request_id, bool)
        and not (isinstance(request_id, str) and not request_id)
    ):
        return request_id
    return None


def _cancel_target(message: dict[str, Any]) -> str | int | None:
    if (
        message.get("jsonrpc") != "2.0"
        or message.get("method") != "$/cancelRequest"
        or "id" in message
    ):
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    request_id = params.get("id")
    if (
        not isinstance(request_id, str | int)
        or isinstance(request_id, bool)
        or (isinstance(request_id, str) and not request_id)
    ):
        return None
    return request_id


def _is_exit_notification(message: dict[str, Any]) -> bool:
    return (
        message.get("jsonrpc") == "2.0"
        and message.get("method") == "exit"
        and "id" not in message
    )


def _is_live_snapshot_mutation(message: dict[str, Any]) -> bool:
    """Return whether one notification may invalidate active exact snapshots."""
    return (
        message.get("jsonrpc") == "2.0"
        and "id" not in message
        and message.get("method")
        in {
            "textDocument/didOpen",
            "textDocument/didChange",
            "textDocument/didClose",
            "workspace/didChangeWorkspaceFolders",
            "workspace/didChangeConfiguration",
        }
    )


def _is_worker_request(message: dict[str, Any], state: ServerState) -> bool:
    """Select ordinary running-state client requests for the single worker lane."""
    method = message.get("method")
    return (
        state is ServerState.RUNNING
        and _client_request_id(message) is not None
        and isinstance(method, str)
        and method not in {"initialize", "initialized", "shutdown", "exit"}
    )


@dataclass(frozen=True, slots=True)
class _CompletedRequest:
    request_id: str | int
    response: dict[str, object] | None
    error: BaseException | None = None


def run_session(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    server: LanguageServer | None = None,
) -> int:
    """Run one stdio session with one request worker and live document mutation.

    Framing stays on one background reader. At most one ordinary client request executes
    on a request worker. While that request is active, document lifecycle mutations,
    workspace-folder scope changes, and formatting-configuration invalidation may advance
    on the foreground dispatcher; all other ordinary inbound client requests and
    lifecycle traffic remain deferred in FIFO order. Live snapshot/configuration
    mutations may overtake queued requests while the active request runs, so exact
    document, workspace, and save-formatting guards can observe transport-time changes
    without introducing parallel client-request execution.
    """
    active_server = server if server is not None else NovaProductLanguageServer()
    reader = MessageReader(input_stream)
    events: Queue[dict[str, Any] | _CompletedRequest | object] = Queue()
    controls: Queue[dict[str, Any]] = Queue()
    deferred: deque[dict[str, Any] | object] = deque()
    pending_ids: set[str | int] = set()
    pending_lock = Lock()
    active_request_thread: Thread | None = None
    active_request_id: str | int | None = None

    def read_inbound() -> None:
        while True:
            try:
                message = reader.read()
            except (FramingError, OSError):
                events.put(_TRANSPORT_FAILURE)
                return

            if message is None:
                events.put(_TRANSPORT_FAILURE)
                return

            cancel_target = _cancel_target(message)
            if cancel_target is not None:
                with pending_lock:
                    known_pending = cancel_target in pending_ids
                if known_pending:
                    active_server.requests.stage_cancel(cancel_target)
                controls.put(message)
                continue

            request_id = _client_request_id(message)
            if request_id is not None:
                with pending_lock:
                    pending_ids.add(request_id)
            events.put(message)

            if _is_exit_notification(message):
                return

    def replay_controls() -> None:
        while True:
            try:
                control = controls.get_nowait()
            except Empty:
                return
            try:
                active_server.handle(control)
            finally:
                controls.task_done()

    def finish_transport_request(request_id: str | int) -> None:
        active_server.requests.clear_staged_cancel(request_id)
        with pending_lock:
            pending_ids.discard(request_id)

    def dispatch_request(message: dict[str, Any], request_id: str | int) -> None:
        try:
            response = active_server.handle(message)
        except BaseException as exc:
            completion = _CompletedRequest(request_id, None, exc)
        else:
            completion = _CompletedRequest(request_id, response)
        finally:
            finish_transport_request(request_id)
        events.put(completion)

    def dispatch_foreground(message: dict[str, Any]) -> dict[str, object] | None:
        request_id = _client_request_id(message)
        try:
            return active_server.handle(message)
        finally:
            if request_id is not None:
                finish_transport_request(request_id)

    reader_thread = Thread(target=read_inbound, name="lsp-stdio-reader", daemon=True)
    reader_thread.start()

    while active_server.state is not ServerState.EXITED:
        item = (
            deferred.popleft()
            if active_request_thread is None and deferred
            else events.get()
        )

        if isinstance(item, _CompletedRequest):
            assert active_request_thread is not None
            assert active_request_id == item.request_id
            active_request_thread.join()
            active_request_thread = None
            active_request_id = None
            replay_controls()
            if item.error is not None:
                raise item.error
            _write_batch(
                output_stream,
                _drain_after_dispatch(active_server, item.response),
            )
            continue

        if item is _TRANSPORT_FAILURE:
            if active_request_thread is not None:
                deferred.append(item)
                continue
            replay_controls()
            _write_batch(
                output_stream,
                _drain_after_dispatch(active_server, None),
            )
            reader_thread.join()
            return 1

        assert isinstance(item, dict)
        replay_controls()

        if active_request_thread is not None:
            if _is_live_snapshot_mutation(item):
                response = dispatch_foreground(item)
                replay_controls()
                _write_batch(
                    output_stream,
                    _drain_after_dispatch(active_server, response),
                )
            else:
                deferred.append(item)
            continue

        if _is_worker_request(item, active_server.state):
            request_id = _client_request_id(item)
            assert request_id is not None
            active_request_id = request_id
            active_request_thread = Thread(
                target=dispatch_request,
                args=(item, request_id),
                name="lsp-request-worker",
                daemon=True,
            )
            active_request_thread.start()
            continue

        response = dispatch_foreground(item)
        replay_controls()
        _write_batch(
            output_stream,
            _drain_after_dispatch(active_server, response),
        )

    if active_request_thread is not None:
        active_request_thread.join()
    reader_thread.join()
    return active_server.exit_code if active_server.exit_code is not None else 1

def main() -> int:
    """Run the final Nova product over process stdin/stdout."""
    return run_session(sys.stdin.buffer, sys.stdout.buffer)
