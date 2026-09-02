from io import BytesIO

import pytest

from mini_language_server.protocol import FramingError, MessageReader, encode_message


def test_round_trip_unicode_message() -> None:
    message = {"jsonrpc": "2.0", "id": 1, "method": "example/測試", "params": {"x": "λ"}}
    assert MessageReader(BytesIO(encode_message(message))).read() == message


def test_reader_returns_none_on_clean_eof() -> None:
    assert MessageReader(BytesIO()).read() is None


def test_missing_content_length_is_rejected() -> None:
    with pytest.raises(FramingError, match="missing Content-Length"):
        MessageReader(BytesIO(b"Content-Type: application/vscode-jsonrpc; charset=utf-8\r\n\r\n{}")) .read()


def test_duplicate_content_length_is_rejected() -> None:
    data = b"Content-Length: 2\r\nContent-Length: 2\r\n\r\n{}"
    with pytest.raises(FramingError, match="duplicate"):
        MessageReader(BytesIO(data)).read()


def test_oversized_message_is_rejected_before_payload_read() -> None:
    with pytest.raises(FramingError, match="size limit"):
        MessageReader(BytesIO(b"Content-Length: 3\r\n\r\n{}"), max_content_length=2).read()


def test_truncated_payload_is_rejected() -> None:
    with pytest.raises(FramingError, match="unexpected EOF"):
        MessageReader(BytesIO(b"Content-Length: 4\r\n\r\n{}")) .read()


def test_non_object_jsonrpc_payload_is_rejected() -> None:
    with pytest.raises(FramingError, match="must be an object"):
        MessageReader(BytesIO(b"Content-Length: 2\r\n\r\n[]")).read()
