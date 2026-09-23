"""Detached closed-workspace unreachable diagnostics and repairs."""

from __future__ import annotations

from typing import Any

from .condition_diagnostics import _UNREACHABLE_CODE_DIAGNOSTIC
from .diagnostics import Diagnostic
from .unary_plus import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Promote the shared unreachable proof into detached workspace ownership."""

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
            if diagnostic.code != _UNREACHABLE_CODE_DIAGNOSTIC
        ]

        def never_returns(
            name: str,
            resolving: frozenset[tuple[int, int, int]],
        ) -> bool:
            candidates = functions.get(name, [])
            if len(candidates) != 1:
                return False
            candidate_snapshot, candidate_symbol = candidates[0]
            return self._closed_declaration_never_returns(
                candidate_snapshot,
                candidate_symbol,
                functions,
                resolving,
            )

        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        structural = self._unreachable_code_diagnostics_for_text(
            text,
            never_resolver=never_returns,
        )
        constant = self._constant_dead_branch_diagnostics(text, code)
        diagnostics.extend(
            self._merge_unreachable_code_diagnostics(
                structural,
                constant,
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
            if diagnostic.code != _UNREACHABLE_CODE_DIAGNOSTIC:
                continue
            if not self._closed_unreachable_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue
            actions.append(
                self._unreachable_code_action(
                    uri,
                    source,
                    diagnostic,
                )
            )
        return actions

    @staticmethod
    def _closed_unreachable_overlaps(
        diagnostic: Diagnostic,
        *,
        start_offset: int,
        end_offset: int,
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
