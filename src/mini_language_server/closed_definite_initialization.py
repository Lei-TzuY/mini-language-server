"""Detached closed-workspace definite-initialization diagnostics and repairs."""

from __future__ import annotations

from typing import Any

from .closed_unreachable import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import Diagnostic
from .local_declaration_diagnostics import (
    _UNINITIALIZED_LOCAL,
    _UNINITIALIZED_READ_DIAGNOSTIC,
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Promote typed-var definite initialization into detached ownership."""

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
            if diagnostic.code != _UNINITIALIZED_READ_DIAGNOSTIC
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
        for declaration in _UNINITIALIZED_LOCAL.finditer(code):
            if declaration.group("keyword") != "var" or declaration.group("type") is None:
                continue
            diagnostics.extend(
                self._nova_reads_before_first_assignment(
                    snapshot,
                    code,
                    declaration,
                    never_resolver=never_returns,
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
        code = self.nova_adapter.code_view(document.text)
        for diagnostic in diagnostics:
            if diagnostic.code != _UNINITIALIZED_READ_DIAGNOSTIC:
                continue
            if not self._closed_uninitialized_read_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue

            name = document.text[diagnostic.span.start : diagnostic.span.end]
            reference_scope = self._brace_scope_at(code, diagnostic.span.start)
            candidates = [
                declaration
                for declaration in _UNINITIALIZED_LOCAL.finditer(code)
                if declaration.group("keyword") == "var"
                and declaration.group("type") is not None
                and declaration.group("name") == name
                and declaration.end() < diagnostic.span.start
                and self._declaration_visible_from_scope(
                    code,
                    declaration.start("name"),
                    reference_scope,
                )
            ]
            if len(candidates) != 1:
                continue
            action = self._uninitialized_read_action(
                uri,
                source,
                diagnostic,
                name,
                candidates[0],
            )
            if action is not None:
                actions.append(action)
        return actions

    def _declaration_visible_from_scope(
        self,
        code: str,
        declaration_start: int,
        reference_scope: tuple[int, ...],
    ) -> bool:
        declaration_scope = self._brace_scope_at(code, declaration_start)
        return (
            len(declaration_scope) <= len(reference_scope)
            and reference_scope[: len(declaration_scope)] == declaration_scope
        )

    @staticmethod
    def _closed_uninitialized_read_overlaps(
        diagnostic: Diagnostic,
        *,
        start_offset: int,
        end_offset: int,
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
