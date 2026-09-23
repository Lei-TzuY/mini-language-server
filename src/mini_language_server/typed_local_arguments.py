"""Exact-snapshot Nova argument type propagation for bounded locals."""

from __future__ import annotations

import re
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .nova import NovaFunctionSyntax
from .source import SourceText, Span
from .typed_parameter_arguments import NovaProductLanguageServer as _NovaProductLanguageServer
from .workspace import WorkspaceIndexError

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_LOCAL_INITIALIZER_SUFFIX = re.compile(
    r'\s*=\s*(?P<value>\d+|true\b|false\b|"(?:\\.|[^"\\])*"|[A-Za-z_][A-Za-z0-9_]*)'
    r"\s*(?=\}|let\b|[A-Za-z_][A-Za-z0-9_]*(?:\s*\(|\b)|$)"
)


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with bounded local type propagation."""

    def _closed_workspace_product_diagnostics(
        self,
        snapshot: Any,
        functions: dict[str, list[tuple[Any, Any]]],
    ) -> tuple[Any, ...]:
        """Extend detached call typing with exact same-snapshot reference evidence."""
        diagnostics = list(
            super()._closed_workspace_product_diagnostics(snapshot, functions)
        )
        tree = snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return tuple(diagnostics)

        text = snapshot.symbols.syntax.document.text
        for name, span in tree.calls:
            candidates = functions.get(name, [])
            if len(candidates) != 1:
                continue

            candidate_snapshot, candidate_symbol = candidates[0]
            expected_types = self._closed_function_parameter_types(
                candidate_snapshot,
                candidate_symbol.span,
            )
            parsed = self._call_argument_spans(text, span.end)
            if parsed is None:
                continue
            arguments = parsed[2]
            if len(arguments) != len(expected_types):
                continue

            for index, (expected_type, argument) in enumerate(
                zip(expected_types, arguments, strict=True),
                start=1,
            ):
                if expected_type is None:
                    continue
                actual_type = self._closed_reference_argument_type(
                    snapshot,
                    argument,
                )
                if actual_type is None or actual_type == expected_type:
                    continue
                diagnostics.append(
                    self._argument_type_diagnostic(
                        name,
                        index,
                        argument,
                        actual_type,
                        expected_type,
                    )
                )
        return tuple(diagnostics)

    def _closed_reference_argument_type(
        self,
        snapshot: Any,
        argument: Span,
    ) -> str | None:
        """Infer one detached argument from an exact parameter/local reference only."""
        text = snapshot.symbols.syntax.document.text
        token = text[argument.start : argument.end].strip()
        if _IDENTIFIER.fullmatch(token) is None:
            return None
        target = self._exact_reference_target(snapshot, argument)
        if target is None:
            return None

        parameter_type = self._parameter_type(snapshot, target)
        if parameter_type is not None:
            return parameter_type
        if target.kind != "variable":
            return None
        return self._closed_local_type(snapshot, target, frozenset())

    def _closed_local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer detached locals from literals and same-snapshot aliases only."""
        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None
        seen = seen | {identity}

        text = snapshot.symbols.syntax.document.text
        match = _LOCAL_INITIALIZER_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        value = match.group("value")
        literal_type = self._literal_type(value)
        if literal_type is not None:
            return literal_type
        if _IDENTIFIER.fullmatch(value) is None:
            return None

        value_span = Span(match.start("value"), match.end("value"))
        alias_target = self._exact_reference_target(snapshot, value_span)
        if alias_target is None:
            return None
        parameter_type = self._parameter_type(snapshot, alias_target)
        if parameter_type is not None:
            return parameter_type
        if alias_target.kind != "variable":
            return None
        return self._closed_local_type(snapshot, alias_target, seen)

    @staticmethod
    def _argument_type_diagnostic(
        name: str,
        index: int,
        argument: Span,
        actual_type: str,
        expected_type: str,
    ):
        from .diagnostics import Diagnostic

        return Diagnostic(
            argument,
            (
                f"argument {index} to '{name}' has type "
                f"'{actual_type}'; expected '{expected_type}'"
            ),
            code="nova.argument-type",
            source="nova",
        )

    def _argument_type(self, snapshot: Any, argument: Span) -> str | None:
        """Return a bounded actual type from literals, parameters, or local aliases."""
        inherited_type = super()._argument_type(snapshot, argument)
        if inherited_type is not None:
            return inherited_type

        target = self._exact_reference_target(snapshot, argument)
        if target is None or target.kind != "variable":
            return None
        return self._local_type(snapshot, target, frozenset())

    def _additional_inlay_hints(
        self,
        semantics: Any,
        source: SourceText,
        *,
        start_offset: int,
        end_offset: int,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Expose bounded local types inside the shared exact-snapshot hint gate."""
        hints = super()._additional_inlay_hints(
            semantics,
            source,
            start_offset=start_offset,
            end_offset=end_offset,
        )
        for symbol in semantics.symbols.symbols:
            if symbol.kind != "variable":
                continue
            if not (start_offset <= symbol.span.start < end_offset):
                continue
            symbol_type = self._symbol_type(semantics, symbol)
            if symbol_type is None:
                continue

            insertion_span = Span(symbol.span.end, symbol.span.end)
            insertion_range = self._range(source, insertion_span)
            annotation = f": {symbol_type}"
            hint: dict[str, Any] = {
                "position": insertion_range["start"],
                "label": annotation,
                "kind": 1,
                "paddingLeft": True,
            }
            if not self._has_explicit_local_annotation(semantics, symbol):
                hint["textEdits"] = [
                    {
                        "range": insertion_range,
                        "newText": annotation,
                    }
                ]
            hints.append((symbol.span.end, hint))
        return hints

    def _has_explicit_local_annotation(self, snapshot: Any, target: Any) -> bool:
        """Return whether an exact local declaration already has a type annotation."""
        code = self.nova_adapter.code_view(snapshot.symbols.syntax.document.text)
        offset = target.span.end
        while offset < len(code) and code[offset].isspace():
            offset += 1
        return offset < len(code) and code[offset] == ":"

    def _handle_workspace_completion(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Expose cursor-visible Nova names with bounded type/signature details."""
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_completion(request_id, params)
        semantics, offset, _ = parsed
        tree = None if semantics is None else semantics.symbols.syntax.tree
        if semantics is None or not isinstance(tree, NovaFunctionSyntax):
            return super()._handle_workspace_completion(request_id, params)

        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = self._typed_completion_items(
                semantics,
                offset,
                snapshots=snapshots,
            )
            self.requests.checkpoint(context)
            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    snapshots, lambda: self._result(request_id, result)
                )
            except WorkspaceIndexError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _typed_completion_items(
        self,
        semantics: Any,
        offset: int,
        *,
        snapshots: tuple[Any, ...],
    ) -> list[dict[str, Any]]:
        """Return lexical/type-aware Nova completion candidates from exact snapshots."""
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return []

        text = semantics.symbols.syntax.document.text
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
            if symbol.kind != "function" and symbol.span not in visible_spans:
                continue
            symbol_type = self._symbol_type(semantics, symbol)
            detail = symbol.kind
            if symbol_type is not None:
                detail = f"{symbol.kind}: {symbol_type}"
            items[(symbol.name, symbol.kind)] = detail

        for snapshot in snapshots:
            if not isinstance(snapshot.symbols.syntax.tree, NovaFunctionSyntax):
                continue
            for symbol in snapshot.symbols.symbols:
                if symbol.kind == "function":
                    items.setdefault((symbol.name, symbol.kind), symbol.kind)

        for name, kind in tuple(items):
            if kind != "function":
                continue
            declarations = tuple(
                declaration
                for declaration in self.workspace_symbols.declarations(name)
                if declaration.symbol.kind == "function"
            )
            if len(declarations) == 1:
                items[(name, kind)] = self._function_signature(declarations[0])

        return [
            {"label": name, "detail": items[(name, kind)]}
            for name, kind in sorted(items, key=lambda item: (item[0], item[1]))
        ]

    def _completion_scope_owner(
        self, text: str, tree: NovaFunctionSyntax, offset: int
    ) -> Span | None:
        """Return the exact function owner whose body contains the cursor."""
        for _, owner in tree.declarations:
            opening = text.find("{", owner.end)
            if opening < 0 or offset <= opening:
                continue
            closing = self.nova_adapter._matching_brace(text, opening)
            if closing is not None and offset <= closing:
                return owner
        return None

    def _handle_workspace_hover(
        self, request_id: Any, params: Any
    ) -> dict[str, Any] | None:
        """Expose bounded Nova parameter/local types from the exact semantic snapshot."""
        parsed = self._semantic_query(params)
        if parsed is None:
            return super()._handle_workspace_hover(request_id, params)
        semantics, offset, source = parsed
        if semantics is None:
            return super()._handle_workspace_hover(request_id, params)

        target = semantics.definition_at(offset)
        if target is None or target.kind not in {"parameter", "variable"}:
            return super()._handle_workspace_hover(request_id, params)

        target_type = self._symbol_type(semantics, target)
        if target_type is None:
            return super()._handle_workspace_hover(request_id, params)

        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result = {
                "contents": {
                    "kind": "plaintext",
                    "value": f"{target.kind} {target.name}: {target_type}",
                },
                "range": self._range(source, target.span),
            }
            self.requests.checkpoint(context)
            return self._current_semantic_result(semantics, request_id, result)
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _symbol_type(self, snapshot: Any, target: Any) -> str | None:
        """Return bounded type knowledge for one exact parameter or local symbol."""
        parameter_type = self._parameter_type(snapshot, target)
        if parameter_type is not None:
            return parameter_type
        if target.kind != "variable":
            return None
        return self._local_type(snapshot, target, frozenset())

    def _local_type(
        self,
        snapshot: Any,
        target: Any,
        seen: frozenset[tuple[int, int]],
    ) -> str | None:
        """Infer only literal or exact-reference local initializer types."""
        identity = (target.span.start, target.span.end)
        if identity in seen:
            return None
        seen = seen | {identity}

        text = snapshot.symbols.syntax.document.text
        match = _LOCAL_INITIALIZER_SUFFIX.match(text, target.span.end)
        if match is None:
            return None
        value = match.group("value")
        literal_type = self._literal_type(value)
        if literal_type is not None:
            return literal_type
        if _IDENTIFIER.fullmatch(value) is None:
            return None

        value_span = Span(match.start("value"), match.end("value"))
        inherited_type = super()._argument_type(snapshot, value_span)
        if inherited_type is not None:
            return inherited_type

        alias_target = self._exact_reference_target(snapshot, value_span)
        if alias_target is None or alias_target.kind != "variable":
            return None
        return self._local_type(snapshot, alias_target, seen)

    @staticmethod
    def _exact_reference_target(snapshot: Any, span: Span) -> Any | None:
        references = tuple(
            reference for reference in snapshot.references if reference.span == span
        )
        if len(references) != 1:
            return None
        return references[0].target