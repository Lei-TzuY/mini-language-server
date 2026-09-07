"""Explicit Nova local type annotations on exact semantic snapshots."""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any

from .formatting import NovaProductLanguageServer as _NovaProductLanguageServer
from .lexical_nova import LexicalNovaFunctionAdapter
from .nova import NovaFunctionSyntax

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_LOCAL_TYPE_SUFFIX = re.compile(
    rf"\s*:\s*(?P<type>{_IDENTIFIER}|!)\s*(?==)"
)


class TypedLocalNovaFunctionAdapter(LexicalNovaFunctionAdapter):
    """Accept explicit simple local types without publishing type names as references."""

    @classmethod
    def parse(cls, text: str) -> NovaFunctionSyntax:
        tree = super().parse(text)
        code = cls.code_view(text)
        annotation_spans: set[tuple[int, int, int]] = set()
        for local in tree.locals:
            match = _LOCAL_TYPE_SUFFIX.match(code, local.span.end)
            if match is None:
                continue
            annotation_spans.add(
                (local.owner.start, match.start("type"), match.end("type"))
            )

        if not annotation_spans:
            return tree
        return replace(
            tree,
            unresolved_names=tuple(
                unresolved
                for unresolved in tree.unresolved_names
                if (
                    unresolved.owner.start,
                    unresolved.span.start,
                    unresolved.span.end,
                )
                not in annotation_spans
            ),
        )


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with explicit local annotations in bounded type knowledge."""

    def __init__(self) -> None:
        super().__init__()
        self.nova_adapter = TypedLocalNovaFunctionAdapter()

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Prefer an explicit exact-snapshot local annotation over initializer inference."""
        text = snapshot.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        match = _LOCAL_TYPE_SUFFIX.match(code, target.span.end)
        if match is not None:
            return match.group("type")
        return super()._local_type(snapshot, target, seen)
