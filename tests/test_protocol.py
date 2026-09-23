from io import BytesIO

import pytest

from mini_language_server.protocol import (
    MAX_HEADER_LINE_LENGTH,
    FramingError,
    MessageReader,
    encode_message,
)


def test_round_trip_unicode_message() -> None:
    message = {"jsonrpc": "2.0", "id": 1, "method": "example/測試", "params": {"x": "λ"}}
    assert MessageReader(BytesIO(encode_message(message))).read() == message


def test_reader_returns_none_on_clean_eof() -> None:
    assert MessageReader(BytesIO()).read() is None


def test_missing_content_length_is_rejected() -> None:
    data = b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n{}"
    with pytest.raises(FramingError, match="missing Content-Length"):
        MessageReader(BytesIO(data)).read()


def test_duplicate_content_length_is_rejected() -> None:
    data = b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    with pytest.raises(FramingError, match="duplicate"):
        MessageReader(BytesIO(data)).read()


def test_oversized_message_is_rejected_before_payload_read() -> None:
    with pytest.raises(FramingError, match="size limit"):
        MessageReader(BytesIO(b"Content-Length: 3\r\n\r\n{}"), max_content_length=2).read()


def test_truncated_payload_is_rejected() -> None:
    with pytest.raises(FramingError, match="unexpected EOF"):
        MessageReader(BytesIO(b"Content-Length: 4\r\n\r\n{}")).read()


def test_non_object_jsonrpc_payload_is_rejected() -> None:
    with pytest.raises(FramingError, match="must be an object"):
        MessageReader(BytesIO(b"Content-Length: 2\r\n\r\n[]")).read()


class TrackingBytesIO(BytesIO):
    def __init__(self, value: bytes) -> None:
        super().__init__(value)
        self.payload_reads = 0
        self.readline_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.payload_reads += 1
        return super().read(size)

    def readline(self, size: int = -1) -> bytes:
        self.readline_sizes.append(size)
        return super().readline(size)


@pytest.mark.parametrize(
    ("keyword", "value", "message"),
    [
        ("max_header_bytes", 0, "max_header_bytes must be positive"),
        ("max_header_count", 0, "max_header_count must be positive"),
    ],
)
def test_header_limits_must_be_positive(
    keyword: str, value: int, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        MessageReader(BytesIO(), **{keyword: value})


def test_header_count_limit_rejects_before_payload_read() -> None:
    stream = TrackingBytesIO(
        b"X-Trace: one\r\nContent-Length: 2\r\n\r\n{}"
    )

    with pytest.raises(FramingError, match="header count"):
        MessageReader(stream, max_header_count=1).read()

    assert stream.payload_reads == 0


def test_total_header_byte_limit_rejects_before_payload_read() -> None:
    extra = b"X-Trace: one\r\n"
    content_length = b"Content-Length: 2\r\n"
    stream = TrackingBytesIO(extra + content_length + b"\r\n{}")
    budget = len(extra) + len(content_length) + len(b"\r\n") - 1

    with pytest.raises(FramingError, match="header section"):
        MessageReader(stream, max_header_bytes=budget).read()

    assert stream.payload_reads == 0


def test_exact_header_byte_and_count_boundaries_are_accepted() -> None:
    extra = b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n"
    content_length = b"Content-Length: 2\r\n"
    headers = extra + content_length
    stream = BytesIO(headers + b"\r\n{}")

    assert MessageReader(
        stream,
        max_header_bytes=len(headers) + len(b"\r\n"),
        max_header_count=2,
    ).read() == {}


def test_header_count_boundary_is_inclusive() -> None:
    stream = BytesIO(
        b"X-Trace: one\r\nContent-Length: 2\r\n\r\n{}"
    )

    assert MessageReader(stream, max_header_count=2).read() == {}


def test_single_header_line_read_is_bounded_before_allocation() -> None:
    stream = TrackingBytesIO(
        b"X-Large: " + (b"a" * 100_000) + b"\r\nContent-Length: 2\r\n\r\n{}"
    )

    with pytest.raises(FramingError, match="header line too long"):
        MessageReader(stream).read()

    assert stream.readline_sizes[0] == MAX_HEADER_LINE_LENGTH + 1
    assert stream.payload_reads == 0


def test_header_read_is_capped_by_remaining_total_budget() -> None:
    stream = TrackingBytesIO(
        b"X-Large: " + (b"a" * 1000) + b"\r\nContent-Length: 2\r\n\r\n{}"
    )

    with pytest.raises(FramingError, match="header section"):
        MessageReader(stream, max_header_bytes=8).read()

    assert stream.readline_sizes[0] == 9
    assert stream.payload_reads == 0
