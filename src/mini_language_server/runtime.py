"""Executable cancellation-aware stdio host for the final Nova language server."""

from __future__ import annotations

import sys
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


def run_session(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    server: LanguageServer | None = None,
) -> int:
    """Run one ordered stdio session with live cancellation while requests execute.

    Framing runs on one background reader. Every ordinary inbound message is still
    dispatched serially on the calling thread. Only cancellation state may cross the
    dispatch boundary: once a request frame has been read, a later cancel frame can
    mark that exact pending generation before or during server dispatch.
    """
    active_server = server if server is not None else NovaProductLanguageServer()
    reader = MessageReader(input_stream)
    inbox: Queue[dict[str, Any] | object] = Queue()
    controls: Queue[dict[str, Any]] = Queue()
    pending_ids: set[str | int] = set()
    pending_lock = Lock()

    def read_inbound() -> None:
        while True:
            try:
                message = reader.read()
            except (FramingError, OSError):
                inbox.put(_TRANSPORT_FAILURE)
                return

            if message is None:
                inbox.put(_TRANSPORT_FAILURE)
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
            inbox.put(message)

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

    reader_thread = Thread(target=read_inbound, name="lsp-stdio-reader", daemon=True)
    reader_thread.start()

    while active_server.state is not ServerState.EXITED:
        item = inbox.get()
        try:
            if item is _TRANSPORT_FAILURE:
                replay_controls()
                _write_batch(
                    output_stream,
                    _drain_after_dispatch(active_server, None),
                )
                reader_thread.join()
                return 1

            assert isinstance(item, dict)
            replay_controls()
            request_id = _client_request_id(item)
            try:
                response = active_server.handle(item)
            finally:
                if request_id is not None:
                    active_server.requests.clear_staged_cancel(request_id)
                    with pending_lock:
                        pending_ids.discard(request_id)

            replay_controls()
            _write_batch(
                output_stream,
                _drain_after_dispatch(active_server, response),
            )
        finally:
            inbox.task_done()

    reader_thread.join()
    return active_server.exit_code if active_server.exit_code is not None else 1


def main() -> int:
    """Run the final Nova product over process stdin/stdout."""
    return run_session(sys.stdin.buffer, sys.stdout.buffer)
