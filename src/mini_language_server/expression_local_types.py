"""Exact-snapshot Nova local types from bounded comparison initializers."""

from __future__ import annotations

import re
from typing import Any

from .comparison_expression_types import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)

_UNANNOTATED_INITIALIZER_PREFIX = re.compile(r"\s*=\s*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded comparison-derived local type knowledge."""

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer inherited local types, then one bounded comparison initializer."""
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

        # Preserve the existing conservative contract for generic compound locals:
        # this slice only promotes initializers owned by the comparison layer.
        normalized, normalized_span = self._trim_expression(
            expression, expression_span
        )
        normalized, normalized_span = self._unwrap_expression_with_span(
            normalized, normalized_span
        )
        if not self._top_level_comparison_operators(normalized):
            return None

        # Reuse the cycle-safe exact-snapshot inference path so call operands retain
        # recursive identity and ambiguous/mixed comparisons stay unknown.
        return self._inference_expression_type(
            snapshot,
            normalized,
            normalized_span,
            frozenset(),
        )
