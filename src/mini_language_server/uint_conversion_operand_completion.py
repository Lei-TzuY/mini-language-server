"""Exact-snapshot completion for bounded Nova numeric conversion operands."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nova import NovaFunctionSyntax
from .source import Span
from .uint_conversion_range_diagnostics import (
    NovaProductLanguageServer as _NovaProductLanguageServer,
)
from .workspace import WorkspaceIndexError

_CONVERSION_START = re.compile(
    r"(?P<call>UInt\s*::\s*from|Int\s*::\s*from_uint)\s*\("
)
_IDENTIFIER_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*$")
_EXPECTED_TYPE = {
    "UInt::from": "Int",
    "Int::from_uint": "UInt",
}


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product with type-directed direct conversion-operand completion."""

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, offset, _ = parsed
        tree = None if semantics is None else semantics.symbols.syntax.tree
        if semantics is None or not isinstance(tree, NovaFunctionSyntax):
            return super()._handle_workspace_completion(request_id, params)

        text = semantics.symbols.syntax.document.text
        code = self.nova_adapter.code_view(text)
        operand = _direct_conversion_operand(code, offset)
        if operand is None:
            return super()._handle_workspace_completion(request_id, params)
        call_name, prefix = operand
        expected_type = _EXPECTED_TYPE[call_name]
        snapshots = self.workspace_symbols.snapshots()

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            owner = self._completion_scope_owner(text, tree, offset)
            visible_spans: set[Span] = set()
            if owner is not None:
                visible_spans.update(
                    parameter.span for parameter in tree.parameters if parameter.owner == owner
                )
                visible_spans.update(
                    local.span
                    for local in tree.locals
                    if local.owner == owner and local.span.end <= offset
                )

            items: dict[tuple[str, str], str] = {}
            for symbol in semantics.symbols.symbols:
                if symbol.span not in visible_spans:
                    continue
                symbol_type = self._symbol_type(semantics, symbol)
                if symbol_type != expected_type:
                    continue
                items[(symbol.name, symbol.kind)] = f"{symbol.kind}: {symbol_type}"

            if expected_type == "UInt":
                items[("UInt::MIN", "constant")] = "constant: UInt"
                items[("UInt::MAX", "constant")] = "constant: UInt"

            result = [
                {"label": name, "detail": items[(name, kind)]}
                for name, kind in sorted(items, key=lambda item: (item[0], item[1]))
                if name.startswith(prefix)
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


def _direct_conversion_operand(code: str, offset: int) -> tuple[str, str] | None:
    """Return a direct conversion operand context and current identifier prefix.

    Completion is intentionally conservative: the cursor must be at the first direct
    argument level, before any top-level comma, and the operand before the current
    identifier must contain only trivia. Nested calls/parentheses therefore fall back
    to the normal lexical completion pipeline rather than inheriting the conversion's
    expected type.
    """

    prefix_text = code[:offset]
    for match in reversed(tuple(_CONVERSION_START.finditer(prefix_text))):
        depth = 1
        saw_top_level_comma = False
        cursor = match.end()
        while cursor < len(prefix_text):
            char = prefix_text[cursor]
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            elif char == "," and depth == 1:
                saw_top_level_comma = True
            cursor += 1

        if depth != 1 or saw_top_level_comma:
            continue

        operand_text = prefix_text[match.end() :]
        identifier = _IDENTIFIER_PREFIX.search(operand_text)
        if identifier is None:
            if operand_text.strip():
                return None
            current_prefix = ""
        else:
            if operand_text[: identifier.start()].strip():
                return None
            current_prefix = identifier.group(0)

        call_name = re.sub(r"\s+", "", match.group("call"))
        return call_name, current_prefix
    return None
