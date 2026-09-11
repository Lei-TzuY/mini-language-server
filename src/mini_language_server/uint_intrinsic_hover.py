"""Exact-snapshot hover for implemented Nova numeric intrinsics."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .source import Span
from .uint_intrinsic_completion import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError

_INTRINSIC = re.compile(
    r"(?P<type>UInt|Int)\s*::\s*(?P<member>MIN|MAX|from_uint|from)\b"
)
_DETAILS = {
    ("UInt", "MIN"): "constant UInt::MIN: UInt",
    ("UInt", "MAX"): "constant UInt::MAX: UInt",
    ("UInt", "from"): "fn UInt::from(value: Int) -> UInt",
    ("Int", "from_uint"): "fn Int::from_uint(value: UInt) -> Int",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded hover for implemented numeric intrinsics."""

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_hover(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_hover(request_id, params)

        text = semantics.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        target: tuple[str, Span] | None = None
        for match in _INTRINSIC.finditer(code):
            member_start, member_end = match.span("member")
            if member_start <= offset <= member_end:
                detail = _DETAILS.get((match.group("type"), match.group("member")))
                if detail is not None:
                    target = (detail, Span(member_start, member_end))
                break
        if target is None:
            return super()._handle_workspace_hover(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        detail, span = target
        try:
            self.requests.checkpoint(context)
            result = {
                "contents": {"kind": "plaintext", "value": detail},
                "range": self._range(source, span),
            }
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
