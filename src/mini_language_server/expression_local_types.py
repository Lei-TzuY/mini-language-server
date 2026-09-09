"""Exact-snapshot Nova local types from bounded expression initializers."""

from __future__ import annotations

import re
from typing import Any

from .comparison_expression_types import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)

_UNANNOTATED_INITIALIZER_PREFIX = re.compile(r"\s*=\s*")
_ADDITIVE = frozenset({"+", "-"})
_MULTIPLICATIVE = frozenset({"*", "/", "%"})


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded expression-derived local type knowledge."""

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer inherited local types, then one bounded expression initializer."""
        inherited = super()._local_type(snapshot, target, seen)
        if inherited is not None:
            return inherited

        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None

        text = snapshot.symbols.syntax.document.text
        prefix = _UNANNOTATED_INITIALIZER_PREFIX.match(text, target.span.end)
        if prefix is None:
            return None
        initializer = self._local_initializer(text, prefix.end())
        if initializer is None:
            return None
        expression, expression_span = initializer

        # Only promote initializer forms owned by the bounded arithmetic/comparison
        # expression layers. Bare identifiers, calls, and literals remain delegated
        # to the inherited exact-snapshot local inference path above.
        normalized, normalized_span = self._trim_expression(
            expression, expression_span
        )
        normalized, normalized_span = self._unwrap_expression_with_span(
            normalized, normalized_span
        )
        owns_comparison = bool(self._top_level_comparison_operators(normalized))
        owns_arithmetic = self._top_level_operator(normalized, _ADDITIVE) is not None
        if not owns_arithmetic:
            owns_arithmetic = (
                self._top_level_operator(normalized, _MULTIPLICATIVE) is not None
            )
        if not owns_comparison and not owns_arithmetic:
            return None

        # Reuse the cycle-safe exact-snapshot inference path so function-call operands
        # retain recursive identity and mixed/ambiguous expressions stay unknown.
        return self._inference_expression_type(
            snapshot,
            normalized,
            normalized_span,
            frozenset(),
        )
