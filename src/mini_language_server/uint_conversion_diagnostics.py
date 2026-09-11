"""Exact-snapshot diagnostics for bounded Nova explicit numeric conversions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span
from .uint_conversion_types import NovaProductLanguageServer as _NovaProductLanguageServer

_CONVERSION_HEAD = re.compile(
    r"\b(?P<name>UInt\s*::\s*from|Int\s*::\s*from_uint)\s*\("
)
_CONVERSION_ARITY_DIAGNOSTIC = "nova.conversion-arity"
_CONVERSION_TYPE_DIAGNOSTIC = "nova.conversion-type"


@dataclass(frozen=True, slots=True)
class _ConversionCall:
    name: str
    target_type: str
    source_type: str
    span: Span
    arguments: tuple[tuple[str, Span], ...]


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with deterministic explicit-conversion diagnostics."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code
            not in {_CONVERSION_ARITY_DIAGNOSTIC, _CONVERSION_TYPE_DIAGNOSTIC}
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_conversion_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_conversion_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        diagnostics: list[Diagnostic] = []
        for call in self._conversion_calls(text):
            if len(call.arguments) != 1:
                diagnostics.append(
                    Diagnostic(
                        span=call.span,
                        message=(
                            f"'{call.name}' expects 1 argument; "
                            f"got {len(call.arguments)}"
                        ),
                        code=_CONVERSION_ARITY_DIAGNOSTIC,
                        source="nova",
                    )
                )
                continue

            argument, argument_span = call.arguments[0]
            argument_type = self._integer_arithmetic_type(
                semantic, argument, argument_span
            )
            if argument_type is None or argument_type == call.source_type:
                continue
            diagnostics.append(
                Diagnostic(
                    span=argument_span,
                    message=(
                        f"argument to '{call.name}' has type '{argument_type}'; "
                        f"expected '{call.source_type}'"
                    ),
                    code=_CONVERSION_TYPE_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

    def _conversion_calls(self, text: str) -> Iterator[_ConversionCall]:
        code = self.nova_adapter.code_view(text)
        for match in _CONVERSION_HEAD.finditer(code):
            opening = code.rfind("(", match.start(), match.end())
            closing = self._matching_paren(code, opening)
            if closing is None:
                continue
            name = "".join(match.group("name").split())
            target_type, source_type = (
                ("UInt", "Int")
                if name == "UInt::from"
                else ("Int", "UInt")
            )
            arguments = self._conversion_arguments(
                text, code, opening + 1, closing
            )
            yield _ConversionCall(
                name=name,
                target_type=target_type,
                source_type=source_type,
                span=Span(match.start(), closing + 1),
                arguments=arguments,
            )

    @staticmethod
    def _matching_paren(code: str, opening: int) -> int | None:
        depth = 0
        for offset in range(opening, len(code)):
            char = code[offset]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return offset
                if depth < 0:
                    return None
        return None

    def _conversion_arguments(
        self, text: str, code: str, start: int, end: int
    ) -> tuple[tuple[str, Span], ...]:
        if not code[start:end].strip():
            return ()

        bounds: list[tuple[int, int]] = []
        argument_start = start
        depth = 0
        for offset in range(start, end):
            char = code[offset]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
            elif char == "," and depth == 0:
                bounds.append((argument_start, offset))
                argument_start = offset + 1
        bounds.append((argument_start, end))

        arguments: list[tuple[str, Span]] = []
        for raw_start, raw_end in bounds:
            raw = text[raw_start:raw_end]
            leading = len(raw) - len(raw.lstrip())
            trailing = len(raw) - len(raw.rstrip())
            value_start = raw_start + leading
            value_end = raw_end - trailing
            if value_start >= value_end:
                arguments.append(("", Span(value_start, value_end)))
                continue
            arguments.append(
                (text[value_start:value_end], Span(value_start, value_end))
            )
        return tuple(arguments)
