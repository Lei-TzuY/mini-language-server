"""Bounded exact-snapshot return-type semantics for Nova."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import replace

from .constant_control_flow import proven_non_fallthrough_while_spans
from .constant_values import bounded_boolean_constant_value
from .diagnostics import Diagnostic
from .range_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .source import Span
from .typed_local_annotations import TypedLocalNovaFunctionAdapter

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TYPED_FUNCTION = re.compile(
    rf"\bfn\s+(?P<name>{_IDENTIFIER})\s*\([^)]*\)\s*->\s*(?P<type>{_IDENTIFIER}|!)\s*\{{"
)
_RETURN = re.compile(r"\breturn\b")
_IF = re.compile(r"\bif\s*\(")
_INTEGER = re.compile(r"-?[0-9]+")
_CALL_EXPRESSION = re.compile(rf"\s*(?P<name>{_IDENTIFIER})\s*\(")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")
_RETURN_TYPE_DIAGNOSTIC = "nova.return-type"
_MISSING_RETURN_DIAGNOSTIC = "nova.missing-return"
_VALUE_RETURN_TYPES = frozenset({"Int", "String", "Bool"})
_EXPLICIT_CALL_RESULT_TYPES = frozenset({"Int", "String", "Bool", "Unit", "UInt"})


_NeverReturnsResolver = Callable[
    [str, frozenset[tuple[int, int, int]]],
    bool,
]


class ReturnTypeNovaFunctionAdapter(TypedLocalNovaFunctionAdapter):
    """Treat return and Boolean literals as executable Nova syntax."""

    @classmethod
    def parse(cls, text: str):
        tree = super().parse(text)
        return replace(
            tree,
            unresolved_names=tuple(
                item
                for item in tree.unresolved_names
                if item.name not in {"return", "true", "false"}
            ),
        )


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded explicit return-type validation."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = ReturnTypeNovaFunctionAdapter()

    def publish_diagnostics(
        self, semantic: SemanticSnapshot, diagnostics: Iterable[Diagnostic]
    ) -> bool:
        document = semantic.symbols.syntax.document
        materialized = tuple(
            diagnostic
            for diagnostic in diagnostics
            if diagnostic.code
            not in {_RETURN_TYPE_DIAGNOSTIC, _MISSING_RETURN_DIAGNOSTIC}
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_return_type_diagnostics(semantic)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_return_type_diagnostics(
        self, semantic: SemanticSnapshot
    ) -> tuple[Diagnostic, ...]:
        text = semantic.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for function in _TYPED_FUNCTION.finditer(code):
            expected = function.group("type")
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue
            body_text = text[opening + 1 : closing]
            body_code = code[opening + 1 : closing]
            guarantees_value_return = self._body_guarantees_value_return(
                body_code, body_text
            )
            for statement in _RETURN.finditer(body_code):
                keyword_end = opening + 1 + statement.end()
                boundary = len(text)
                for delimiter in (";", "\n", "\r"):
                    found = text.find(delimiter, keyword_end, closing)
                    if found >= 0:
                        boundary = min(boundary, found)
                boundary = min(boundary, closing)
                raw = text[keyword_end:boundary]
                leading = len(raw) - len(raw.lstrip())
                expression = raw.strip()
                if not expression:
                    continue
                start = keyword_end + leading
                expression_span = Span(start, start + len(expression))
                actual = self._return_expression_type(
                    semantic, expression, expression_span
                )
                if actual is None or actual == expected:
                    continue
                diagnostics.append(
                    Diagnostic(
                        span=expression_span,
                        message=(
                            f"return type mismatch: expected '{expected}', got '{actual}'"
                        ),
                        code=_RETURN_TYPE_DIAGNOSTIC,
                        source="nova",
                    )
                )
            if expected in _VALUE_RETURN_TYPES and not guarantees_value_return:
                diagnostics.append(
                    Diagnostic(
                        span=Span(function.start("type"), function.end("type")),
                        message=(
                            f"function '{function.group('name')}' with return type "
                            f"'{expected}' has no value return"
                        ),
                        code=_MISSING_RETURN_DIAGNOSTIC,
                        source="nova",
                    )
                )
        return tuple(diagnostics)

    def _body_guarantees_value_return(
        self,
        code: str,
        text: str,
        *,
        never_resolver: _NeverReturnsResolver | None = None,
        never_resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> bool:
        """Prove every fallthrough path is closed by a value return or divergence."""
        for statement in _RETURN.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if self._return_statement_has_value(text, statement.end()):
                return True

        for statement in _IF.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if self._if_statement_guarantees_value_return(
                code,
                text,
                statement.start(),
                statement.end(),
                never_resolver=never_resolver,
                never_resolving=never_resolving,
            ):
                return True

        if any(
            self._brace_depth_before(code, statement_start) == 0
            for statement_start, _ in proven_non_fallthrough_while_spans(code)
        ):
            return True

        return (
            not self._body_has_bare_return(code, text)
            and bool(
                self._top_level_never_call_statements(
                    code,
                    text,
                    resolver=never_resolver,
                    resolving=never_resolving,
                )
            )
        )

    def _if_statement_guarantees_value_return(
        self,
        code: str,
        text: str,
        statement_start: int,
        condition_prefix_end: int,
        *,
        never_resolver: _NeverReturnsResolver | None = None,
        never_resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> bool:
        condition = self._if_condition_then_bounds(
            code, statement_start, condition_prefix_end
        )
        if condition is None:
            return False
        condition_open, condition_close, then_open, then_close = condition
        constant = bounded_boolean_constant_value(
            code[condition_open + 1 : condition_close]
        )
        then_returns = self._body_guarantees_value_return(
            code[then_open + 1 : then_close],
            text[then_open + 1 : then_close],
            never_resolver=never_resolver,
            never_resolving=never_resolving,
        )
        if constant is True:
            return then_returns

        branches = self._if_then_else_bounds(code, statement_start, condition_prefix_end)
        if branches is None:
            return False
        _, _, else_start = branches
        if code[else_start] == "{":
            else_close = self._matching_delimiter(code, else_start, "{", "}")
            if else_close is None:
                return False
            else_returns = self._body_guarantees_value_return(
                code[else_start + 1 : else_close],
                text[else_start + 1 : else_close],
                never_resolver=never_resolver,
                never_resolving=never_resolving,
            )
        else:
            nested_if = _IF.match(code, else_start)
            if nested_if is None:
                return False
            else_returns = self._if_statement_guarantees_value_return(
                code,
                text,
                nested_if.start(),
                nested_if.end(),
                never_resolver=never_resolver,
                never_resolving=never_resolving,
            )

        if constant is False:
            return else_returns
        return then_returns and else_returns

    def _top_level_never_call_statements(
        self,
        code: str,
        text: str,
        *,
        resolver: _NeverReturnsResolver | None = None,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> tuple[tuple[int, int], ...]:
        """Return direct top-level call statements whose exact target never returns."""
        statements: list[tuple[int, int]] = []
        for match in _CALL_EXPRESSION.finditer(code):
            start = match.start("name")
            if self._brace_depth_before(code, start) != 0:
                continue

            boundary = max(
                code.rfind(";", 0, start),
                code.rfind("\n", 0, start),
                code.rfind("\r", 0, start),
                code.rfind("}", 0, start),
            )
            if code[boundary + 1 : start].strip():
                continue

            name = match.group("name")
            expression = text[start:]
            parsed = self._call_argument_bounds(expression, len(name))
            if parsed is None:
                continue
            closing = parsed[1]
            call_end = start + closing + 1

            statement_boundary = len(code)
            for delimiter in (";", "\n", "\r"):
                found = code.find(delimiter, call_end)
                if found >= 0:
                    statement_boundary = min(statement_boundary, found)
            if code[call_end:statement_boundary].strip():
                continue
            target_never_returns = (
                self._function_name_never_returns(name, resolving)
                if resolver is None
                else resolver(name, resolving)
            )
            if not target_never_returns:
                continue

            statement_end = statement_boundary
            if statement_end < len(code):
                statement_end += 1
            statements.append((start, statement_end))

        return tuple(statements)

    def _function_name_never_returns(
        self,
        name: str,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> bool:
        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return False
        return self._declaration_never_returns(declarations[0], resolving)

    def _declaration_never_returns(
        self,
        declaration: object,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> bool:
        snapshot = getattr(declaration, "snapshot", None)
        symbol = getattr(declaration, "symbol", None)
        if snapshot is None or symbol is None:
            return False
        identity = (id(snapshot), symbol.span.start, symbol.span.end)
        if identity in resolving:
            return False

        signature = self._function_signature(declaration)
        annotation = _RETURN_ANNOTATION.search(signature)
        if annotation is not None:
            return annotation.group("type") == "!"

        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        opening = code.find("{", symbol.span.end)
        if opening < 0:
            return False
        closing = self.nova_adapter._matching_brace(text, opening)
        if closing is None:
            return False

        body_code = code[opening + 1 : closing]
        body_text = text[opening + 1 : closing]
        if _RETURN.search(body_code) is not None:
            return False

        return self._body_guarantees_value_return(
            body_code,
            body_text,
            never_resolving=resolving | {identity},
        )

    def _explicit_function_result_annotation(self, name: str) -> str | None:
        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(name)
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return None
        signature = self._function_signature(declarations[0])
        annotation = _RETURN_ANNOTATION.search(signature)
        return None if annotation is None else annotation.group("type")

    def _body_has_bare_return(self, code: str, text: str) -> bool:
        return any(
            not self._return_statement_has_value(text, statement.end())
            for statement in _RETURN.finditer(code)
        )

    @staticmethod
    def _return_statement_has_value(text: str, keyword_end: int) -> bool:
        boundary = len(text)
        for delimiter in (";", "\n", "\r"):
            found = text.find(delimiter, keyword_end)
            if found >= 0:
                boundary = min(boundary, found)
        return bool(text[keyword_end:boundary].strip())

    @classmethod
    def _if_condition_then_bounds(
        cls, code: str, statement_start: int, condition_prefix_end: int
    ) -> tuple[int, int, int, int] | None:
        condition_open = code.find("(", statement_start, condition_prefix_end)
        if condition_open < 0:
            return None
        condition_close = cls._matching_delimiter(code, condition_open, "(", ")")
        if condition_close is None:
            return None
        then_open = cls._next_non_space(code, condition_close + 1)
        if then_open is None or code[then_open] != "{":
            return None
        then_close = cls._matching_delimiter(code, then_open, "{", "}")
        if then_close is None:
            return None
        return condition_open, condition_close, then_open, then_close

    @classmethod
    def _if_then_else_bounds(
        cls, code: str, statement_start: int, condition_prefix_end: int
    ) -> tuple[int, int, int] | None:
        condition = cls._if_condition_then_bounds(
            code, statement_start, condition_prefix_end
        )
        if condition is None:
            return None
        _, _, then_open, then_close = condition
        else_keyword = cls._next_non_space(code, then_close + 1)
        if else_keyword is None or not code.startswith("else", else_keyword):
            return None
        else_end = else_keyword + len("else")
        if else_end < len(code) and (code[else_end].isalnum() or code[else_end] == "_"):
            return None
        else_start = cls._next_non_space(code, else_end)
        if else_start is None:
            return None
        if code[else_start] == "{" or _IF.match(code, else_start) is not None:
            return then_open, then_close, else_start
        return None

    @staticmethod
    def _next_non_space(code: str, offset: int) -> int | None:
        while offset < len(code) and code[offset].isspace():
            offset += 1
        return offset if offset < len(code) else None

    @staticmethod
    def _matching_delimiter(
        code: str, opening: int, open_character: str, close_character: str
    ) -> int | None:
        depth = 0
        for offset in range(opening, len(code)):
            character = code[offset]
            if character == open_character:
                depth += 1
            elif character == close_character:
                depth -= 1
                if depth == 0:
                    return offset
        return None

    @staticmethod
    def _brace_depth_before(code: str, offset: int) -> int:
        """Return structural brace depth before one trivia-masked body offset."""
        depth = 0
        for character in code[:offset]:
            if character == "{":
                depth += 1
            elif character == "}" and depth > 0:
                depth -= 1
        return depth

    def _return_expression_type(
        self, semantic: SemanticSnapshot, expression: str, span: Span
    ) -> str | None:
        literal_type = self._literal_type(expression)
        if literal_type is not None:
            return literal_type
        call_type = self._function_call_return_type_for_semantic(
            semantic,
            expression,
        )
        if call_type is not None:
            return call_type
        if re.fullmatch(_IDENTIFIER, expression) is None:
            return None
        target = self._exact_reference_target(semantic, span)
        if target is None or target.kind not in {"parameter", "variable"}:
            return None
        return self._symbol_type(semantic, target)

    def _visible_function_call_declaration(
        self,
        semantic: SemanticSnapshot,
        expression: str,
    ) -> Any | None:
        """Resolve one direct call against the caller's exact import namespace."""
        code = self.nova_adapter.code_view(expression)
        match = _CALL_EXPRESSION.match(code)
        if match is None:
            return None
        parsed = self._call_argument_bounds(expression, match.end("name"))
        if parsed is None:
            return None
        closing = parsed[1]
        if code[closing + 1 :].strip():
            return None

        snapshots = self.workspace_symbols.snapshots()
        declarations = self._nova_visible_function_declarations(
            semantic,
            snapshots,
            match.group("name"),
        )
        return declarations[0] if len(declarations) == 1 else None

    def _function_call_return_type_for_semantic(
        self,
        semantic: SemanticSnapshot,
        expression: str,
        resolving: frozenset[tuple[int, int, int]] = frozenset(),
    ) -> str | None:
        """Resolve one explicit call result in the caller's import namespace."""
        declaration = self._visible_function_call_declaration(semantic, expression)
        if declaration is None:
            return None
        signature = self._function_signature(declaration)
        annotation = _RETURN_ANNOTATION.search(signature)
        if annotation is None:
            return None
        result_type = annotation.group("type")
        return (
            result_type
            if result_type in _EXPLICIT_CALL_RESULT_TYPES
            else None
        )

    def _function_call_return_type(self, expression: str) -> str | None:
        """Resolve a bounded explicit result type from one exact workspace call."""
        code = self.nova_adapter.code_view(expression)
        match = _CALL_EXPRESSION.match(code)
        if match is None:
            return None
        parsed = self._call_argument_bounds(expression, match.end("name"))
        if parsed is None:
            return None
        closing = parsed[1]
        if code[closing + 1 :].strip():
            return None

        result_type = self._explicit_function_result_annotation(match.group("name"))
        return result_type if result_type in _VALUE_RETURN_TYPES else None

    @staticmethod
    def _literal_type(expression: str) -> str | None:
        if _INTEGER.fullmatch(expression):
            return "Int"
        if expression in {"true", "false"}:
            return "Bool"
        if (
            len(expression) >= 2
            and expression[0] == expression[-1]
            and expression[0] in {"'", '"'}
        ):
            return "String"
        return None
