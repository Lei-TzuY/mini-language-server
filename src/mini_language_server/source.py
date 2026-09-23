"""Language-independent source coordinates and negotiated LSP position mapping."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

DEFAULT_POSITION_ENCODING = "utf-16"
SUPPORTED_POSITION_ENCODINGS = ("utf-8", "utf-16", "utf-32")


class SourceError(ValueError):
    """Raised when a source position or span is invalid."""


@dataclass(frozen=True, slots=True, order=True)
class Position:
    """An LSP position measured in zero-based lines and negotiated character units."""

    line: int
    character: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.line, int)
            or isinstance(self.line, bool)
            or self.line < 0
            or not isinstance(self.character, int)
            or isinstance(self.character, bool)
            or self.character < 0
        ):
            raise SourceError("position line and character must be non-negative integers")


@dataclass(frozen=True, slots=True)
class Span:
    """A half-open source span using Python string offsets."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if (
            not isinstance(self.start, int)
            or isinstance(self.start, bool)
            or not isinstance(self.end, int)
            or isinstance(self.end, bool)
            or self.start < 0
            or self.end < self.start
        ):
            raise SourceError("span offsets must satisfy 0 <= start <= end")

    @property
    def length(self) -> int:
        return self.end - self.start


class SourceText:
    """Immutable text snapshot with reusable line and position mapping metadata."""

    def __init__(
        self,
        text: str,
        *,
        position_encoding: str = DEFAULT_POSITION_ENCODING,
    ) -> None:
        if not isinstance(text, str):
            raise SourceError("source text must be a string")
        if position_encoding not in SUPPORTED_POSITION_ENCODINGS:
            raise SourceError(f"unsupported position encoding: {position_encoding!r}")
        self.text = text
        self.position_encoding = position_encoding
        self._line_starts = self._compute_line_starts(text)

    @property
    def line_count(self) -> int:
        return len(self._line_starts)

    def offset_at(self, position: Position) -> int:
        """Convert one negotiated LSP position into a Python string offset."""
        if position.line >= self.line_count:
            raise SourceError("position line is outside the document")

        start = self._line_starts[position.line]
        end = self._line_content_end(position.line)
        line_text = self.text[start:end]

        units_seen = 0
        for index, char in enumerate(line_text):
            if units_seen == position.character:
                return start + index
            units = self._character_units(char)
            if units_seen < position.character < units_seen + units:
                if self.position_encoding == "utf-16":
                    raise SourceError("position splits a UTF-16 surrogate pair")
                raise SourceError("position splits a UTF-8 code point")
            units_seen += units

        if units_seen == position.character:
            return end
        raise SourceError("position character is outside the line")

    def position_at(self, offset: int) -> Position:
        """Convert a Python string offset to a canonical negotiated LSP position."""
        if (
            not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or offset > len(self.text)
        ):
            raise SourceError("offset is outside the document")

        line = bisect_right(self._line_starts, offset) - 1
        start = self._line_starts[line]
        content_end = self._line_content_end(line)
        bounded_offset = min(offset, content_end)
        character = self._encoded_units(self.text[start:bounded_offset])
        return Position(line, character)

    def span_from_range(self, start: Position, end: Position) -> Span:
        """Convert an LSP range into a validated half-open source span."""
        start_offset = self.offset_at(start)
        end_offset = self.offset_at(end)
        if start_offset > end_offset:
            raise SourceError("range start is after end")
        return Span(start_offset, end_offset)

    def range_from_span(self, span: Span) -> tuple[Position, Position]:
        """Convert a source span into canonical negotiated LSP positions."""
        if span.end > len(self.text):
            raise SourceError("span is outside the document")
        return self.position_at(span.start), self.position_at(span.end)

    def _character_units(self, char: str) -> int:
        if self.position_encoding == "utf-16":
            return 2 if ord(char) > 0xFFFF else 1
        if self.position_encoding == "utf-8":
            return len(char.encode("utf-8"))
        return 1

    def _encoded_units(self, text: str) -> int:
        if self.position_encoding == "utf-16":
            return len(text.encode("utf-16-le")) // 2
        if self.position_encoding == "utf-8":
            return len(text.encode("utf-8"))
        return len(text)

    def _line_content_end(self, line: int) -> int:
        start = self._line_starts[line]
        physical_end = (
            self._line_starts[line + 1] if line + 1 < self.line_count else len(self.text)
        )
        if physical_end <= start:
            return physical_end
        if self.text[physical_end - 1] == "\n":
            physical_end -= 1
            if physical_end > start and self.text[physical_end - 1] == "\r":
                physical_end -= 1
        elif self.text[physical_end - 1] == "\r":
            physical_end -= 1
        return physical_end

    @staticmethod
    def _compute_line_starts(text: str) -> tuple[int, ...]:
        starts = [0]
        index = 0
        while index < len(text):
            char = text[index]
            if char == "\r":
                if index + 1 < len(text) and text[index + 1] == "\n":
                    index += 1
                starts.append(index + 1)
            elif char == "\n":
                starts.append(index + 1)
            index += 1
        return tuple(starts)
