"""Exact-snapshot quick fixes for Nova literal argument type mismatches."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .nova import NovaFunctionSyntax
from .typed_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}

_ARGUMENT_TYPE_MESSAGE = re.compile(
    r"^argument (\d+) to '([^']+)' has type '[^']+'; expected '([^']+)'$"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with executable literal type-mismatch repairs."""

    def _closed_nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        """Add detached type repairs using captured diagnostic evidence only."""
        actions = super()._closed_nova_code_actions(
            uri,
            document,
            source,
            diagnostics,
            start_offset,
            end_offset,
        )
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.argument-type":
                continue
            if not self._diagnostic_overlaps(
                diagnostic,
                start_offset=start_offset,
                end_offset=end_offset,
            ):
                continue
            repair = self._argument_type_repair_from_diagnostic(diagnostic)
            if repair is None:
                continue
            actions.append(
                self._argument_type_action(
                    uri,
                    source,
                    diagnostic,
                    repair,
                )
            )
        return actions

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
        current = self.diagnostics.get(uri)
        if current is None or current.semantic.symbols.syntax.document is not document:
            return actions

        for diagnostic in diagnostics:
            if diagnostic.code != "nova.argument-type":
                continue
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue

            repair = self._argument_type_repair(uri, document, diagnostic)
            if repair is None:
                continue
            actions.append(
                self._argument_type_action(
                    uri,
                    source,
                    diagnostic,
                    repair,
                )
            )
        return actions

    def _argument_type_repair(
        self, uri: str, document: Any, diagnostic: Diagnostic
    ) -> tuple[str, int, str, str] | None:
        snapshot = self.workspace_symbols.get(uri)
        if snapshot is None or snapshot.symbols.syntax.document is not document:
            return None
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return None

        matches: list[tuple[str, int, str, str]] = []
        for name, call_span in tree.calls:
            parsed = self._call_argument_spans(document.text, call_span.end)
            if parsed is None:
                continue
            arguments = parsed[2]
            for argument_index, argument_span in enumerate(arguments):
                if argument_span != diagnostic.span:
                    continue
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) != 1:
                    continue
                parameter_types = self._declaration_parameter_types(declarations[0])
                if argument_index >= len(parameter_types):
                    continue
                expected_type = parameter_types[argument_index]
                if expected_type is None:
                    continue
                replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
                if replacement is None:
                    continue
                matches.append(
                    (name, argument_index + 1, expected_type, replacement)
                )

        if len(matches) != 1:
            return None
        repair = matches[0]
        captured = self._argument_type_repair_from_diagnostic(diagnostic)
        if captured != repair:
            return None
        return repair

    @staticmethod
    def _argument_type_repair_from_diagnostic(
        diagnostic: Diagnostic,
    ) -> tuple[str, int, str, str] | None:
        """Decode one repair whose policy is fully represented by the diagnostic."""
        match = _ARGUMENT_TYPE_MESSAGE.fullmatch(diagnostic.message)
        if match is None:
            return None
        index_text, name, expected_type = match.groups()
        replacement = _DEFAULT_LITERAL_BY_TYPE.get(expected_type)
        if replacement is None:
            return None
        return name, int(index_text), expected_type, replacement

    def _argument_type_action(
        self,
        uri: str,
        source: Any,
        diagnostic: Diagnostic,
        repair: tuple[str, int, str, str],
    ) -> dict[str, Any]:
        name, argument_index, expected_type, replacement = repair
        return {
            "title": (
                f"Replace argument {argument_index} to '{name}' "
                f"with {expected_type} literal"
            ),
            "kind": "quickfix",
            "diagnostics": [self._diagnostic(source, diagnostic)],
            "edit": {
                "changes": {
                    uri: [
                        {
                            "range": self._range(source, diagnostic.span),
                            "newText": replacement,
                        }
                    ]
                }
            },
        }

    @staticmethod
    def _diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
