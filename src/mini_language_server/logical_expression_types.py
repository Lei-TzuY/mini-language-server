"""Bounded exact-snapshot typing for Nova logical boolean expressions."""

from __future__ import annotations

from typing import Any

from .comparison_expression_types import NovaProductLanguageServer as _NovaProductLanguageServer
from .source import Span


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with conservative logical-expression typing."""

    def _return_expression_type(
        self, semantic: Any, expression: str, span: Span
    ) -> str | None:
        present, logical_type = self._logical_type_if_present(
            semantic, expression, span
        )
        if present:
            return logical_type
        return super()._return_expression_type(semantic, expression, span)

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        text = snapshot.symbols.syntax.document.text
        expression = text[argument.start : argument.end]
        present, logical_type = self._logical_type_if_present(
            snapshot, expression, argument
        )
        if present:
            return logical_type
        return super()._argument_type(snapshot, argument)

    def _inference_expression_type(
        self,
        semantic: Any,
        expression: str,
        span: Span,
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)

        parts = self._split_top_level_logical(expression, span, "||")
        if parts is not None:
            if not parts:
                return None
            return self._all_bool_parts_type(semantic, parts, resolving)

        parts = self._split_top_level_logical(expression, span, "&&")
        if parts is not None:
            if not parts:
                return None
            return self._all_bool_parts_type(semantic, parts, resolving)

        if expression.startswith("!") and not expression.startswith("!="):
            operand_text = expression[1:]
            operand_span = Span(span.start + 1, span.end)
            operand_type = self._inference_expression_type(
                semantic,
                operand_text,
                operand_span,
                resolving,
            )
            return "Bool" if operand_type == "Bool" else None

        return super()._inference_expression_type(
            semantic,
            expression,
            span,
            resolving,
        )

    def _logical_type_if_present(
        self, semantic: Any, expression: str, span: Span
    ) -> tuple[bool, str | None]:
        expression, span = self._trim_expression(expression, span)
        expression, span = self._unwrap_expression_with_span(expression, span)
        if not self._has_logical_operator(expression):
            return False, None
        return True, self._inference_expression_type(
            semantic,
            expression,
            span,
            frozenset(),
        )

    def _all_bool_parts_type(
        self,
        semantic: Any,
        parts: tuple[tuple[str, Span], ...],
        resolving: frozenset[tuple[int, int, int]],
    ) -> str | None:
        for part_text, part_span in parts:
            if (
                self._inference_expression_type(
                    semantic,
                    part_text,
                    part_span,
                    resolving,
                )
                != "Bool"
            ):
                return None
        return "Bool"

    def _has_logical_operator(self, expression: str) -> bool:
        normalized = self._trim_expression(expression, Span(0, len(expression)))[0]
        normalized = self._unwrap_expression_with_span(
            normalized, Span(0, len(normalized))
        )[0]
        if normalized.startswith("!") and not normalized.startswith("!="):
            return True
        return (
            self._split_top_level_logical(
                normalized,
                Span(0, len(normalized)),
                "||",
            )
            is not None
            or self._split_top_level_logical(
                normalized,
                Span(0, len(normalized)),
                "&&",
            )
            is not None
        )

    def _split_top_level_logical(
        self,
        expression: str,
        span: Span,
        operator: str,
    ) -> tuple[tuple[str, Span], ...] | None:
        code = self.nova_adapter.code_view(expression)
        depth = 0
        offsets: list[int] = []
        offset = 0
        while offset < len(code):
            char = code[offset]
            if char == "(":
                depth += 1
                offset += 1
                continue
            if char == ")":
                depth -= 1
                if depth < 0:
                    return None
                offset += 1
                continue
            if depth == 0 and code.startswith(operator, offset):
                offsets.append(offset)
                offset += len(operator)
                continue
            offset += 1

        if depth != 0 or not offsets:
            return None

        parts: list[tuple[str, Span]] = []
        start = 0
        for operator_offset in (*offsets, len(expression)):
            end = operator_offset
            part_text = expression[start:end]
            part_span = Span(span.start + start, span.start + end)
            trimmed_text, trimmed_span = self._trim_expression(part_text, part_span)
            if not trimmed_text:
                return ()
            parts.append((trimmed_text, trimmed_span))
            start = end + len(operator)
        return tuple(parts)
