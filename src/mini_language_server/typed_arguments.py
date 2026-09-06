"""Exact-workspace Nova argument type diagnostics."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .inlay_hints import NovaProductLanguageServer as _NovaProductLanguageServer
from .nova import NovaFunctionSyntax
from .semantic_token_delta import SemanticTokenDeltaMixin
from .source import Span
from .workspace import WorkspaceIndexError

_INTEGER_LITERAL = re.compile(r"[+-]?\d+")
_STRING_LITERAL = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)
_BOOLEAN_LITERALS = frozenset({"true", "false"})


class NovaProductLanguageServer(SemanticTokenDeltaMixin, _NovaProductLanguageServer):
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

    @classmethod
    def _call_argument_bounds(
        cls, text: str, name_end: int
    ) -> tuple[int, int, tuple[tuple[int, int], ...]] | None:
        opening = text.find("(", name_end)
        if opening < 0 or text[name_end:opening].strip():
            return None
        closing = cls._matching_paren(text, opening)
        if closing is None:
            return None

        body_start = opening + 1
        body = text[body_start:closing]
        if not body.strip():
            return opening, closing, ()

        bounds: list[tuple[int, int]] = []
        depth = 0
        quoted = False
        escaped = False
        segment_start = 0
        for index, character in enumerate(body):
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
                continue
            if character == '"':
                quoted = True
            elif character == "(":
                depth += 1
            elif character == ")" and depth:
                depth -= 1
            elif character == "," and depth == 0:
                bounds.append(cls._trimmed_bounds(body, segment_start, index))
                segment_start = index + 1
        bounds.append(cls._trimmed_bounds(body, segment_start, len(body)))
        return opening, closing, tuple(
            (body_start + start, body_start + end)
            for start, end in bounds
            if start != end
        )

    @staticmethod
    def _trimmed_bounds(body: str, start: int, end: int) -> tuple[int, int]:
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        return start, end

    @classmethod
    def _call_arguments(
        cls, text: str, name_end: int
    ) -> tuple[int, int, tuple[str, ...]] | None:
        parsed = cls._call_argument_bounds(text, name_end)
        if parsed is None:
            return None
        opening, closing, bounds = parsed
        return opening, closing, tuple(text[start:end] for start, end in bounds)

    @classmethod
    def _call_argument_spans(
        cls, text: str, name_end: int
    ) -> tuple[int, int, tuple[Span, ...]] | None:
        parsed = cls._call_argument_bounds(text, name_end)
        if parsed is None:
            return None
        opening, closing, bounds = parsed
        return opening, closing, tuple(Span(start, end) for start, end in bounds)

    @staticmethod
    def _matching_paren(text: str, opening: int) -> int | None:
        depth = 0
        quoted = False
        escaped = False
        for index in range(opening, len(text)):
            character = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
                continue
            if character == '"':
                quoted = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return index
        return None

    @staticmethod
    def _active_parameter(text: str, opening: int, offset: int) -> int:
        parameter = 0
        nested = 0
        quoted = False
        escaped = False
        for character in text[opening + 1 : offset]:
            if quoted:
                if escaped:
                    escaped = False
                elif character == "\\":
                    escaped = True
                elif character == '"':
                    quoted = False
                continue
            if character == '"':
                quoted = True
            elif character == "(":
                nested += 1
            elif character == ")" and nested:
                nested -= 1
            elif character == "," and nested == 0:
                parameter += 1
        return parameter

    @staticmethod
    def _literal_type(argument: str) -> str | None:
        literal = argument.strip()
        if _INTEGER_LITERAL.fullmatch(literal) is not None:
            return "Int"
        if _STRING_LITERAL.fullmatch(literal) is not None:
            return "String"
        if literal in _BOOLEAN_LITERALS:
            return "Bool"
        return None
