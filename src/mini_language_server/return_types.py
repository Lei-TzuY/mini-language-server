"""Bounded exact-snapshot return-type semantics for Nova."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace

from .diagnostics import Diagnostic
from .range_formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .semantic import SemanticSnapshot
from .typed_local_annotations import TypedLocalNovaFunctionAdapter

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TYPED_FUNCTION = re.compile(
    rf"\bfn\s+(?P<name>{_IDENTIFIER})\s*\([^)]*\)\s*->\s*(?P<type>{_IDENTIFIER}|!)\s*\{{"
)
_RETURN = re.compile(r"\breturn\b")
_INTEGER = re.compile(r"-?[0-9]+")
_RETURN_TYPE_DIAGNOSTIC = "nova.return-type"


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
            if diagnostic.code != _RETURN_TYPE_DIAGNOSTIC
        )
        if document.language_id == self.nova_adapter.language_id:
            materialized += self._nova_return_type_diagnostics(document.text)
        return super().publish_diagnostics(semantic, materialized)

    def _nova_return_type_diagnostics(self, text: str) -> tuple[Diagnostic, ...]:
        code = self.nova_adapter.code_view(text)
        diagnostics: list[Diagnostic] = []
        for function in _TYPED_FUNCTION.finditer(code):
            expected = function.group("type")
            opening = function.end() - 1
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue
            body_code = code[opening + 1 : closing]
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
                actual = self._literal_type(expression)
                if actual is None or actual == expected:
                    continue
                start = keyword_end + leading
                diagnostics.append(
                    Diagnostic(
                        span=self._span(start, start + len(expression)),
                        message=(
                            f"return type mismatch: expected '{expected}', got '{actual}'"
                        ),
                        code=_RETURN_TYPE_DIAGNOSTIC,
                        source="nova",
                    )
                )
        return tuple(diagnostics)

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

    @staticmethod
    def _span(start: int, end: int):
        from .source import Span

        return Span(start, end)
