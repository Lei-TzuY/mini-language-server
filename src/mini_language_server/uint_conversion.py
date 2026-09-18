"""Unified exact-snapshot diagnostics and quick fixes for Nova conversions."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span
from .uint_types import NovaProductLanguageServer as _NovaProductLanguageServer

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
    """Nova product with one deterministic conversion diagnostic/action boundary."""

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

    def _nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        snapshot = self.workspace_symbols.get(uri)
        current = self.diagnostics.get(uri)
        if (
            snapshot is None
            or snapshot.symbols.syntax.document is not document
            or current is None
            or current.semantic.symbols.syntax.document is not document
        ):
            return actions

        semantic = snapshot
        for diagnostic in diagnostics:
            if diagnostic.code != _CONVERSION_TYPE_DIAGNOSTIC:
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue
            repair = self._redundant_conversion_repair(
                semantic, document.text, diagnostic
            )
            if repair is None:
                continue
            call_span, argument = repair
            actions.append(
                {
                    "title": "Remove redundant numeric conversion",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(source, call_span),
                                    "newText": argument,
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _redundant_conversion_repair(
        self, semantic: Any, text: str, diagnostic: Diagnostic
    ) -> tuple[Any, str] | None:
        matches: list[tuple[Any, str]] = []
        for call in self._conversion_calls(text):
            if len(call.arguments) != 1:
                continue
            argument, argument_span = call.arguments[0]
            if argument_span != diagnostic.span:
                continue
            argument_type = self._integer_arithmetic_type(
                semantic, argument, argument_span
            )
            if argument_type != call.target_type:
                continue
            matches.append((call.span, argument))
        if len(matches) != 1:
            return None
        return matches[0]
