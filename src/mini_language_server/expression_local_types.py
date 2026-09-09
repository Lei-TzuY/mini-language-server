"""Exact-snapshot Nova local types from bounded initializer expressions."""

from __future__ import annotations

import re
from typing import Any

from .comparison_expression_types import NovaProductLanguageServer as _NovaProductLanguageServer

_UNANNOTATED_INITIALIZER_PREFIX = re.compile(r"\s*=\s*")


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded expression-derived local type knowledge."""

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer a local from inherited rules or one bounded exact initializer expression."""
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

        # Reuse the cycle-safe inference expression path rather than the public
        # argument dispatcher: exact references stay conservative and recursive
        # function calls retain their snapshot-bound resolving identity.
        return self._inference_expression_type(
            snapshot,
            expression,
            expression_span,
            frozenset(),
        )
