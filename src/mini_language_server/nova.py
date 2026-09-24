"""Executable Nova adapter layered on the language-independent tooling core."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .cancellation import RequestCancelled, StaleRequest
from .diagnostics import Diagnostic, DiagnosticError, DiagnosticRelatedInformation
from .documents import Document
from .semantic import Reference, SemanticError, SemanticSnapshot
from .server import LanguageServer, ServerState
from .source import Position, SourceError, SourceText, Span
from .symbols import Symbol, SymbolError
from .syntax import SyntaxError

_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_SIMPLE_TYPE_REF = rf"(?:{_IDENTIFIER}|!)"
_FUNCTION_DECLARATION = re.compile(
    rf"\b(?:private\s+)?fn\s+({_IDENTIFIER})\s*\(([^)]*)\)\s*"
    rf"(?:->\s*{_SIMPLE_TYPE_REF}\s*)?\{{"
)
_CALL = re.compile(rf"\b({_IDENTIFIER})\s*(?=\()")
_IMPORT_DECLARATION = re.compile(
    r"(?m)^[ \t]*import[ \t]+((?:\./|\.\./)(?:[A-Za-z0-9_.~%+-]+/)*"
    r"[A-Za-z0-9_.~%+-]+\.nova)[ \t]*;?[ \t]*\r?$"
)
_SELECTIVE_IMPORT_DECLARATION = re.compile(
    r"(?m)^[ \t]*import[ \t]*\{([^}\r\n]*)\}[ \t]+from[ \t]+"
    r"((?:\./|\.\./)(?:[A-Za-z0-9_.~%+-]+/)*"
    r"[A-Za-z0-9_.~%+-]+\.nova)[ \t]*;?[ \t]*\r?$"
)
_IMPORT_NAME = re.compile(rf"(?:^|,)[ \t]*({_IDENTIFIER})[ \t]*(?=,|$)")
_ALIASED_IMPORT_NAME = re.compile(
    rf"(?:^|,)[ \t]*({_IDENTIFIER})(?:[ \t]+as[ \t]+({_IDENTIFIER}))?[ \t]*(?=,|$)"
)
_IMPORT_ALIAS_MARKER = re.compile(r"[ \t]+as[ \t]+")
_EXPORT_DECLARATION = re.compile(
    r"(?m)^[ \t]*export[ \t]*\{([^}\r\n]*)\}[ \t]*;?[ \t]*\r?$"
)
_EXPORT_NAME = re.compile(rf"(?:^|,)[ \t]*({_IDENTIFIER})[ \t]*(?=,|$)")
_IDENTIFIER_MATCH = re.compile(rf"\b({_IDENTIFIER})\b")
_PARAMETER_PART = re.compile(r"[^,]+")
_PARAMETER = re.compile(
    rf"^\s*({_IDENTIFIER})(?:\s*:\s*{_SIMPLE_TYPE_REF})?\s*$"
)
_LOCAL_DECLARATION = re.compile(rf"\b(?:let|var)\s+({_IDENTIFIER})\b")
_KEYWORDS = frozenset({"fn", "let", "var"})


@dataclass(frozen=True, slots=True)
class NovaScopedName:
    """A Nova name anchored to one exact function declaration span."""

    owner: Span
    name: str
    span: Span


@dataclass(frozen=True, slots=True)
class NovaImportNameSyntax:
    """One exact selected function name and optional importer-local alias."""

    name: str
    span: Span
    alias: str | None = None
    alias_span: Span | None = None

    @property
    def binding_name(self) -> str:
        """Return the importer-local spelling for this selected declaration."""
        return self.alias or self.name

    @property
    def binding_span(self) -> Span:
        """Return the exact syntax span that owns the importer-local spelling."""
        return self.alias_span or self.span


@dataclass(frozen=True, slots=True)
class NovaImportSyntax:
    """One exact URI-relative Nova file dependency declaration."""

    path: str
    span: Span
    names: tuple[NovaImportNameSyntax, ...] = ()
    has_name_list: bool = False


@dataclass(frozen=True, slots=True)
class NovaExportSyntax:
    """One exact explicit function export name."""

    name: str
    span: Span


@dataclass(frozen=True, slots=True)
class NovaFunctionSyntax:
    """Immutable syntax payload for Nova functions, calls, parameters, and locals."""

    declarations: tuple[tuple[str, Span], ...]
    calls: tuple[tuple[str, Span], ...]
    private_declarations: tuple[Span, ...] = ()
    imports: tuple[NovaImportSyntax, ...] = ()
    exports: tuple[NovaExportSyntax, ...] = ()
    has_export_list: bool = False
    parameters: tuple[NovaScopedName, ...] = ()
    parameter_references: tuple[NovaScopedName, ...] = ()
    locals: tuple[NovaScopedName, ...] = ()
    local_references: tuple[NovaScopedName, ...] = ()
    unresolved_names: tuple[NovaScopedName, ...] = ()


class NovaFunctionAdapter:
    """Analyze a bounded executable Nova subset without leaking rules into core stores.

    The adapter owns named ``fn`` / ``private fn`` declarations, identifier calls,
    bare legacy or
    Nova-style typed parameters with simple identifier/never surface types, optional
    explicit simple return types, and function-scoped ``let``/``var`` variables.
    Parameter and local references resolve only inside the owning function body. A
    local takes precedence after its declaration when exactly one preceding local with
    that name exists; otherwise a unique parameter remains visible. Function calls
    resolve only when exactly one function declaration with that name exists. Bare
    identifiers that cannot resolve to a visible parameter or local are published as
    deterministic diagnostics.
    """

    language_id = "nova"

    @staticmethod
    def _matching_brace(text: str, opening: int) -> int | None:
        depth = 0
        for offset in range(opening, len(text)):
            character = text[offset]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    return offset
        return None

    @staticmethod
    def _parameters(
        parameter_text: str, base_offset: int, owner: Span
    ) -> tuple[NovaScopedName, ...]:
        parameters: list[NovaScopedName] = []
        for part in _PARAMETER_PART.finditer(parameter_text):
            raw = part.group(0)
            match = _PARAMETER.fullmatch(raw)
            if match is None:
                continue
            name = match.group(1)
            start = base_offset + part.start() + match.start(1)
            parameters.append(
                NovaScopedName(owner, name, Span(start, start + len(name)))
            )
        return tuple(parameters)

    @classmethod
    def parse(cls, text: str) -> NovaFunctionSyntax:
        declarations: list[tuple[str, Span]] = []
        private_declarations: list[Span] = []
        bare_import_candidates = tuple(
            NovaImportSyntax(
                match.group(1),
                Span(match.start(1), match.end(1)),
            )
            for match in _IMPORT_DECLARATION.finditer(text)
        )
        selective_import_declarations = tuple(
            _SELECTIVE_IMPORT_DECLARATION.finditer(text)
        )
        selective_import_candidates = tuple(
            NovaImportSyntax(
                declaration.group(2),
                Span(declaration.start(2), declaration.end(2)),
                names=tuple(
                    NovaImportNameSyntax(
                        name.group(1),
                        Span(
                            declaration.start(1) + name.start(1),
                            declaration.start(1) + name.end(1),
                        ),
                        alias=(
                            name.group(2)
                            if name.re is _ALIASED_IMPORT_NAME
                            else None
                        ),
                        alias_span=(
                            None
                            if name.re is _IMPORT_NAME or name.group(2) is None
                            else Span(
                                declaration.start(1) + name.start(2),
                                declaration.start(1) + name.end(2),
                            )
                        ),
                    )
                    for name in (
                        _ALIASED_IMPORT_NAME.finditer(declaration.group(1))
                        if _IMPORT_ALIAS_MARKER.search(declaration.group(1))
                        else _IMPORT_NAME.finditer(declaration.group(1))
                    )
                ),
                has_name_list=True,
            )
            for declaration in selective_import_declarations
        )
        import_candidates = tuple(
            sorted(
                (*bare_import_candidates, *selective_import_candidates),
                key=lambda item: item.span.start,
            )
        )
        export_declarations = tuple(_EXPORT_DECLARATION.finditer(text))
        export_candidates = tuple(
            NovaExportSyntax(
                name.group(1),
                Span(
                    declaration.start(1) + name.start(1),
                    declaration.start(1) + name.end(1),
                ),
            )
            for declaration in export_declarations
            for name in _EXPORT_NAME.finditer(declaration.group(1))
        )
        parameters: list[NovaScopedName] = []
        parameter_references: list[NovaScopedName] = []
        locals_: list[NovaScopedName] = []
        local_references: list[NovaScopedName] = []
        unresolved_names: list[NovaScopedName] = []
        declaration_spans: set[Span] = set()

        matches = tuple(_FUNCTION_DECLARATION.finditer(text))
        function_extents: list[Span] = []
        for match in matches:
            opening_brace = match.end() - 1
            closing_brace = cls._matching_brace(text, opening_brace)
            if closing_brace is not None:
                function_extents.append(Span(match.start(), closing_brace + 1))
        imports = tuple(
            item
            for item in import_candidates
            if not any(
                extent.start <= item.span.start < extent.end
                for extent in function_extents
            )
        )
        exports = tuple(
            item
            for item in export_candidates
            if not any(
                extent.start <= item.span.start < extent.end
                for extent in function_extents
            )
        )
        has_export_list = any(
            not any(
                extent.start <= declaration.start() < extent.end
                for extent in function_extents
            )
            for declaration in export_declarations
        )
        call_spans = {
            Span(match.start(1), match.end(1)) for match in _CALL.finditer(text)
        }

        for match in matches:
            owner = Span(match.start(1), match.end(1))
            declarations.append((match.group(1), owner))
            if text[match.start() : match.start(1)].startswith("private"):
                private_declarations.append(owner)
            declaration_spans.add(owner)

            scoped_parameters = cls._parameters(match.group(2), match.start(2), owner)
            parameters.extend(scoped_parameters)
            parameters_by_name: dict[str, list[NovaScopedName]] = {}
            for parameter in scoped_parameters:
                parameters_by_name.setdefault(parameter.name, []).append(parameter)

            opening_brace = match.end() - 1
            closing_brace = cls._matching_brace(text, opening_brace)
            if closing_brace is None:
                continue
            body_start = opening_brace + 1
            body = text[body_start:closing_brace]

            scoped_locals = tuple(
                NovaScopedName(
                    owner,
                    local.group(1),
                    Span(
                        body_start + local.start(1),
                        body_start + local.end(1),
                    ),
                )
                for local in _LOCAL_DECLARATION.finditer(body)
            )
            locals_.extend(scoped_locals)
            local_declaration_spans = {item.span for item in scoped_locals}
            locals_by_name: dict[str, list[NovaScopedName]] = {}
            for local in scoped_locals:
                locals_by_name.setdefault(local.name, []).append(local)

            for identifier in _IDENTIFIER_MATCH.finditer(body):
                name = identifier.group(1)
                span = Span(
                    body_start + identifier.start(1),
                    body_start + identifier.end(1),
                )
                if (
                    name in _KEYWORDS
                    or span in call_spans
                    or span in declaration_spans
                    or span in local_declaration_spans
                ):
                    continue

                preceding_locals = [
                    local
                    for local in locals_by_name.get(name, [])
                    if local.span.end <= span.start
                ]
                if len(preceding_locals) == 1:
                    local_references.append(NovaScopedName(owner, name, span))
                    continue
                if len(preceding_locals) > 1:
                    continue

                candidates = parameters_by_name.get(name, [])
                if len(candidates) == 1:
                    parameter_references.append(NovaScopedName(owner, name, span))
                elif not candidates:
                    unresolved_names.append(NovaScopedName(owner, name, span))

        calls = tuple(
            (match.group(1), Span(match.start(1), match.end(1)))
            for match in _CALL.finditer(text)
            if Span(match.start(1), match.end(1)) not in declaration_spans
        )
        return NovaFunctionSyntax(
            declarations=tuple(declarations),
            calls=calls,
            private_declarations=tuple(private_declarations),
            imports=imports,
            exports=exports,
            has_export_list=has_export_list,
            parameters=tuple(parameters),
            parameter_references=tuple(parameter_references),
            locals=tuple(locals_),
            local_references=tuple(local_references),
            unresolved_names=tuple(unresolved_names),
        )

    def publish(self, server: LanguageServer, document: Document) -> SemanticSnapshot:
        """Publish one exact Nova snapshot chain and its deterministic diagnostics."""
        parsed = self.parse(document.text)
        syntax = server.syntax.publish(document, parsed)
        symbols = server.symbols.publish(
            syntax,
            (
                *(
                    Symbol(name, "function", span)
                    for name, span in parsed.declarations
                ),
                *(
                    Symbol(parameter.name, "parameter", parameter.span)
                    for parameter in parsed.parameters
                ),
                *(
                    Symbol(local.name, "variable", local.span)
                    for local in parsed.locals
                ),
            ),
        )

        functions_by_name: dict[str, list[Symbol]] = {}
        symbols_by_span = {symbol.span: symbol for symbol in symbols.symbols}
        for symbol in symbols.symbols:
            if symbol.kind == "function":
                functions_by_name.setdefault(symbol.name, []).append(symbol)

        parameters_by_scope: dict[tuple[int, str], list[Symbol]] = {}
        for parameter in parsed.parameters:
            symbol = symbols_by_span[parameter.span]
            key = (parameter.owner.start, parameter.name)
            parameters_by_scope.setdefault(key, []).append(symbol)

        locals_by_scope: dict[tuple[int, str], list[Symbol]] = {}
        for local in parsed.locals:
            symbol = symbols_by_span[local.span]
            key = (local.owner.start, local.name)
            locals_by_scope.setdefault(key, []).append(symbol)

        references: list[Reference] = []
        diagnostics: list[Diagnostic] = []

        def duplicate_related_information(
            candidate: Symbol,
            candidates: list[Symbol],
            *,
            kind: str,
            name: str,
        ) -> tuple[DiagnosticRelatedInformation, ...]:
            return tuple(
                DiagnosticRelatedInformation(
                    document.uri,
                    other.span,
                    f"conflicting {kind} declaration '{name}' is here",
                )
                for other in candidates
                if other is not candidate
            )

        for name, candidates in sorted(functions_by_name.items()):
            if len(candidates) <= 1:
                continue
            for candidate in candidates:
                diagnostics.append(
                    Diagnostic(
                        candidate.span,
                        f"duplicate function declaration '{name}'",
                        code="nova.duplicate-function",
                        source="nova",
                        related_information=duplicate_related_information(
                            candidate,
                            candidates,
                            kind="function",
                            name=name,
                        ),
                    )
                )

        for (_, name), candidates in sorted(parameters_by_scope.items()):
            if len(candidates) <= 1:
                continue
            for candidate in candidates:
                diagnostics.append(
                    Diagnostic(
                        candidate.span,
                        f"duplicate parameter '{name}'",
                        code="nova.duplicate-parameter",
                        source="nova",
                        related_information=duplicate_related_information(
                            candidate,
                            candidates,
                            kind="parameter",
                            name=name,
                        ),
                    )
                )

        for (_, name), candidates in sorted(locals_by_scope.items()):
            if len(candidates) <= 1:
                continue
            for candidate in candidates:
                diagnostics.append(
                    Diagnostic(
                        candidate.span,
                        f"duplicate local variable '{name}'",
                        code="nova.duplicate-variable",
                        source="nova",
                        related_information=duplicate_related_information(
                            candidate,
                            candidates,
                            kind="local",
                            name=name,
                        ),
                    )
                )

        for reference in parsed.parameter_references:
            candidates = parameters_by_scope.get(
                (reference.owner.start, reference.name), []
            )
            if len(candidates) == 1:
                references.append(Reference(reference.span, candidates[0]))

        for reference in parsed.local_references:
            candidates = [
                candidate
                for candidate in locals_by_scope.get(
                    (reference.owner.start, reference.name), []
                )
                if candidate.span.end <= reference.span.start
            ]
            if len(candidates) == 1:
                references.append(Reference(reference.span, candidates[0]))

        for unresolved in parsed.unresolved_names:
            diagnostics.append(
                Diagnostic(
                    unresolved.span,
                    f"unresolved name '{unresolved.name}'",
                    code="nova.unresolved-name",
                    source="nova",
                )
            )

        for name, span in parsed.calls:
            candidates = functions_by_name.get(name, [])
            if len(candidates) == 1:
                references.append(Reference(span, candidates[0]))
            elif not candidates:
                diagnostics.append(
                    Diagnostic(
                        span,
                        f"unresolved function '{name}'",
                        code="nova.unresolved-function",
                        source="nova",
                    )
                )
            else:
                diagnostics.append(
                    Diagnostic(
                        span,
                        f"ambiguous function call '{name}'",
                        code="nova.ambiguous-function",
                        source="nova",
                    )
                )

        semantic = server.semantics.publish(symbols, references)
        server.publish_diagnostics(semantic, diagnostics)
        return semantic


class NovaLanguageServer(LanguageServer):
    """LanguageServer composition that automatically analyzes open Nova documents."""

    def __init__(self, adapter: NovaFunctionAdapter | None = None) -> None:
        super().__init__()
        self.nova_adapter = adapter or NovaFunctionAdapter()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/codeAction"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_nova_code_action(message.get("id"), message.get("params"))

        result = super().handle(message)
        if method == "initialize" and result is not None and "result" in result:
            params = message.get("params")
            if self._client_supports_code_action(params):
                capabilities = result["result"].get("capabilities")
                if isinstance(capabilities, dict):
                    capabilities["codeActionProvider"] = True
        return result

    def _handle_document_notification(self, method: str, params: Any) -> None:
        super()._handle_document_notification(method, params)
        if method not in {"textDocument/didOpen", "textDocument/didChange"}:
            return
        uri = self._document_uri(params)
        if uri is None:
            return
        document = self.documents.get(uri)
        if document is None or document.language_id != self.nova_adapter.language_id:
            return
        try:
            self.nova_adapter.publish(self, document)
        except (SyntaxError, SymbolError, SemanticError):
            return

    def _handle_nova_code_action(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            assert uri is not None
            document = self.documents.get(uri)
            if document is None:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            parsed = self._nova_code_action_scope(params, document.text)
            if parsed is None:
                return self._error(request_id, -32602, "Invalid params")
            source, start_offset, end_offset, supports_quickfix = parsed
            if not supports_quickfix:
                self.requests.checkpoint(context)
                return self._result(request_id, [])
            if document.language_id != self.nova_adapter.language_id:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            self.requests.checkpoint(context)
            snapshot = self.diagnostics.get(uri)
            if snapshot is None or snapshot.semantic.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._result(request_id, [])

            actions = self._nova_code_actions(
                uri, document, source, snapshot.diagnostics, start_offset, end_offset
            )
            actions = self._render_code_action_workspace_edits(actions, document)
            self.requests.checkpoint(context)
            try:
                return self.diagnostics.commit_if_current(
                    snapshot, lambda: self._result(request_id, actions)
                )
            except DiagnosticError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _nova_code_action_scope(
        self,
        params: dict[str, Any],
        text: str,
    ) -> tuple[SourceText, int, int, bool] | None:
        """Parse one code-action range and quick-fix filter against captured text."""
        source_range = params.get("range")
        action_context = params.get("context")
        if not isinstance(source_range, dict) or not isinstance(action_context, dict):
            return None
        start = source_range.get("start")
        end = source_range.get("end")
        if not isinstance(start, dict) or not isinstance(end, dict):
            return None

        only = action_context.get("only")
        supports_quickfix = True
        if only is not None:
            if not isinstance(only, list) or not all(
                isinstance(item, str) for item in only
            ):
                return None
            supports_quickfix = any(
                item == "quickfix" or item.startswith("quickfix.") for item in only
            )

        source = self._source_text(text)
        try:
            start_offset = source.offset_at(
                Position(line=start.get("line"), character=start.get("character"))
            )
            end_offset = source.offset_at(
                Position(line=end.get("line"), character=end.get("character"))
            )
        except SourceError:
            return None
        if end_offset < start_offset:
            return None
        return source, start_offset, end_offset, supports_quickfix

    def _render_code_action_workspace_edits(
        self,
        actions: list[dict[str, Any]],
        document: Document,
    ) -> list[dict[str, Any]]:
        """Bind eager code-action edits to the captured live document version."""
        return self._render_code_action_workspace_edits_for_version(
            actions,
            uri=document.uri,
            version=document.version,
        )

    def _render_code_action_workspace_edits_for_version(
        self,
        actions: list[dict[str, Any]],
        *,
        uri: str,
        version: int | None,
    ) -> list[dict[str, Any]]:
        """Render document-scoped actions from one captured open/closed version state."""
        for action in actions:
            edit = action.get("edit")
            if not isinstance(edit, dict):
                continue
            changes = edit.get("changes")
            if not isinstance(changes, dict):
                continue
            if any(changed_uri != uri for changed_uri in changes):
                raise AssertionError(
                    "document-scoped code action escaped its captured document"
                )
            title = action.get("title")
            rendered = self._workspace_edit(
                changes,
                versions={uri: version},
                annotation_label=title if isinstance(title, str) else None,
            )
            preserved = {key: value for key, value in edit.items() if key != "changes"}
            action["edit"] = {**preserved, **rendered}
        return actions

    def _nova_code_actions(
        self,
        uri: str,
        document: Document,
        source: SourceText,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        return self._nova_unresolved_function_actions(
            uri,
            document,
            source,
            diagnostics,
            start_offset,
            end_offset,
        )

    def _nova_unresolved_function_actions(
        self,
        uri: str,
        document: Document,
        source: SourceText,
        diagnostics: tuple[Diagnostic, ...],
        start_offset: int,
        end_offset: int,
    ) -> list[dict[str, Any]]:
        """Build deterministic unresolved-function repairs for captured Nova text."""
        actions: list[dict[str, Any]] = []
        insertion = Span(len(document.text), len(document.text))
        insertion_range = self._range(source, insertion)
        for diagnostic in diagnostics:
            if diagnostic.code != "nova.unresolved-function":
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
            if re.fullmatch(_IDENTIFIER, name) is None:
                continue
            prefix = "" if not document.text or document.text.endswith("\n") else "\n"
            actions.append(
                {
                    "title": f"Create function '{name}'",
                    "kind": "quickfix",
                    "diagnostics": [self._diagnostic(source, diagnostic)],
                    "edit": {
                        "changes": {
                            uri: [
                                {
                                    "range": insertion_range,
                                    "newText": f"{prefix}fn {name}() {{}}\n",
                                }
                            ]
                        }
                    },
                }
            )
        return actions

    @staticmethod
    def _client_supports_code_action(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("codeAction"), dict)
