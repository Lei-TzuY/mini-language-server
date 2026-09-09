"""Exact-snapshot LSP pull diagnostics."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .diagnostics import DiagnosticError, DiagnosticSnapshot
from .documents import Document, DocumentError
from .folding_ranges import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .source import SourceText


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact-snapshot pull diagnostics."""

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if (
            method == "textDocument/diagnostic"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_pull_diagnostic(message.get("id"), message.get("params"))
        if (
            method == "workspace/diagnostic"
            and "id" in message
            and self.state is ServerState.RUNNING
        ):
            return self._handle_workspace_diagnostic(message.get("id"), message.get("params"))

        result = super().handle(message)
        if (
            method == "initialize"
            and result is not None
            and "result" in result
            and self._client_supports_pull_diagnostics(message.get("params"))
        ):
            capabilities = result["result"].get("capabilities")
            if isinstance(capabilities, dict):
                capabilities["diagnosticProvider"] = {
                    "interFileDependencies": False,
                    "workspaceDiagnostics": True,
                }
        return result

    @staticmethod
    def _client_supports_pull_diagnostics(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        text_document = capabilities.get("textDocument")
        if not isinstance(text_document, dict):
            return False
        return isinstance(text_document.get("diagnostic"), dict)

    def _handle_pull_diagnostic(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        previous_result_id = params.get("previousResultId")
        if previous_result_id is not None and not isinstance(previous_result_id, str):
            self.requests.finish(context)
            return self._error(request_id, -32602, "Invalid params")

        try:
            uri = self._document_uri(params)
            assert uri is not None
            self.requests.checkpoint(context)
            document = self.documents.get(uri)
            if document is None:
                return self._error(request_id, -32602, "Invalid params")

            snapshot = self.diagnostics.get(uri)
            if snapshot is None:
                result_id = self._diagnostic_result_id(document, None)
                report = self._diagnostic_report(previous_result_id, result_id, [])
                self.requests.checkpoint(context)
                try:
                    return self.documents.commit_if_current(
                        document, lambda: self._result(request_id, report)
                    )
                except DocumentError:
                    return self._error(request_id, -32801, "Content modified")

            if snapshot.semantic.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._error(request_id, -32801, "Content modified")

            source = SourceText(document.text)
            items = [self._diagnostic(source, item) for item in snapshot.diagnostics]
            result_id = self._diagnostic_result_id(document, snapshot)
            report = self._diagnostic_report(previous_result_id, result_id, items)
            self.requests.checkpoint(context)
            try:
                return self.diagnostics.commit_if_current(
                    snapshot, lambda: self._result(request_id, report)
                )
            except DiagnosticError:
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _handle_workspace_diagnostic(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        previous = self._workspace_previous_result_ids(params.get("previousResultIds", []))
        if previous is None:
            return self._error(request_id, -32602, "Invalid params")
        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        try:
            self.requests.checkpoint(context)
            documents = self.documents.snapshots()
            reports: list[dict[str, Any]] = []
            diagnostic_snapshots: list[DiagnosticSnapshot] = []
            for document in documents:
                self.requests.checkpoint(context)
                snapshot = self.diagnostics.get(document.uri)
                if snapshot is not None:
                    if snapshot.semantic.symbols.syntax.document is not document:
                        return self._error(request_id, -32801, "Content modified")
                    diagnostic_snapshots.append(snapshot)
                    source = SourceText(document.text)
                    items = [self._diagnostic(source, item) for item in snapshot.diagnostics]
                else:
                    items = []
                result_id = self._diagnostic_result_id(document, snapshot)
                report = self._diagnostic_report(previous.get(document.uri), result_id, items)
                reports.append({"uri": document.uri, "version": document.version, **report})

            self.requests.checkpoint(context)
            try:
                return self.documents.commit_all_if_current(
                    documents,
                    lambda: self.diagnostics.commit_all_if_current(
                        diagnostic_snapshots,
                        lambda: self._result(request_id, {"items": reports}),
                    ),
                )
            except (DocumentError, DiagnosticError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        finally:
            self.requests.finish(context)

    @staticmethod
    def _workspace_previous_result_ids(value: Any) -> dict[str, str] | None:
        if not isinstance(value, list):
            return None
        previous: dict[str, str] = {}
        for item in value:
            if not isinstance(item, dict):
                return None
            uri = item.get("uri")
            result_id = item.get("value")
            if not isinstance(uri, str) or not uri or not isinstance(result_id, str):
                return None
            if uri in previous:
                return None
            previous[uri] = result_id
        return previous

    @staticmethod
    def _diagnostic_report(
        previous_result_id: str | None,
        result_id: str,
        items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if previous_result_id == result_id:
            return {"kind": "unchanged", "resultId": result_id}
        return {"kind": "full", "resultId": result_id, "items": items}

    @staticmethod
    def _diagnostic_result_id(
        document: Document, snapshot: DiagnosticSnapshot | None
    ) -> str:
        digest = sha256()
        digest.update(document.uri.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(document.version).encode("ascii"))
        digest.update(b"\0")
        digest.update(document.text.encode("utf-8"))
        if snapshot is not None:
            for diagnostic in snapshot.diagnostics:
                digest.update(b"\0")
                digest.update(str(diagnostic.span.start).encode("ascii"))
                digest.update(b":")
                digest.update(str(diagnostic.span.end).encode("ascii"))
                for value in (
                    diagnostic.severity,
                    diagnostic.message,
                    diagnostic.code or "",
                    diagnostic.source or "",
                    *diagnostic.tags,
                ):
                    digest.update(b"\0")
                    digest.update(value.encode("utf-8"))
        return f"{document.version}:{digest.hexdigest()[:24]}"
