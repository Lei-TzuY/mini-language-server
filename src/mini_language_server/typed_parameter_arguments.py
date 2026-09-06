"""Exact-workspace Nova argument type propagation for typed parameters."""

from __future__ import annotations

import re
from typing import Any

from .argument_type_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .diagnostics import Diagnostic
from .nova import NovaFunctionSyntax
from .source import Span
from .workspace import WorkspaceIndexError

_PARAMETER_TYPE_SUFFIX = re.compile(r"\s*:\s*([A-Za-z_][A-Za-z0-9_]*|!)")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact typed-parameter argument propagation."""

    def _publish_workspace_diagnostics(self) -> None:
        """Publish exact-workspace call diagnostics with bounded argument typing."""
        snapshots = self.workspace_symbols.snapshots()
        planned: list[tuple[Any, tuple[Diagnostic, ...]]] = []
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            current = self.diagnostics.get(snapshot.uri)
            if current is None or current.semantic is not snapshot:
                continue
            text = snapshot.symbols.syntax.document.text
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
                and not self._is_literal_unresolved_name(text, diagnostic)
            ]
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
                    actual_type = self._argument_type(snapshot, argument)
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

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Return a bounded actual type from a literal or exact parameter reference."""
        text = snapshot.symbols.syntax.document.text
        literal_type = self._literal_type(text[argument.start : argument.end])
        if literal_type is not None:
            return literal_type

        token = text[argument.start : argument.end].strip()
        if _IDENTIFIER.fullmatch(token) is None:
            return None
        references = tuple(
            reference for reference in snapshot.references if reference.span == argument
        )
        if len(references) != 1:
            return None
        target = references[0].target
        if target.kind != "parameter":
            return None

        match = _PARAMETER_TYPE_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        return match.group(1)
