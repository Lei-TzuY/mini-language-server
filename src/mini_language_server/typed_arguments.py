"""Exact-workspace Nova argument type diagnostics."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .inlay_hints import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .workspace import WorkspaceIndexError

_INTEGER_LITERAL = re.compile(r"[+-]?\d+")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final product server with bounded exact-workspace call type checking."""

    def _publish_workspace_diagnostics(self) -> None:
        """Publish exact-workspace Nova call resolution, arity, and type diagnostics."""
        snapshots = self.workspace_symbols.snapshots()
        planned: list[tuple[Any, tuple[Diagnostic, ...]]] = []
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            current = self.diagnostics.get(snapshot.uri)
            if current is None or current.semantic is not snapshot:
                continue
            diagnostics = [
                diagnostic
                for diagnostic in current.diagnostics
                if diagnostic.code
                not in {
                    "nova.unresolved-function",
                    "nova.ambiguous-function",
                    "nova.argument-count",
                    "nova.argument-type",
                }
            ]
            text = snapshot.symbols.syntax.document.text
            for name, span in tree.calls:
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) == 0:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"unresolved function '{name}'",
                            code="nova.unresolved-function",
                            source="nova",
                        )
                    )
                    continue
                if len(declarations) > 1:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"ambiguous function call '{name}'",
                            code="nova.ambiguous-function",
                            source="nova",
                        )
                    )
                    continue

                declaration = declarations[0]
                expected = self._declaration_parameter_count(declaration)
                parsed = self._call_arguments(text, span.end)
                if parsed is None:
                    continue
                actual = len(parsed[2])
                if actual != expected:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            (
                                f"function '{name}' expects {expected} "
                                f"argument(s) but got {actual}"
                            ),
                            code="nova.argument-count",
                            source="nova",
                        )
                    )
                    continue

                typed_parameters = self._declaration_parameter_types(declaration)
                spanned = self._call_argument_spans(text, span.end)
                if spanned is None:
                    continue
                arguments = spanned[2]
                for index, (expected_type, argument) in enumerate(
                    zip(typed_parameters, arguments, strict=False), start=1
                ):
                    if expected_type is None:
                        continue
                    actual_type = self._literal_type(text[argument.start : argument.end])
                    if actual_type is None or actual_type == expected_type:
                        continue
                    diagnostics.append(
                        Diagnostic(
                            argument,
                            (
                                f"argument {index} to '{name}' has type "
                                f"'{actual_type}'; expected '{expected_type}'"
                            ),
                            code="nova.argument-type",
                            source="nova",
                        )
                    )
            planned.append((snapshot, tuple(diagnostics)))

        def publish() -> None:
            for snapshot, diagnostics in planned:
                self.publish_diagnostics(snapshot, diagnostics)

        try:
            self.workspace_symbols.commit_snapshots_if_current(snapshots, publish)
        except WorkspaceIndexError:
            return

    def _declaration_parameter_types(self, declaration: Any) -> tuple[str | None, ...]:
        signature = self._function_signature(declaration)
        parameters = self._signature_parameters(signature)
        result: list[str | None] = []
        for parameter in parameters:
            _, separator, type_name = parameter.partition(":")
            result.append(type_name.strip() if separator else None)
        return tuple(result)

    @staticmethod
    def _literal_type(argument: str) -> str | None:
        if _INTEGER_LITERAL.fullmatch(argument.strip()) is not None:
            return "Int"
        return None
