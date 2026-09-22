"""Exact-snapshot diagnostics for bounded Nova local declaration shapes."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .assignment_diagnostics import NovaProductLanguageServer as _NovaProductLanguageServer
from .constant_control_flow import proven_non_fallthrough_while_spans
from .constant_values import bounded_boolean_constant_value
from .diagnostics import Diagnostic
from .semantic import SemanticSnapshot
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_UNINITIALIZED_LOCAL = re.compile(
    rf"\b(?P<keyword>let|var)\s+(?P<name>{_IDENTIFIER})"
    rf"(?:\s*:\s*(?P<type>{_IDENTIFIER}|!))?\s*;"
)
_ASSIGNMENT_SUFFIX = re.compile(r"\s*=(?!=)")
_RETURN_STATEMENT = re.compile(r"\breturn\b[^{};]*;")
_UNINITIALIZED_LET_DIAGNOSTIC = "nova.uninitialized-let"
_UNTYPED_VAR_DIAGNOSTIC = "nova.untyped-var"
_UNINITIALIZED_READ_DIAGNOSTIC = "nova.uninitialized-read"
_DEFAULT_LITERAL_BY_TYPE = {
    "Int": "0",
    "String": '""',
    "Bool": "false",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded local declaration validation."""

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code
            not in {
                _UNINITIALIZED_LET_DIAGNOSTIC,
                _UNTYPED_VAR_DIAGNOSTIC,
                _UNINITIALIZED_READ_DIAGNOSTIC,
            }
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_local_declaration_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_local_declaration_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []

        for match in _UNINITIALIZED_LOCAL.finditer(code):
            keyword = match.group("keyword")
            name = match.group("name")
            if keyword == "let":
                diagnostics.append(
                    Diagnostic(
                        span=Span(*match.span("keyword")),
                        message=f"immutable local '{name}' requires an initializer",
                        code=_UNINITIALIZED_LET_DIAGNOSTIC,
                        source="nova",
                    )
                )
            elif match.group("type") is None:
                diagnostics.append(
                    Diagnostic(
                        span=Span(*match.span("name")),
                        message=f"uninitialized mutable local '{name}' requires an explicit type",
                        code=_UNTYPED_VAR_DIAGNOSTIC,
                        source="nova",
                    )
                )
            else:
                diagnostics.extend(
                    self._nova_reads_before_first_assignment(semantic, code, match)
                )

        return tuple(diagnostics)

    @staticmethod
    def _brace_scope_at(code: str, offset: int) -> tuple[int, ...]:
        """Return the structural brace ancestry containing ``offset``."""
        scope: list[int] = []
        for index, char in enumerate(code[:offset]):
            if char == "{":
                scope.append(index)
            elif char == "}" and scope:
                scope.pop()
        return tuple(scope)

    @staticmethod
    def _matching_braces(code: str) -> dict[int, int]:
        """Return matched structural brace pairs from the trivia-masked code view."""
        stack: list[int] = []
        pairs: dict[int, int] = {}
        for index, char in enumerate(code):
            if char == "{":
                stack.append(index)
            elif char == "}" and stack:
                pairs[stack.pop()] = index
        return pairs

    @staticmethod
    def _is_if_block(code: str, open_brace: int) -> bool:
        boundary = max(
            code.rfind(";", 0, open_brace),
            code.rfind("{", 0, open_brace),
            code.rfind("}", 0, open_brace),
        )
        prefix = code[boundary + 1 : open_brace]
        if re.search(r"\belse\s+if\b[^{};]*$", prefix) is not None:
            return False
        return re.search(r"\bif\b[^{};]*$", prefix) is not None

    @staticmethod
    def _direct_scope_assigned(
        branch_open: int,
        branch_close: int,
        branch_scope: tuple[int, ...],
        assignments: list[tuple[int, tuple[int, ...]]],
    ) -> bool:
        return any(
            branch_open < assignment_start < branch_close
            and assignment_scope == branch_scope
            for assignment_start, assignment_scope in assignments
        )

    def _direct_scope_terminates(
        self,
        code: str,
        text: str,
        branch_open: int,
        branch_close: int,
        branch_scope: tuple[int, ...],
    ) -> bool:
        if any(
            self._brace_scope_at(code, match.start()) == branch_scope
            for match in _RETURN_STATEMENT.finditer(code, branch_open + 1, branch_close)
        ):
            return True

        branch_code = code[branch_open + 1 : branch_close]
        if any(
            self._brace_scope_at(branch_code, statement_start) == ()
            for statement_start, _ in proven_non_fallthrough_while_spans(branch_code)
        ):
            return True

        branch_text = text[branch_open + 1 : branch_close]
        return bool(
            self._top_level_explicit_never_call_statements(
                branch_code, branch_text
            )
        )

    def _scope_definitely_assigned(
        self,
        code: str,
        text: str,
        branch_open: int,
        branch_close: int,
        branch_scope: tuple[int, ...],
        assignments: list[tuple[int, tuple[int, ...]]],
    ) -> bool:
        if self._direct_scope_assigned(
            branch_open, branch_close, branch_scope, assignments
        ):
            return True
        if self._direct_scope_terminates(
            code, text, branch_open, branch_close, branch_scope
        ):
            return True
        return self._complete_if_chain_initializes(
            code,
            text,
            branch_open + 1,
            branch_close,
            branch_scope,
            assignments,
        )

    def _complete_if_chain_initializes(
        self,
        code: str,
        text: str,
        start_offset: int,
        end_offset: int,
        reference_scope: tuple[int, ...],
        assignments: list[tuple[int, tuple[int, ...]]],
    ) -> bool:
        """Prove every reachable arm assigns or terminates before the join."""
        pairs = self._matching_braces(code)
        for if_open, if_close in sorted(pairs.items()):
            if if_open < start_offset or if_close >= end_offset:
                continue
            if self._brace_scope_at(code, if_open) != reference_scope:
                continue
            if not self._is_if_block(code, if_open):
                continue

            condition = self._if_condition_before_brace(code, if_open)
            constant = (
                None
                if condition is None
                else bounded_boolean_constant_value(condition)
            )
            branch_assigned = self._scope_definitely_assigned(
                code,
                text,
                if_open,
                if_close,
                reference_scope + (if_open,),
                assignments,
            )
            if constant is True:
                if branch_assigned:
                    return True
                continue

            all_reachable_assigned = True if constant is False else branch_assigned
            cursor = if_close + 1

            while cursor < end_offset:
                remainder = code[cursor:end_offset]
                else_if_match = re.match(
                    r"\s*else\s+if\b(?P<condition>[^{};]*)\{",
                    remainder,
                )
                if else_if_match is not None:
                    branch_open = cursor + else_if_match.end() - 1
                    branch_close = pairs.get(branch_open, -1)
                    if branch_close < branch_open or branch_close >= end_offset:
                        break
                    if self._brace_scope_at(code, branch_open) != reference_scope:
                        break

                    branch_constant = bounded_boolean_constant_value(
                        else_if_match.group("condition")
                    )
                    branch_assigned = self._scope_definitely_assigned(
                        code,
                        text,
                        branch_open,
                        branch_close,
                        reference_scope + (branch_open,),
                        assignments,
                    )
                    if branch_constant is True:
                        if all_reachable_assigned and branch_assigned:
                            return True
                        break
                    if branch_constant is None:
                        all_reachable_assigned = (
                            all_reachable_assigned and branch_assigned
                        )
                    cursor = branch_close + 1
                    continue

                else_match = re.match(r"\s*else\s*\{", remainder)
                if else_match is None:
                    break
                branch_open = cursor + else_match.end() - 1
                branch_close = pairs.get(branch_open, -1)
                if branch_close < branch_open or branch_close >= end_offset:
                    break
                if self._brace_scope_at(code, branch_open) != reference_scope:
                    break
                branch_assigned = self._scope_definitely_assigned(
                    code,
                    text,
                    branch_open,
                    branch_close,
                    reference_scope + (branch_open,),
                    assignments,
                )
                if all_reachable_assigned and branch_assigned:
                    return True
                break
        return False

    @staticmethod
    def _if_condition_before_brace(code: str, open_brace: int) -> str | None:
        """Return one direct if condition ending at the supplied body brace."""
        boundary = max(
            code.rfind(";", 0, open_brace),
            code.rfind("{", 0, open_brace),
            code.rfind("}", 0, open_brace),
        )
        prefix = code[boundary + 1 : open_brace]
        if re.search(r"\belse\s+if\b[^{};]*$", prefix) is not None:
            return None
        match = re.search(r"\bif\b(?P<condition>[^{};]*)$", prefix)
        if match is None:
            return None
        condition = match.group("condition").strip()
        return condition or None

    def _if_else_join_initializes(
        self,
        code: str,
        text: str,
        reference_start: int,
        reference_scope: tuple[int, ...],
        assignments: list[tuple[int, tuple[int, ...]]],
    ) -> bool:
        """Prove a bounded complete if/else-if/else join before the reference."""
        return self._complete_if_chain_initializes(
            code, text, 0, reference_start, reference_scope, assignments
        )

    def _nova_reads_before_first_assignment(
        self, semantic: SemanticSnapshot, code: str, declaration: re.Match[str]
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        name_span = Span(*declaration.span("name"))
        target = next(
            (
                symbol
                for symbol in semantic.symbols.symbols
                if symbol.kind == "variable" and symbol.span == name_span
            ),
            None,
        )
        if target is None:
            return ()

        references = sorted(
            (
                reference
                for reference in semantic.references
                if reference.target is target and reference.span.start > declaration.end()
            ),
            key=lambda reference: reference.span.start,
        )
        assignments: list[tuple[int, tuple[int, ...]]] = []
        diagnostics: list[Diagnostic] = []
        for reference in references:
            reference_scope = self._brace_scope_at(code, reference.span.start)
            if _ASSIGNMENT_SUFFIX.match(code, reference.span.end):
                assignments.append((reference.span.start, reference_scope))
                continue

            definitely_initialized = any(
                assignment_start < reference.span.start
                and len(assignment_scope) <= len(reference_scope)
                and reference_scope[: len(assignment_scope)] == assignment_scope
                for assignment_start, assignment_scope in assignments
            ) or self._if_else_join_initializes(
                code, text, reference.span.start, reference_scope, assignments
            )
            if definitely_initialized:
                continue
            diagnostics.append(
                Diagnostic(
                    span=reference.span,
                    message=f"local '{target.name}' is read before its first assignment",
                    code=_UNINITIALIZED_READ_DIAGNOSTIC,
                    source="nova",
                )
            )
        return tuple(diagnostics)

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

        code = self.nova_adapter.code_view(document.text)
        for diagnostic in diagnostics:
            if not any(item is diagnostic for item in current.diagnostics):
                continue
            if start_offset == end_offset:
                overlaps = diagnostic.span.start <= start_offset <= diagnostic.span.end
            else:
                overlaps = diagnostic.span.start < end_offset and start_offset < diagnostic.span.end
            if not overlaps:
                continue

            if diagnostic.code == _UNINITIALIZED_LET_DIAGNOSTIC:
                declaration = next(
                    (
                        match
                        for match in _UNINITIALIZED_LOCAL.finditer(code)
                        if match.span("keyword") == (diagnostic.span.start, diagnostic.span.end)
                    ),
                    None,
                )
                if declaration is None or declaration.group("type") is None:
                    continue
                actions.append(
                    {
                        "title": "Change uninitialized let declaration to var",
                        "kind": "quickfix",
                        "diagnostics": [self._diagnostic(source, diagnostic)],
                        "edit": {
                            "changes": {
                                uri: [
                                    {
                                        "range": self._range(source, diagnostic.span),
                                        "newText": "var",
                                    }
                                ]
                            }
                        },
                    }
                )
                continue

            if diagnostic.code != _UNINITIALIZED_READ_DIAGNOSTIC:
                continue
            reference = next(
                (
                    reference
                    for reference in current.semantic.references
                    if reference.span == diagnostic.span
                ),
                None,
            )
            if reference is None or reference.target is None or reference.target.kind != "variable":
                continue
            target = reference.target
            declaration = next(
                (
                    match
                    for match in _UNINITIALIZED_LOCAL.finditer(code)
                    if match.group("keyword") == "var"
                    and match.span("name") == (target.span.start, target.span.end)
                ),
                None,
            )
            if declaration is None:
                continue
            default_literal = _DEFAULT_LITERAL_BY_TYPE.get(declaration.group("type") or "")
            if default_literal is None:
                continue
            insert_offset = declaration.end() - 1
            actions.append(
                {
                    "title": f"Initialize '{target.name}' at declaration",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(
                                        source, Span(insert_offset, insert_offset)
                                    ),
                                    "newText": f" = {default_literal}",
                                }
                            ]
                        }
                    },
                }
            )
        return actions
