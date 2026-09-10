"""Bounded Nova definite-initialization proof for loop-control exits."""

from __future__ import annotations

import re

from .local_declaration_diagnostics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)

_LOOP_CONTROL_STATEMENT = re.compile(r"\b(?:break|continue)\b\s*;")
_WHILE_PREFIX = re.compile(r"\bwhile\s*\([^{};]*\)\s*$")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product that treats valid direct loop exits as join termination."""

    @classmethod
    def _direct_scope_terminates(
        cls,
        code: str,
        branch_open: int,
        branch_close: int,
        branch_scope: tuple[int, ...],
    ) -> bool:
        if super()._direct_scope_terminates(
            code, branch_open, branch_close, branch_scope
        ):
            return True
        if not cls._scope_is_inside_while(code, branch_scope):
            return False
        return any(
            cls._brace_scope_at(code, match.start()) == branch_scope
            for match in _LOOP_CONTROL_STATEMENT.finditer(
                code, branch_open + 1, branch_close
            )
        )

    @classmethod
    def _scope_is_inside_while(cls, code: str, scope: tuple[int, ...]) -> bool:
        return any(cls._local_scope_brace_opens_while(code, opening) for opening in scope)

    @staticmethod
    def _local_scope_brace_opens_while(code: str, opening: int) -> bool:
        boundary = max(
            code.rfind(";", 0, opening),
            code.rfind("{", 0, opening),
            code.rfind("}", 0, opening),
        )
        return _WHILE_PREFIX.search(code[boundary + 1 : opening]) is not None
