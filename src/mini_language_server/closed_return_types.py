"""Detached closed-workspace Nova return-expression validation."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .expression_local_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_RETURN = re.compile(r"\breturn\b")
_CLOSED_EXPECTED_RETURN_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Add bounded return-type diagnostics for detached workspace snapshots."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(
            super()._closed_workspace_product_diagnostics(snapshot, functions)
        )
        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)

        for symbol in snapshot.symbols.symbols:
            if symbol.kind != "function":
                continue
            expected = self._closed_function_result_type(snapshot, symbol.span)
            if expected not in _CLOSED_EXPECTED_RETURN_TYPES:
                continue

            opening = code.find("{", symbol.span.end)
            if opening < 0:
                continue
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue

            body_code = code[opening + 1 : closing]
            for statement in _RETURN.finditer(body_code):
                keyword_end = opening + 1 + statement.end()
                boundary = closing
                for delimiter in (";", "\n", "\r"):
                    found = text.find(delimiter, keyword_end, closing)
                    if found >= 0:
                        boundary = min(boundary, found)

                raw = text[keyword_end:boundary]
                leading = len(raw) - len(raw.lstrip())
                expression = raw.strip()
                if not expression:
                    continue
                expression_span = Span(
                    keyword_end + leading,
                    keyword_end + leading + len(expression),
                )
                actual = self._closed_expression_type(
                    snapshot,
                    expression,
                    expression_span,
                    functions,
                    frozenset(),
                )
                if actual is None or actual == expected:
                    continue
                diagnostics.append(
                    Diagnostic(
                        span=expression_span,
                        message=(
                            f"return type mismatch: expected '{expected}', "
                            f"got '{actual}'"
                        ),
                        code="nova.return-type",
                        source="nova",
                    )
                )

        return tuple(diagnostics)
