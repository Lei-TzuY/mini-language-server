"""Product-oriented Nova LSP composition built on exact workspace snapshots."""

from __future__ import annotations

from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .diagnostics import Diagnostic, DiagnosticError
from .nova import NovaFunctionSyntax
from .server import ServerState
from .source import Position, SourceError, SourceText, Span
from .workspace import WorkspaceIndexError
from .workspace_lsp import WorkspaceNovaLanguageServer


class NovaProductLanguageServer(WorkspaceNovaLanguageServer):
    """Workspace Nova server with negotiated, version-safe product semantics."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/signatureHelp"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_signature_help(message.get("id"), message.get("params"))

        result = super().handle(message)
        if method == "initialize" and result is not None and "result" in result:
            params = message.get("params")
            if self._client_supports_signature_help(params):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["signatureHelpProvider"] = {
                        "triggerCharacters": ["(", ","],
                    }
        return result

    def _publish_workspace_diagnostics(self) -> None:
        """Publish exact-workspace Nova call resolution and argument-count diagnostics."""
        snapshots = self.workspace_symbols.snapshots()
        planned: list[tuple[Any, tuple[Diagnostic, ...]]] = []
        for snapshot in snapshots:
            tree = snapshot.symbols.syntax.tree
            if not isinstance(tree, NovaFunctionSyntax):
                continue
            current = self.diagnostics.get(snapshot.uri)
            if current is None or current.semantic is not snapshot:
                continue
            diagnostics = [
                diagnostic
                for diagnostic in current.diagnostics
                if diagnostic.code
                not in {
                    "nova.unresolved-function",
                    "nova.ambiguous-function",
                    "nova.argument-count",
                }
            ]
            text = snapshot.symbols.syntax.document.text
            for name, span in tree.calls:
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) == 0:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"unresolved function '{name}'",
                            code="nova.unresolved-function",
                            source="nova",
                        )
                    )
                elif len(declarations) > 1:
                    diagnostics.append(
                        Diagnostic(
                            span,
                            f"ambiguous function call '{name}'",
                            code="nova.ambiguous-function",
                            source="nova",
                        )
                    )
                else:
                    expected = self._declaration_parameter_count(declarations[0])
                    actual = self._call_argument_count(text, span.end)
                    if actual is not None and actual != expected:
                        diagnostics.append(
                            Diagnostic(
                                span,
                                (
                                    f"function '{name}' expects {expected} "
                                    f"argument(s) but got {actual}"
                                ),
                                code="nova.argument-count",
                                source="nova",
                            )
                        )
            planned.append((snapshot, tuple(diagnostics)))

        def publish() -> None:
            for snapshot, diagnostics in planned:
                self.publish_diagnostics(snapshot, diagnostics)

        try:
            self.workspace_symbols.commit_snapshots_if_current(snapshots, publish)
        except WorkspaceIndexError:
            return

    @staticmethod
    def _declaration_parameter_count(declaration: Any) -> int:
        tree = declaration.snapshot.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return 0
        owner = declaration.symbol.span
        return sum(1 for parameter in tree.parameters if parameter.owner == owner)

    @classmethod
    def _call_argument_count(cls, text: str, name_end: int) -> int | None:
        parsed = cls._call_arguments(text, name_end)
        if parsed is None:
            return None
        return len(parsed[2])

    @classmethod
    def _call_arguments(
        cls, text: str, name_end: int
    ) -> tuple[int, int, tuple[str, ...]] | None:
        opening = text.find("(", name_end)
        if opening < 0 or text[name_end:opening].strip():
            return None
        closing = cls._matching_paren(text, opening)
        if closing is None:
            return None
        body = text[opening + 1 : closing]
        if not body.strip():
            return opening, closing, ()

        arguments: list[str] = []
        depth = 0
        start = 0
        for index, character in enumerate(body):
            if character == "(":
                depth += 1
            elif character == ")" and depth:
                depth -= 1
            elif character == "," and depth == 0:
                arguments.append(body[start:index].strip())
                start = index + 1
        arguments.append(body[start:].strip())
        return opening, closing, tuple(arguments)

    def _handle_nova_code_action(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            source_range = params.get("range")
            action_context = params.get("context")
            if not isinstance(source_range, dict) or not isinstance(action_context, dict):
                return self._error(request_id, -32602, "Invalid params")
            start = source_range.get("start")
            end = source_range.get("end")
            if not isinstance(start, dict) or not isinstance(end, dict):
                return self._error(request_id, -32602, "Invalid params")
            only = action_context.get("only")
            if only is not None:
                valid_only = isinstance(only, list) and all(
                    isinstance(item, str) for item in only
                )
                if not valid_only:
                    return self._error(request_id, -32602, "Invalid params")
                supports_quickfix = any(
                    item == "quickfix" or item.startswith("quickfix.") for item in only
                )
                if not supports_quickfix:
                    self.requests.checkpoint(context)
                    return self._result(request_id, [])

            uri = self._document_uri(params)
            assert uri is not None
            document = self.documents.get(uri)
            if document is None or document.language_id != self.nova_adapter.language_id:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            source = SourceText(document.text)
            try:
                start_offset = source.offset_at(
                    Position(line=start.get("line"), character=start.get("character"))
                )
                end_offset = source.offset_at(
                    Position(line=end.get("line"), character=end.get("character"))
                )
            except SourceError:
                return self._error(request_id, -32602, "Invalid params")
            if end_offset < start_offset:
                return self._error(request_id, -32602, "Invalid params")

            self.requests.checkpoint(context)
            snapshot = self.diagnostics.get(uri)
            if snapshot is None or snapshot.semantic.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            workspace_snapshots = self.workspace_symbols.snapshots()

            actions = self._nova_code_actions(
                uri, document, source, snapshot.diagnostics, start_offset, end_offset
            )
            self.requests.checkpoint(context)

            def publish() -> dict[str, Any]:
                return self.diagnostics.commit_if_current(
                    snapshot, lambda: self._result(request_id, actions)
                )

            try:
                return self.workspace_symbols.commit_snapshots_if_current(
                    workspace_snapshots, publish
                )
            except (DiagnosticError, WorkspaceIndexError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _nova_code_actions(
        self,
        uri: str,
        document: Any,
        source: Any,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        actions = super()._nova_code_actions(
            uri, document, source, diagnostics, start_offset, end_offset
        )
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.argument-count":
                continue
            if start_offset == end_offset:
                overlaps = diagnostic.span.start <= start_offset <= diagnostic.span.end
            else:
                overlaps = (
                    diagnostic.span.start < end_offset
                    and start_offset < diagnostic.span.end
                )
            if not overlaps:
                continue

            name = document.text[diagnostic.span.start : diagnostic.span.end]
            declarations = tuple(
                declaration
                for declaration in self.workspace_symbols.declarations(name)
                if declaration.symbol.kind == "function"
            )
            if len(declarations) != 1:
                continue
            expected = self._declaration_parameter_count(declarations[0])
            parsed = self._call_arguments(document.text, diagnostic.span.end)
            if parsed is None:
                continue
            opening, closing, arguments = parsed
            if len(arguments) == expected:
                continue

            replacement = list(arguments[:expected])
            replacement.extend("0" for _ in range(expected - len(replacement)))
            actions.append(
                {
                    "title": f"Adjust '{name}' to {expected} argument(s)",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": self._range(
                                        source, Span(opening + 1, closing)
                                    ),
                                    "newText": ", ".join(replacement),
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    def _handle_signature_help(self, request_id: Any, params: Any) -> dict[str, Any]:
        parsed = self._semantic_query(params)
        if parsed is None:
            return self._error(request_id, -32602, "Invalid params")
        semantics, offset, _ = parsed
        if semantics is None:
            return self._result(request_id, None)
        tree = semantics.symbols.syntax.tree
        if not isinstance(tree, NovaFunctionSyntax):
            return self._result(request_id, None)

        document = semantics.symbols.syntax.document
        call = self._containing_call(document.text, tree, offset)
        snapshots = self.workspace_symbols.snapshots()
        try:
            context = self.requests.start(request_id, uri=semantics.uri)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            result: Any = None
            if call is not None:
                name, opening, active_parameter = call
                declarations = tuple(
                    declaration
                    for declaration in self.workspace_symbols.declarations(name)
                    if declaration.symbol.kind == "function"
                )
                if len(declarations) == 1:
                    label = self._function_signature(declarations[0])
                    parameters = self._signature_parameters(label)
                    signature: dict[str, Any] = {"label": label}
                    if parameters:
                        signature["parameters"] = [
                            {"label": parameter} for parameter in parameters
                        ]
                    result = {
                        "signatures": [signature],
                        "activeSignature": 0,
                    }
                    if parameters:
                        result["activeParameter"] = min(
                            active_parameter, len(parameters) - 1
                        )
                    elif offset > opening:
                        result["activeParameter"] = 0

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

    @classmethod
    def _containing_call(
        cls, text: str, tree: NovaFunctionSyntax, offset: int
    ) -> tuple[str, int, int] | None:
        candidates: list[tuple[int, str, int]] = []
        for name, span in tree.calls:
            opening = text.find("(", span.end)
            if opening < 0 or text[span.end:opening].strip():
                continue
            closing = cls._matching_paren(text, opening)
            if closing is None or not (opening < offset <= closing):
                continue
            candidates.append((opening, name, cls._active_parameter(text, opening, offset)))
        if not candidates:
            return None
        opening, name, active_parameter = max(candidates, key=lambda item: item[0])
        return name, opening, active_parameter

    @staticmethod
    def _matching_paren(text: str, opening: int) -> int | None:
        depth = 0
        for index in range(opening, len(text)):
            character = text[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    return index
        return None

    @staticmethod
    def _active_parameter(text: str, opening: int, offset: int) -> int:
        parameter = 0
        nested = 0
        for character in text[opening + 1 : offset]:
            if character == "(":
                nested += 1
            elif character == ")" and nested:
                nested -= 1
            elif character == "," and nested == 0:
                parameter += 1
        return parameter

    @staticmethod
    def _signature_parameters(signature: str) -> tuple[str, ...]:
        opening = signature.find("(")
        closing = signature.rfind(")")
        if opening < 0 or closing <= opening:
            return ()
        body = signature[opening + 1 : closing].strip()
        if not body:
            return ()
        return tuple(part.strip() for part in body.split(",") if part.strip())

    @staticmethod
    def _client_supports_signature_help(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("signatureHelp"), dict)