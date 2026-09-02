import pytest

from mini_language_server.source import Position, SourceError, SourceText, Span


def test_source_text_maps_utf16_positions_and_offsets() -> None:
    source = SourceText("a😀b\nsecond")

    assert source.offset_at(Position(0, 0)) == 0
    assert source.offset_at(Position(0, 1)) == 1
    assert source.offset_at(Position(0, 3)) == 2
    assert source.offset_at(Position(1, 0)) == 4
    assert source.position_at(2) == Position(0, 3)
    assert source.position_at(4) == Position(1, 0)


def test_source_text_rejects_position_inside_surrogate_pair() -> None:
    source = SourceText("a😀b")

    with pytest.raises(SourceError, match="surrogate pair"):
        source.offset_at(Position(0, 2))


def test_source_text_handles_crlf_and_lone_cr_line_endings() -> None:
    source = SourceText("one\r\ntwo\rthree\n")

    assert source.line_count == 4
    assert source.offset_at(Position(1, 0)) == 5
    assert source.offset_at(Position(2, 0)) == 9
    assert source.offset_at(Position(3, 0)) == 15
    assert source.position_at(5) == Position(1, 0)
    assert source.position_at(9) == Position(2, 0)


def test_span_range_conversion_is_half_open_and_round_trips() -> None:
    source = SourceText("alpha\nβ😀z")

    span = source.span_from_range(Position(1, 0), Position(1, 3))

    assert span == Span(6, 8)
    assert span.length == 2
    assert source.range_from_span(span) == (Position(1, 0), Position(1, 3))


def test_span_from_range_rejects_reverse_range() -> None:
    source = SourceText("abc\ndef")

    with pytest.raises(SourceError, match="range start is after end"):
        source.span_from_range(Position(1, 0), Position(0, 1))


def test_position_at_rejects_offset_outside_document() -> None:
    source = SourceText("abc")

    with pytest.raises(SourceError, match="offset is outside"):
        source.position_at(4)


def test_range_from_span_rejects_span_past_document_end() -> None:
    source = SourceText("abc")

    with pytest.raises(SourceError, match="span is outside"):
        source.range_from_span(Span(0, 4))
