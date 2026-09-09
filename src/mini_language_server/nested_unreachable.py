"""Recursive exact-snapshot diagnostics for nested Nova unreachable code."""

from __future__ import annotations

from .condition_diagnostics import _FUNCTION, _UNREACHABLE_CODE_DIAGNOSTIC
from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span
from .unreachable_actions import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with nested structural unreachable-code analysis."""

    def _nova_unreachable_code_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for function in _FUNCTION.finditer(code):
            opening = function.end() - 1
            closing = self._matching_delimiter(code, opening, "{", "}")
            if closing is None:
                continue
            diagnostics.extend(
                self._unreachable_in_body(
                    code[opening + 1 : closing],
                    text[opening + 1 : closing],
                    base_offset=opening + 1,
                )
            )
        return tuple(diagnostics)

    def _unreachable_in_body(
        self, code: str, text: str, *, base_offset: int
    ) -> tuple[Diagnostic, ...]:
        termination_end = self._first_guaranteed_termination_end(code, text)
        scan_end = len(code) if termination_end is None else termination_end
        diagnostics: list[Diagnostic] = []

        if termination_end is not None:
            unreachable_start = self._next_non_space(code, termination_end)
            if unreachable_start is not None:
                unreachable_end = len(code)
                while (
                    unreachable_end > unreachable_start
                    and code[unreachable_end - 1].isspace()
                ):
                    unreachable_end -= 1
                if unreachable_end > unreachable_start:
                    diagnostics.append(
                        Diagnostic(
                            span=Span(
                                base_offset + unreachable_start,
                                base_offset + unreachable_end,
                            ),
                            message="unreachable code after guaranteed return",
                            code=_UNREACHABLE_CODE_DIAGNOSTIC,
                            source="nova",
                            tags=("unnecessary",),
                        )
                    )

        cursor = 0
        while cursor < scan_end:
            opening = code.find("{", cursor, scan_end)
            if opening < 0:
                break
            closing = self._matching_delimiter(code, opening, "{", "}")
            if closing is None or closing >= scan_end:
                break
            diagnostics.extend(
                self._unreachable_in_body(
                    code[opening + 1 : closing],
                    text[opening + 1 : closing],
                    base_offset=base_offset + opening + 1,
                )
            )
            cursor = closing + 1

        return tuple(diagnostics)
