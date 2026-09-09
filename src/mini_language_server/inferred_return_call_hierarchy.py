"""Exact-workspace Nova call hierarchy with conservative inferred returns."""

from __future__ import annotations

from typing import Any

from .inferred_return_signature_help import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with inferred return types in call-hierarchy details."""

    def _call_hierarchy_item(self, declaration: Any) -> dict[str, Any]:
        item = super()._call_hierarchy_item(declaration)
        detail = item.get("detail")
        if not isinstance(detail, str):
            return item
        if "->" in self.nova_adapter.code_view(detail):
            return item
        inferred = self._bounded_function_return_type(declaration)
        if inferred is None:
            return item
        return {**item, "detail": f"{detail} -> {inferred}"}
