"""Minimal JSON-RPC/LSP framing primitives."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, BinaryIO

MAX_CONTENT_LENGTH = 8 * 1024 * 1024
MAX_HEADER_BYTES = 64 * 1024
MAX_HEADER_COUNT = 64
MAX_HEADER_LINE_LENGTH = 8192


class FramingError(ValueError):
    """Raised when an LSP transport frame is malformed."""


@dataclass(frozen=True)
class JsonRpcError:
    code: int
    message: str
    data: Any | None = None

    def as_object(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.data is not None:
            value["data"] = self.data
        return value


class MessageReader:
    """Read Content-Length framed JSON-RPC messages from a binary stream."""

    def __init__(
        self,
        stream: BinaryIO,
        *,
        max_content_length: int = MAX_CONTENT_LENGTH,
        max_header_bytes: int = MAX_HEADER_BYTES,
        max_header_count: int = MAX_HEADER_COUNT,
    ) -> None:
        if max_content_length <= 0:
            raise ValueError("max_content_length must be positive")
        if max_header_bytes <= 0:
            raise ValueError("max_header_bytes must be positive")
        if max_header_count <= 0:
            raise ValueError("max_header_count must be positive")
        self._stream = stream
        self._max_content_length = max_content_length
        self._max_header_bytes = max_header_bytes
        self._max_header_count = max_header_count

    def read(self) -> dict[str, Any] | None:
        headers: dict[str, str] = {}
        saw_anything = False
        header_bytes = 0
        header_count = 0

        while True:
            remaining_header_bytes = self._max_header_bytes - header_bytes
            read_limit = min(
                MAX_HEADER_LINE_LENGTH,
                remaining_header_bytes,
            ) + 1
            raw = self._stream.readline(read_limit)
            if raw == b"":
                if not saw_anything:
                    return None
                raise FramingError("unexpected EOF while reading headers")
            saw_anything = True
            if raw in (b"\r\n", b"\n"):
                break

            header_bytes += len(raw)
            if header_bytes > self._max_header_bytes:
                raise FramingError("header section exceeds configured size limit")

            header_count += 1
            if header_count > self._max_header_count:
                raise FramingError("header count exceeds configured limit")

            if len(raw) > MAX_HEADER_LINE_LENGTH:
                raise FramingError("header line too long")
            try:
                line = raw.decode("ascii").rstrip("\r\n")
            except UnicodeDecodeError as exc:
                raise FramingError("headers must be ASCII") from exc
            name, sep, value = line.partition(":")
            if not sep:
                raise FramingError("malformed header line")
            key = name.strip().lower()
            if not key or key in headers:
                raise FramingError("missing or duplicate header name")
            headers[key] = value.strip()

        value = headers.get("content-length")
        if value is None:
            raise FramingError("missing Content-Length header")
        if not value.isascii() or not value.isdecimal():
            raise FramingError("invalid Content-Length header")

        length = int(value, 10)
        if length > self._max_content_length:
            raise FramingError("message exceeds configured size limit")

        payload = self._stream.read(length)
        if len(payload) != length:
            raise FramingError("unexpected EOF while reading payload")
        try:
            decoded = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FramingError("payload must be UTF-8") from exc
        try:
            message = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise FramingError("payload is not valid JSON") from exc
        if not isinstance(message, dict):
            raise FramingError("JSON-RPC payload must be an object")
        return message


def encode_message(message: dict[str, Any]) -> bytes:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii") + payload
