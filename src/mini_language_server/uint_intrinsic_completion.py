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
_INTRINSICS: dict[str, tuple[tuple[str, str, str | None], ...]] = {
    "UInt": (
        ("MIN", "constant: UInt", None),
        ("MAX", "constant: UInt", None),
        ("from", "fn UInt::from(value: Int) -> UInt", "from(${1:value})"),
    ),
    "Int": (
        (
            "from_uint",
            "fn Int::from_uint(value: UInt) -> Int",
            "from_uint(${1:value})",
        ),
    ),
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with bounded member completion for numeric intrinsics."""

    def __init__(self) -> None:
        super().__init__()
        self._numeric_intrinsic_snippets = False

    def handle(self, message: Any) -> dict[str, Any] | None:
        if isinstance(message, dict) and message.get("method") == "initialize":
            self._numeric_intrinsic_snippets = self._client_supports_completion_snippets(
                message.get("params")
            )
        return super().handle(message)

    @staticmethod
    def _client_supports_completion_snippets(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        completion = text_document.get("completion")
        if not isinstance(completion, dict):
            return False
        completion_item = completion.get("completionItem")
        return isinstance(completion_item, dict) and completion_item.get(
            "snippetSupport"
        ) is True

    def _intrinsic_completion_item(
        self, label: str, detail: str, snippet: str | None
    ) -> dict[str, Any]:
        item: dict[str, Any] = {"label": label, "detail": detail}
        if self._numeric_intrinsic_snippets and snippet is not None:
            item["insertText"] = snippet
            item["insertTextFormat"] = 2
        return item

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
                self._intrinsic_completion_item(label, detail, snippet)
                for label, detail, snippet in _INTRINSICS[receiver]
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