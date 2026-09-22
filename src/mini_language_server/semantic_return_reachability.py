"""Consolidate Nova return inference with final semantic reachability facts."""

from __future__ import annotations

import re

from .nested_unreachable import NovaProductLanguageServer as _NovaProductLanguageServer


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Filter inferred return evidence through final bounded control-flow regions."""

    def _reachable_return_statements(
        self, body_code: str, body_text: str | None = None
    ) -> tuple[re.Match[str], ...]:
        candidates = super()._reachable_return_statements(body_code, body_text)
        if body_text is None:
            return candidates

        regions = self._unreachable_regions_in_body(
            body_code,
            body_text,
            base_offset=0,
            loop_depth=0,
            include_never_calls=True,
        )
        return tuple(
            statement
            for statement in candidates
            if not any(
                span.start <= statement.start() < span.end
                for span, _ in regions
            )
        )
