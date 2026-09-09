"""Bounded exact-snapshot return-type semantics for Nova."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

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

    def _body_guarantees_value_return(self, code: str, text: str) -> bool:
        """Prove a bounded body returns via a top-level return or complete if chain."""
        for statement in _RETURN.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if self._return_statement_has_value(text, statement.end()):
                return True

        for statement in _IF.finditer(code):
            if self._brace_depth_before(code, statement.start()) != 0:
                continue
            if self._if_statement_guarantees_value_return(
                code, text, statement.start(), statement.end()
            ):
                return True
        return False

    def _if_statement_guarantees_value_return(
        self, code: str, text: str, statement_start: int, condition_prefix_end: int
    ) -> bool:
        branches = self._if_then_else_bounds(code, statement_start, condition_prefix_end)
        if branches is None:
            return False
        then_open, then_close, else_start = branches
        if not self._body_guarantees_value_return(
            code[then_open + 1 : then_close], text[then_open + 1 : then_close]
        ):
            return False

        if code[else_start] == "{":
            else_close = self._matching_delimiter(code, else_start, "{", "}")
            if else_close is None:
                return False
            return self._body_guarantees_value_return(
                code[else_start + 1 : else_close], text[else_start + 1 : else_close]
            )

        nested_if = _IF.match(code, else_start)
        if nested_if is None:
            return False
        return self._if_statement_guarantees_value_return(
            code, text, nested_if.start(), nested_if.end()
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
    def _if_then_else_bounds(
        cls, code: str, statement_start: int, condition_prefix_end: int
    ) -> tuple[int, int, int] | None:
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
        call_type = self._function_call_return_type(expression)
        if call_type is not None:
            return call_type
        if re.fullmatch(_IDENTIFIER, expression) is None:
            return None
        target = self._exact_reference_target(semantic, span)
        if target is None or target.kind not in {"parameter", "variable"}:
            return None
        return self._symbol_type(semantic, target)

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

        declarations = tuple(
            declaration
            for declaration in self.workspace_symbols.declarations(match.group("name"))
            if declaration.symbol.kind == "function"
        )
        if len(declarations) != 1:
            return None
        signature = self._function_signature(declarations[0])
        annotation = _RETURN_ANNOTATION.search(signature)
        if annotation is None:
            return None
        result_type = annotation.group("type")
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
