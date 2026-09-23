"""Detached closed-workspace Nova return-expression validation."""

from __future__ import annotations

import re
from typing import Any

from .diagnostics import Diagnostic
from .expression_local_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_RETURN = re.compile(r"\breturn\b")
_RETURN_ANNOTATION = re.compile(rf"->\s*(?P<type>{_IDENTIFIER}|!)\s*$")
_CLOSED_EXPECTED_RETURN_TYPES = frozenset({"Int", "String", "Bool"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Add bounded return diagnostics for detached workspace snapshots."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Diagnostic, ...]:
        diagnostics = list(
            super()._closed_workspace_product_diagnostics(snapshot, functions)
        )
        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)

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

        for symbol in snapshot.symbols.symbols:
            if symbol.kind != "function":
                continue
            annotation = self._closed_result_annotation(snapshot, symbol.span)
            if annotation is None:
                continue
            expected, expected_span = annotation
            if expected not in _CLOSED_EXPECTED_RETURN_TYPES:
                continue

            opening = code.find("{", symbol.span.end)
            if opening < 0:
                continue
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is None:
                continue

            body_text = text[opening + 1 : closing]
            body_code = code[opening + 1 : closing]
            for statement in _RETURN.finditer(body_code):
                keyword_end = opening + 1 + statement.end()
                boundary = closing
                for delimiter in (";", "\n", "\r"):
                    found = text.find(delimiter, keyword_end, closing)
                    if found >= 0:
                        boundary = min(boundary, found)

                raw = text[keyword_end:boundary]
                leading = len(raw) - len(raw.lstrip())
                expression = raw.strip()
                if not expression:
                    continue
                expression_span = Span(
                    keyword_end + leading,
                    keyword_end + leading + len(expression),
                )
                actual = self._closed_expression_type(
                    snapshot,
                    expression,
                    expression_span,
                    functions,
                    frozenset(),
                )
                if actual is None or actual == expected:
                    continue
                diagnostics.append(
                    Diagnostic(
                        span=expression_span,
                        message=(
                            f"return type mismatch: expected '{expected}', "
                            f"got '{actual}'"
                        ),
                        code="nova.return-type",
                        source="nova",
                    )
                )

            if self._body_guarantees_value_return(
                body_code,
                body_text,
                never_resolver=never_returns,
            ):
                continue

            tail = self._bounded_tail_expression(text, code, opening + 1, closing)
            if tail is not None:
                expression, expression_span = tail
                actual = self._closed_expression_type(
                    snapshot,
                    expression,
                    expression_span,
                    functions,
                    frozenset(),
                )
                if actual is not None:
                    if actual != expected and not any(
                        diagnostic.code == "nova.return-type"
                        and diagnostic.span == expression_span
                        for diagnostic in diagnostics
                    ):
                        diagnostics.append(
                            Diagnostic(
                                span=expression_span,
                                message=(
                                    f"return type mismatch: expected '{expected}', "
                                    f"got '{actual}'"
                                ),
                                code="nova.return-type",
                                source="nova",
                            )
                        )
                    continue

            diagnostics.append(
                Diagnostic(
                    span=expected_span,
                    message=(
                        f"function '{symbol.name}' with return type "
                        f"'{expected}' has no value return"
                    ),
                    code="nova.missing-return",
                    source="nova",
                )
            )

        return tuple(diagnostics)

    def _closed_result_annotation(
        self,
        snapshot: Any,
        owner: Span,
    ) -> tuple[str, Span] | None:
        """Return one explicit result annotation from captured detached source."""
        text = snapshot.symbols.syntax.document.text
        start = text.rfind("fn", 0, owner.start)
        opening = text.find("{", owner.end)
        if start < 0 or opening < 0 or text[start + 2 : owner.start].strip():
            return None
        signature = text[start:opening]
        match = _RETURN_ANNOTATION.search(signature)
        if match is None:
            return None
        return (
            match.group("type"),
            Span(start + match.start("type"), start + match.end("type")),
        )

    def _closed_declaration_never_returns(
        self,
        snapshot: Any,
        symbol: Any,
        functions: dict[str, list[tuple[Any, Any]]],
        resolving: frozenset[tuple[int, int, int]],
    ) -> bool:
        """Prove one captured declaration never returns without live workspace reads."""
        identity = (id(snapshot), symbol.span.start, symbol.span.end)
        if identity in resolving:
            return False

        annotation = self._closed_result_annotation(snapshot, symbol.span)
        if annotation is not None:
            return annotation[0] == "!"

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

        def never_returns(
            name: str,
            nested_resolving: frozenset[tuple[int, int, int]],
        ) -> bool:
            candidates = functions.get(name, [])
            if len(candidates) != 1:
                return False
            candidate_snapshot, candidate_symbol = candidates[0]
            return self._closed_declaration_never_returns(
                candidate_snapshot,
                candidate_symbol,
                functions,
                nested_resolving,
            )

        return self._body_guarantees_value_return(
            body_code,
            body_text,
            never_resolver=never_returns,
            never_resolving=resolving | {identity},
        )
