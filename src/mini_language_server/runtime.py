"""Executable synchronous stdio host for the final Nova language server."""

from __future__ import annotations

import sys
from typing import BinaryIO

from .completion_pipeline import NovaProductLanguageServer
from .protocol import FramingError, MessageReader, encode_message
from .server import LanguageServer, ServerState


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


def run_session(
    input_stream: BinaryIO,
    output_stream: BinaryIO,
    *,
    server: LanguageServer | None = None,
) -> int:
    """Run one synchronous LSP stdio session until clean exit or transport failure."""
    active_server = server if server is not None else NovaProductLanguageServer()
    reader = MessageReader(input_stream)

    while active_server.state is not ServerState.EXITED:
        try:
            message = reader.read()
        except FramingError:
            return 1

        if message is None:
            return 1

        response = active_server.handle(message)
        _write_batch(
            output_stream,
            _drain_after_dispatch(active_server, response),
        )

    return active_server.exit_code if active_server.exit_code is not None else 1


def main() -> int:
    """Run the final Nova product over process stdin/stdout."""
    return run_session(sys.stdin.buffer, sys.stdout.buffer)
