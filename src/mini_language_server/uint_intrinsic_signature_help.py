"""Exact-snapshot signature help for implemented Nova numeric intrinsics."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nova import NovaFunctionSyntax
from .uint_intrinsic_hover import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError

_INTRINSIC_CALL = re.compile(
    r"(?P<type>UInt|Int)\s*::\s*(?P<member>from_uint|from)\s*(?P<open>\()"
)
_SIGNATURES = {
    ("UInt", "from"): ("fn UInt::from(value: Int) -> UInt", "value: Int"),
    ("Int", "from_uint"): (
        "fn Int::from_uint(value: UInt) -> Int",
        "value: UInt",
    ),
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded numeric-intrinsic signature help."""

    def _handle_signature_help(self, request_id: Any, params: Any) -> dict[str, Any]:
        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")
        semantics, offset, _ = parsed
        if semantics is None:
            return self._result(request_id, None)

        tree = semantics.symbols.syntax.tree
        document = semantics.symbols.syntax.document
        intrinsic = self._containing_intrinsic_call(document.text, offset)
        if intrinsic is None:
            return super()._handle_signature_help(request_id, params)

        ordinary = None
        if isinstance(tree, NovaFunctionSyntax):
            ordinary = self._containing_call(document.text, tree, offset)
        if ordinary is not None and ordinary[1] > intrinsic[1]:
            return super()._handle_signature_help(request_id, params)

        label, parameter, opening = intrinsic
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = {
                "signatures": [
                    {
                        "label": label,
                        "parameters": [{"label": parameter}],
                    }
                ],
                "activeSignature": 0,
                "activeParameter": 0 if offset > opening else 0,
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

    def _containing_intrinsic_call(
        self, text: str, offset: int
    ) -> tuple[str, str, int] | None:
        code = self.nova_adapter.code_view(text)
        candidates: list[tuple[int, str, str]] = []
        for match in _INTRINSIC_CALL.finditer(code):
            signature = _SIGNATURES.get((match.group("type"), match.group("member")))
            if signature is None:
                continue
            opening = match.start("open")
            closing = self._matching_paren(code, opening)
            if closing is None or not (opening < offset <= closing):
                continue
            candidates.append((opening, signature[0], signature[1]))
        if not candidates:
            return None
        opening, label, parameter = max(candidates, key=lambda item: item[0])
        return label, parameter, opening
