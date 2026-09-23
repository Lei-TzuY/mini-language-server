"""Detached closed-workspace Nova explicit-local type validation."""

from __future__ import annotations

from typing import Any

from .closed_return_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import Diagnostic

_CLOSED_EXPECTED_LOCAL_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Add bounded explicit-local diagnostics for detached workspace snapshots."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(
            super()._closed_workspace_product_diagnostics(snapshot, functions)
        )
        text = snapshot.symbols.syntax.document.text

        for symbol in snapshot.symbols.symbols:
            if symbol.kind != "variable":
                continue
            annotation = self._explicit_local_annotation(snapshot, symbol)
            if annotation is None:
                continue
            expected, initializer_start = annotation
            if expected not in _CLOSED_EXPECTED_LOCAL_TYPES:
                continue

            initializer = self._local_initializer(text, initializer_start)
            if initializer is None:
                continue
            expression, expression_span = initializer
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
                        f"local type mismatch: expected '{expected}', got '{actual}'"
                    ),
                    code="nova.local-type",
                    source="nova",
                )
            )

        return tuple(diagnostics)
