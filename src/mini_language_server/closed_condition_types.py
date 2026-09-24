"""Detached closed-workspace Nova condition typing diagnostics and repairs."""

from __future__ import annotations

from typing import Any

from .closed_scalar_diagnostics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .condition_diagnostics import _CONDITION_TYPE_DIAGNOSTIC
from .diagnostics import Diagnostic


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Promote exact captured condition typing into detached workspace ownership."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics = [
            diagnostic
            for diagnostic in super()._closed_workspace_product_diagnostics(
                snapshot,
                functions,
            )
            if diagnostic.code != _CONDITION_TYPE_DIAGNOSTIC
        ]
        text = snapshot.symbols.syntax.document.text
        diagnostics.extend(
            self._condition_type_diagnostics(
                text,
                lambda expression, span: self._closed_expression_type(
                    snapshot,
                    expression,
                    span,
                    functions,
                    frozenset(),
                ),
            )
        )
        return tuple(diagnostics)

    def _closed_nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._closed_nova_code_actions(
            uri,
            document,
            source,
            diagnostics,
            start_offset,
            end_offset,
        )
        for diagnostic in diagnostics:
            if diagnostic.code != _CONDITION_TYPE_DIAGNOSTIC:
                continue
            if not self._condition_diagnostic_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue
            actual = self._condition_actual_type_from_diagnostic(diagnostic)
            if actual is None:
                continue
            expression = document.text[diagnostic.span.start : diagnostic.span.end]
            if not expression:
                continue
            typed_action = self._condition_preserving_action(
                uri,
                source,
                diagnostic,
                expression,
                actual,
            )
            if typed_action is not None:
                actions.append(typed_action)
            actions.append(
                self._condition_bool_literal_action(
                    uri,
                    source,
                    diagnostic,
                )
            )
        return actions
