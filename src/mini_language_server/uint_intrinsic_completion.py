"""Exact-snapshot completion for implemented Nova numeric intrinsics."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .uint_conversion_actions import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError

_MEMBER_PREFIX = re.compile(
    r"(?P<type>UInt|Int)\s*::\s*(?P<prefix>[A-Za-z_][A-Za-z0-9_]*)?$"
)
_INTRINSICS: dict[str, tuple[tuple[str, str], ...]] = {
    "UInt": (
        ("MIN", "constant: UInt"),
        ("MAX", "constant: UInt"),
        ("from", "fn UInt::from(value: Int) -> UInt"),
    ),
    "Int": (("from_uint", "fn Int::from_uint(value: UInt) -> Int"),),
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded member completion for numeric intrinsics."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, offset, _ = parsed
        if semantics is None:
            return super()._handle_workspace_completion(request_id, params)

        text = semantics.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        match = _MEMBER_PREFIX.search(code[:offset])
        if match is None:
            return super()._handle_workspace_completion(request_id, params)

        receiver = match.group("type")
        prefix = match.group("prefix") or ""
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = [
                {"label": label, "detail": detail}
                for label, detail in _INTRINSICS[receiver]
                if label.startswith(prefix)
            ]
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots,
                    lambda: self._current_semantic_result(
                        semantics, request_id, result
                    ),
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)