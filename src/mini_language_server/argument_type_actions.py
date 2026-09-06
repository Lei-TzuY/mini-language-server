"""Exact-snapshot quick fixes for Nova literal argument type mismatches."""

from __future__ import annotations

from typing import Any

from .diagnostics import Diagnostic
from .nova import NovaFunctionSyntax
from .typed_arguments import NovaProductLanguageServer as _NovaProductLanguageServer

_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with executable literal type-mismatch repairs."""

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
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.argument-type":
                continue
            if not self._diagnostic_overlaps(
                diagnostic, start_offset=start_offset, end_offset=end_offset
            ):
                continue

            repair = self._argument_type_repair(uri, document, diagnostic)
            if repair is None:
                continue
            name, argument_index, expected_type, replacement = repair
            actions.append(
                {
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
        return matches[0]

    @staticmethod
    def _diagnostic_overlaps(
        diagnostic: Diagnostic, *, start_offset: int, end_offset: int
    ) -> bool:
        if start_offset == end_offset:
            return diagnostic.span.start <= start_offset <= diagnostic.span.end
        return diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
