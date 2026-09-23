"""Executable cancellation-aware stdio host for the final Nova language server."""

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass
from queue import Empty, Queue
from threading import Lock, Thread
from typing import Any, BinaryIO

from .cancellation import RequestCancelled
from .completion_pipeline import NovaProductLanguageServer
from .protocol import FramingError, MessageReader, encode_message
from .server import LanguageServer, ServerState

_TRANSPORT_FAILURE = object()
_SERVER_REQUEST_OUTBOX_READY = object()


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


def _is_shutdown_request(message: dict[str, Any]) -> bool:
    """Return whether one inbound object is a valid shutdown request shape."""
    return (
        message.get("jsonrpc") == "2.0"
        and message.get("method") == "shutdown"
        and _client_request_id(message) is not None
    )


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
            "workspace/didRenameFiles",
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
    workspace-folder scope changes, formatting-configuration invalidation, responses
    to already-delivered server-to-client requests, and one causally safe terminal
    shutdown request may advance on the foreground dispatcher;
    all other ordinary inbound client requests and lifecycle traffic remain deferred in
    FIFO order. Shutdown may overtake only deferred ordinary client requests: an
    earlier deferred server response/lifecycle frame, or a live snapshot mutation
    already applied to the active generation, preserves the older transport outcome.
    A live shutdown retires the worker generation immediately but delays its own
    response until that worker has cooperatively produced a cancellation completion.
    Live snapshot/configuration mutations and delivered server-request responses may
    overtake queued ordinary requests while the active request runs, so exact document,
    workspace, save-formatting, and bidirectional request dependencies can observe
    transport-time changes without introducing parallel client-request execution.
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
    transport_failed = False
    join_reader_after_failure = True
    pending_shutdown_prefix: tuple[dict[str, object], ...] = ()
    pending_shutdown_response: dict[str, object] | None = None
    active_request_saw_live_mutation = False

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

    def abort_output_transport() -> None:
        """Fail closed when stdout can no longer carry protocol traffic."""
        nonlocal transport_failed, join_reader_after_failure
        if transport_failed:
            return
        if active_request_thread is not None:
            assert active_request_id is not None
            active_server.abort_transport(active_request_id=active_request_id)
            transport_failed = True
        else:
            active_server.abort_transport()
        join_reader_after_failure = False
        active_server._set_server_request_outbox_wakeup(None)

    def write_transport_batch(
        messages: tuple[dict[str, object], ...],
    ) -> bool:
        try:
            _write_batch(output_stream, messages)
        except OSError:
            abort_output_transport()
            return False
        return True

    def wake_server_request_outbox() -> None:
        events.put(_SERVER_REQUEST_OUTBOX_READY)

    active_server._set_server_request_outbox_wakeup(wake_server_request_outbox)
    reader_thread = Thread(target=read_inbound, name="lsp-stdio-reader", daemon=True)
    reader_thread.start()

    while active_server.state is not ServerState.EXITED:
        item = (
            deferred.popleft()
            if active_request_thread is None and deferred
            else events.get()
        )

        if (
            transport_failed
            and active_request_thread is not None
            and not isinstance(item, _CompletedRequest)
        ):
            continue

        if isinstance(item, _CompletedRequest):
            assert active_request_thread is not None
            assert active_request_id == item.request_id
            active_request_thread.join()
            active_request_thread = None
            active_request_id = None
            if transport_failed:
                if (
                    item.error is not None
                    and not isinstance(item.error, RequestCancelled)
                ):
                    active_server._set_server_request_outbox_wakeup(None)
                    raise item.error
                active_server._set_server_request_outbox_wakeup(None)
                active_server._retire_all_server_requests(cancel_remote=False)
                if join_reader_after_failure:
                    reader_thread.join()
                return 1
            if pending_shutdown_response is not None:
                if (
                    item.error is not None
                    and not isinstance(item.error, RequestCancelled)
                ):
                    active_server._set_server_request_outbox_wakeup(None)
                    raise item.error
                cancelled_response: dict[str, object] = {
                    "jsonrpc": "2.0",
                    "id": item.request_id,
                    "error": {
                        "code": -32800,
                        "message": "Request cancelled",
                    },
                }
                if not write_transport_batch(
                    (
                        *pending_shutdown_prefix,
                        cancelled_response,
                        pending_shutdown_response,
                    )
                ):
                    return 1
                pending_shutdown_prefix = ()
                pending_shutdown_response = None
                continue
            replay_controls()
            if item.error is not None:
                active_server._set_server_request_outbox_wakeup(None)
                raise item.error
            if not write_transport_batch(
                _drain_after_dispatch(active_server, item.response)
            ):
                return 1
            continue

        if item is _SERVER_REQUEST_OUTBOX_READY:
            if (
                not transport_failed
                and active_server.state is ServerState.RUNNING
                and not write_transport_batch(
                    _drain_after_dispatch(active_server, None)
                )
            ):
                if active_request_thread is None:
                    return 1
                continue
            continue

        if item is _TRANSPORT_FAILURE:
            if active_request_thread is not None:
                assert active_request_id is not None
                active_server.abort_transport(
                    active_request_id=active_request_id,
                )
                active_server._set_server_request_outbox_wakeup(None)
                transport_failed = True
                continue
            active_server.abort_transport()
            active_server._set_server_request_outbox_wakeup(None)
            reader_thread.join()
            return 1

        assert isinstance(item, dict)
        replay_controls()

        if active_request_thread is not None:
            if (
                active_server.state is ServerState.RUNNING
                and active_server._can_dispatch_server_response_live(item)
                and all(
                    isinstance(deferred_item, dict)
                    and _is_worker_request(deferred_item, active_server.state)
                    for deferred_item in deferred
                )
            ):
                response = dispatch_foreground(item)
                assert response is None
                replay_controls()
                if not write_transport_batch(
                    _drain_after_dispatch(active_server, None)
                ):
                    continue
                continue
            if (
                active_server.state is ServerState.RUNNING
                and _is_shutdown_request(item)
                and not active_request_saw_live_mutation
                and all(
                    isinstance(deferred_item, dict)
                    and _is_worker_request(deferred_item, active_server.state)
                    for deferred_item in deferred
                )
            ):
                response = dispatch_foreground(item)
                assert response is not None
                assert active_request_id is not None
                active_server.requests.stage_cancel(active_request_id)
                pending_shutdown_prefix = _drain_after_dispatch(
                    active_server,
                    None,
                )
                pending_shutdown_response = response
                continue
            if _is_live_snapshot_mutation(item):
                response = dispatch_foreground(item)
                active_request_saw_live_mutation = True
                replay_controls()
                if not write_transport_batch(
                    _drain_after_dispatch(active_server, response)
                ):
                    continue
            else:
                deferred.append(item)
            continue

        if _is_worker_request(item, active_server.state):
            request_id = _client_request_id(item)
            assert request_id is not None
            active_request_id = request_id
            active_request_saw_live_mutation = False
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
        if not write_transport_batch(
            _drain_after_dispatch(active_server, response)
        ):
            return 1

    if active_request_thread is not None:
        active_request_thread.join()
    active_server._set_server_request_outbox_wakeup(None)
    reader_thread.join()
    return active_server.exit_code if active_server.exit_code is not None else 1

def main() -> int:
    """Run the final Nova product over process stdin/stdout."""
    return run_session(sys.stdin.buffer, sys.stdout.buffer)
