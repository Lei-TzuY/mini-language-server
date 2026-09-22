"""Recursive exact-snapshot diagnostics for nested Nova unreachable code."""

from __future__ import annotations

import re

from .condition_diagnostics import _FUNCTION, _UNREACHABLE_CODE_DIAGNOSTIC
from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span
from .unreachable_actions import NovaProductLanguageServer as _NovaProductLanguageServer

_LOOP_CONTROL = re.compile(r"\b(?:break|continue)\b")
_WHILE = re.compile(r"\bwhile\s*\(")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with nested structural unreachable-code analysis."""

    def _nova_unreachable_code_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[tuple[Span, str], ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        regions: list[tuple[Span, str]] = []
        for function in _FUNCTION.finditer(code):
            opening = function.end() - 1
            closing = self._matching_delimiter(code, opening, "{", "}")
            if closing is None:
                continue
            regions.extend(
                self._unreachable_regions_in_body(
                    code[opening + 1 : closing],
                    text[opening + 1 : closing],
                    base_offset=opening + 1,
                    loop_depth=0,
                    include_never_calls=True,
                )
            )
        return tuple(
            Diagnostic(
                span=span,
                message=f"unreachable code after {termination_kind}",
                code=_UNREACHABLE_CODE_DIAGNOSTIC,
                source="nova",
                tags=("unnecessary",),
            )
            for span, termination_kind in regions
        )

    def _unreachable_regions_in_body(
        self,
        code: str,
        text: str,
        *,
        base_offset: int,
        loop_depth: int,
        include_never_calls: bool = False,
    ) -> tuple[Diagnostic, ...]:
        termination = self._first_guaranteed_termination(
            code,
            text,
            include_never_calls=include_never_calls,
        )
        if termination is None:
            termination_end = None
            termination_kind = "guaranteed return"
        else:
            termination_end, termination_kind = termination
        if loop_depth > 0:
            loop_control = self._first_top_level_loop_control(code)
            if loop_control is not None and (
                termination_end is None or loop_control[0] < termination_end
            ):
                _, termination_end, keyword = loop_control
                termination_kind = f"'{keyword}'"
        scan_end = len(code) if termination_end is None else termination_end
        regions: list[tuple[Span, str]] = []

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
                    regions.append(
                        (
                            Span(
                                base_offset + unreachable_start,
                                base_offset + unreachable_end,
                            ),
                            termination_kind,
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
            child_loop_depth = loop_depth + int(self._brace_opens_while(code, opening))
            regions.extend(
                self._unreachable_regions_in_body(
                    code[opening + 1 : closing],
                    text[opening + 1 : closing],
                    base_offset=base_offset + opening + 1,
                    loop_depth=child_loop_depth,
                    include_never_calls=include_never_calls,
                )
            )
            cursor = closing + 1

        return tuple(regions)

    def _first_top_level_loop_control(
        self, code: str
    ) -> tuple[int, int, str] | None:
        for statement in _LOOP_CONTROL.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            return (
                statement.start(),
                self._return_statement_end(code, statement.end()),
                statement.group(0),
            )
        return None

    def _brace_opens_while(self, code: str, opening: int) -> bool:
        """Return whether one brace is the body brace of a structural while."""
        for statement in _WHILE.finditer(code, 0, opening):
            if self._brace_depth_before(code, statement.start()) != self._brace_depth_before(
                code, opening
            ):
                continue
            condition_open = statement.end() - 1
            condition_close = self._matching_delimiter(code, condition_open, "(", ")")
            if condition_close is None:
                continue
            body_open = self._next_non_space(code, condition_close + 1)
            if body_open == opening:
                return True
        return False
