"""Detached exact-snapshot scalar diagnostics and repairs."""

from __future__ import annotations

from typing import Any

from .closed_assignment_semantics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .constant_condition_diagnostics import _CONSTANT_CONDITION_DIAGNOSTIC
from .diagnostics import Diagnostic
from .division_diagnostics import _DIVISION_BY_ZERO_DIAGNOSTIC
from .uint_conversion_range_diagnostics import _CONVERSION_RANGE_DIAGNOSTIC


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Promote pure scalar snapshot analyzers into detached ownership."""

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
            if diagnostic.code
            not in {
                _CONSTANT_CONDITION_DIAGNOSTIC,
                _DIVISION_BY_ZERO_DIAGNOSTIC,
                _CONVERSION_RANGE_DIAGNOSTIC,
            }
        ]
        diagnostics.extend(self._nova_constant_condition_diagnostics(snapshot))
        diagnostics.extend(self._nova_division_by_zero_diagnostics(snapshot))
        diagnostics.extend(self._nova_conversion_range_diagnostics(snapshot))
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
            if diagnostic.code != _DIVISION_BY_ZERO_DIAGNOSTIC:
                continue
            if not self._division_diagnostic_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue
            actions.append(
                self._division_by_zero_action(
                    uri,
                    source,
                    diagnostic,
                )
            )
        return actions
