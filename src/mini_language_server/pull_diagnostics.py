"""Exact-snapshot LSP pull diagnostics."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from .cancellation import RequestCancelled, RequestError, StaleRequest
from .diagnostics import DiagnosticError, DiagnosticSnapshot
from .documents import Document, DocumentError
from .folding_ranges import NovaProductLanguageServer as _NovaProductLanguageServer
from .server import ServerState
from .workspace import WorkspaceIndexError
from .workspace_folders import WorkspaceFolderError

_DIAGNOSTIC_PARTIAL_CHUNK_SIZE = 16


class NovaProductLanguageServer(_NovaProductLanguageServer):
    """Final Nova product server with exact-snapshot pull diagnostics."""

    def __init__(self) -> None:
        super().__init__()
        self._diagnostic_refresh_support = False
        self._workspace_diagnostics_publish_succeeded = False

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        method = message.get("method")
        if method == "initialize" and self.state is ServerState.PRE_INITIALIZE:
            params = message.get("params")
            self._diagnostic_refresh_support = (
                self._client_supports_pull_diagnostics(params)
                and self._client_supports_diagnostic_refresh(params)
            )
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
                    "interFileDependencies": True,
                    "workspaceDiagnostics": True,
                }
        return result

    @staticmethod
    def _client_supports_diagnostic_refresh(params: Any) -> bool:
        if not isinstance(params, dict):
            return False
        capabilities = params.get("capabilities")
        if not isinstance(capabilities, dict):
            return False
        workspace = capabilities.get("workspace")
        if not isinstance(workspace, dict):
            return False
        diagnostics = workspace.get("diagnostics")
        return (
            isinstance(diagnostics, dict)
            and diagnostics.get("refreshSupport") is True
        )

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

    def _handle_document_notification(self, method: str, params: Any) -> None:
        uri = self._document_uri(params)
        before = self.workspace_symbols.snapshots()
        self._workspace_diagnostics_publish_succeeded = False
        super()._handle_document_notification(method, params)
        after = self.workspace_symbols.snapshots()
        if (
            not self._workspace_diagnostics_publish_succeeded
            or self._same_workspace_identity(before, after)
            or uri is None
        ):
            return
        other_uris = {
            snapshot.uri for snapshot in (*before, *after)
        } - {uri}
        if other_uris:
            self._queue_diagnostic_refresh()

    def _publish_workspace_diagnostics(self) -> bool:
        succeeded = bool(super()._publish_workspace_diagnostics())
        self._workspace_diagnostics_publish_succeeded = succeeded
        return succeeded

    @staticmethod
    def _same_workspace_identity(left: tuple[Any, ...], right: tuple[Any, ...]) -> bool:
        return len(left) == len(right) and all(
            old is new for old, new in zip(left, right, strict=True)
        )

    def _workspace_scope_changed(self, before: Any, after: Any) -> None:
        super()._workspace_scope_changed(before, after)
        if self._workspace_diagnostics_publish_succeeded:
            self._queue_diagnostic_refresh()

    def _queue_diagnostic_refresh(self) -> None:
        if not self._diagnostic_refresh_support:
            return
        method = "workspace/diagnostic/refresh"
        if self._has_pending_server_request(method):
            return
        self._queue_server_request(method)

    def _handle_pull_diagnostic(self, request_id: Any, params: Any) -> dict[str, Any]:
        context = self._start_document_request(request_id, params)
        if context is None or not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        previous_result_id = params.get("previousResultId")
        if previous_result_id is not None and not isinstance(previous_result_id, str):
            self.requests.finish(context)
            return self._error(request_id, -32602, "Invalid params")
        valid_partial, partial_result_token = self._partial_result_token(params)
        if not valid_partial:
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
                        document,
                        lambda: self._document_diagnostic_result(
                            request_id,
                            report,
                            {},
                            partial_result_token,
                        ),
                    )
                except DocumentError:
                    return self._error(request_id, -32801, "Content modified")

            if snapshot.semantic.symbols.syntax.document is not document:
                self.requests.checkpoint(context)
                return self._error(request_id, -32801, "Content modified")

            source = self._source_text(document.text)
            items = [self._diagnostic(source, item) for item in snapshot.diagnostics]
            result_id = self._diagnostic_result_id(document, snapshot)
            report = self._diagnostic_report(previous_result_id, result_id, items)
            try:
                (
                    related_documents,
                    related_snapshots,
                    related_reports,
                ) = self._pull_related_document_reports(snapshot)
            except DiagnosticError:
                return self._error(request_id, -32801, "Content modified")
            self.requests.checkpoint(context)
            try:
                return self.documents.commit_subset_if_current(
                    (document, *related_documents),
                    lambda: self.diagnostics.commit_all_if_current(
                        (snapshot, *related_snapshots),
                        lambda: self._document_diagnostic_result(
                            request_id,
                            report,
                            related_reports,
                            partial_result_token,
                        ),
                    ),
                )
            except (DocumentError, DiagnosticError):
                return self._error(request_id, -32801, "Content modified")
        except RequestCancelled:
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _document_diagnostic_result(
        self,
        request_id: Any,
        report: dict[str, Any],
        related_reports: dict[str, dict[str, Any]],
        partial_result_token: str | int | None,
    ) -> dict[str, Any]:
        """Commit one document report and optionally stream exact related reports."""
        if partial_result_token is None:
            if related_reports:
                report = {**report, "relatedDocuments": related_reports}
            return self._result(request_id, report)

        related_items = sorted(related_reports.items())
        for start in range(0, len(related_items), _DIAGNOSTIC_PARTIAL_CHUNK_SIZE):
            self._queue_progress(
                partial_result_token,
                {
                    "relatedDocuments": dict(
                        related_items[start : start + _DIAGNOSTIC_PARTIAL_CHUNK_SIZE]
                    )
                },
            )
        return self._result(request_id, report)

    def _pull_related_document_reports(
        self,
        snapshot: DiagnosticSnapshot,
    ) -> tuple[
        tuple[Document, ...],
        tuple[DiagnosticSnapshot, ...],
        dict[str, dict[str, Any]],
    ]:
        """Render direct cross-file diagnostic dependencies as exact full reports."""
        documents: list[Document] = []
        snapshots: list[DiagnosticSnapshot] = []
        reports: dict[str, dict[str, Any]] = {}

        for semantic in snapshot.related_semantics:
            if semantic.uri == snapshot.uri:
                continue
            document = semantic.symbols.syntax.document
            current = self.documents.get(semantic.uri)
            if current is not document:
                raise DiagnosticError(
                    "related diagnostic document is not exact-current"
                )

            related_snapshot = self.diagnostics.get(semantic.uri)
            if related_snapshot is not None:
                if related_snapshot.semantic is not semantic:
                    raise DiagnosticError(
                        "related diagnostic snapshot does not match its semantic parent"
                    )
                source = self._source_text(document.text)
                items = [
                    self._diagnostic(source, diagnostic)
                    for diagnostic in related_snapshot.diagnostics
                ]
                snapshots.append(related_snapshot)
            else:
                items = []

            result_id = self._diagnostic_result_id(document, related_snapshot)
            reports[semantic.uri] = self._diagnostic_report(None, result_id, items)
            documents.append(document)

        return tuple(documents), tuple(snapshots), reports

    def _handle_workspace_diagnostic(self, request_id: Any, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            return self._error(request_id, -32602, "Invalid params")
        previous = self._workspace_previous_result_ids(params.get("previousResultIds", []))
        if previous is None:
            return self._error(request_id, -32602, "Invalid params")
        valid_partial, partial_result_token = self._partial_result_token(params)
        if not valid_partial:
            return self._error(request_id, -32602, "Invalid params")
        valid_work_done, work_done_token = self._workspace_work_done_token(params)
        if not valid_work_done:
            return self._error(request_id, -32602, "Invalid params")
        active_work_done_token = (
            work_done_token if self._work_done_progress_support else None
        )
        try:
            context = self.requests.start(request_id)
        except RequestError:
            return self._error(request_id, -32602, "Invalid params")

        work_done_started = False
        try:
            self.requests.checkpoint(context)
            folder_scope = self.workspace_folders.snapshot()
            documents = self._workspace_documents(folder_scope)
            workspace_snapshots = self.workspace_symbols.snapshots()
            closed_diagnostics = tuple(
                snapshot
                for snapshot in self._closed_workspace_diagnostic_snapshots(
                    tuple(workspace_snapshots)
                )
                if folder_scope.contains(snapshot.uri)
            )
            if active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {
                        "kind": "begin",
                        "title": "Workspace diagnostics",
                        "cancellable": True,
                        "percentage": 0,
                    },
                )
                work_done_started = True

            reports: list[dict[str, Any]] = []
            diagnostic_snapshots: list[DiagnosticSnapshot] = []
            total_documents = len(documents) + len(closed_diagnostics)
            for index, document in enumerate(documents, start=1):
                self.requests.checkpoint(context)
                snapshot = self.diagnostics.get(document.uri)
                if snapshot is not None:
                    if snapshot.semantic.symbols.syntax.document is not document:
                        raise StaleRequest(
                            f"workspace diagnostic snapshot is stale: {document.uri}"
                        )
                    diagnostic_snapshots.append(snapshot)
                    source = self._source_text(document.text)
                    items = [self._diagnostic(source, item) for item in snapshot.diagnostics]
                else:
                    items = []
                result_id = self._diagnostic_result_id(document, snapshot)
                report = self._diagnostic_report(previous.get(document.uri), result_id, items)
                reports.append({"uri": document.uri, "version": document.version, **report})
                if active_work_done_token is not None and total_documents:
                    self._queue_progress(
                        active_work_done_token,
                        {
                            "kind": "report",
                            "message": (
                                f"Processed {index} of {total_documents} "
                                "workspace documents"
                            ),
                            "percentage": (index * 100) // total_documents,
                        },
                    )

            for index, snapshot in enumerate(
                closed_diagnostics,
                start=len(documents) + 1,
            ):
                self.requests.checkpoint(context)
                document = snapshot.semantic.symbols.syntax.document
                source = self._source_text(document.text)
                items = [
                    self._diagnostic(source, item)
                    for item in snapshot.diagnostics
                ]
                result_id = self._diagnostic_result_id_values(
                    uri=document.uri,
                    version=None,
                    text=document.text,
                    snapshot=snapshot,
                )
                report = self._diagnostic_report(
                    previous.get(document.uri),
                    result_id,
                    items,
                )
                reports.append(
                    {
                        "uri": document.uri,
                        "version": None,
                        **report,
                    }
                )
                if active_work_done_token is not None and total_documents:
                    self._queue_progress(
                        active_work_done_token,
                        {
                            "kind": "report",
                            "message": (
                                f"Processed {index} of {total_documents} "
                                "workspace documents"
                            ),
                            "percentage": (index * 100) // total_documents,
                        },
                    )

            self.requests.checkpoint(context)
            try:
                result = self.workspace_folders.commit_if_current(
                    folder_scope.generation,
                    lambda: self.workspace_symbols.commit_snapshots_if_current(
                        workspace_snapshots,
                        lambda: self.documents.commit_matching_if_current(
                            documents,
                            lambda document: folder_scope.contains(document.uri),
                            lambda: self.diagnostics.commit_all_if_current(
                                diagnostic_snapshots,
                                lambda: self._workspace_diagnostic_result(
                                    request_id,
                                    reports,
                                    partial_result_token,
                                ),
                            ),
                        ),
                    ),
                )
            except (
                DocumentError,
                DiagnosticError,
                WorkspaceFolderError,
                WorkspaceIndexError,
            ) as exc:
                raise StaleRequest("workspace diagnostic inputs changed") from exc

            if active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace diagnostics complete"},
                )
            return result
        except RequestCancelled:
            if work_done_started and active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace diagnostics cancelled"},
                )
            return self._error(request_id, -32800, "Request cancelled")
        except StaleRequest:
            if work_done_started and active_work_done_token is not None:
                self._queue_progress(
                    active_work_done_token,
                    {"kind": "end", "message": "Workspace diagnostics changed"},
                )
            return self._error(request_id, -32801, "Content modified")
        finally:
            self.requests.finish(context)

    def _workspace_diagnostic_result(
        self,
        request_id: Any,
        reports: list[dict[str, Any]],
        partial_result_token: str | int | None,
    ) -> dict[str, Any]:
        if partial_result_token is None:
            return self._result(request_id, {"items": reports})

        for start in range(0, len(reports), _DIAGNOSTIC_PARTIAL_CHUNK_SIZE):
            self._queue_progress(
                partial_result_token,
                {
                    "items": reports[
                        start : start + _DIAGNOSTIC_PARTIAL_CHUNK_SIZE
                    ]
                },
            )
        return self._result(request_id, {"items": []})

    @staticmethod
    def _workspace_work_done_token(
        params: dict[str, Any],
    ) -> tuple[bool, str | int | None]:
        if "workDoneToken" not in params:
            return True, None
        token = params["workDoneToken"]
        if isinstance(token, bool) or not isinstance(token, str | int):
            return False, None
        return True, token

    @staticmethod
    def _partial_result_token(
        params: dict[str, Any],
    ) -> tuple[bool, str | int | None]:
        if "partialResultToken" not in params:
            return True, None
        token = params["partialResultToken"]
        if isinstance(token, bool) or not isinstance(token, str | int):
            return False, None
        return True, token

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
        return NovaProductLanguageServer._diagnostic_result_id_values(
            uri=document.uri,
            version=document.version,
            text=document.text,
            snapshot=snapshot,
        )

    @staticmethod
    def _diagnostic_result_id_values(
        *,
        uri: str,
        version: int | None,
        text: str,
        snapshot: DiagnosticSnapshot | None,
    ) -> str:
        version_token = "null" if version is None else str(version)
        digest = sha256()
        digest.update(uri.encode("utf-8"))
        digest.update(b"\0")
        digest.update(version_token.encode("ascii"))
        digest.update(b"\0")
        digest.update(text.encode("utf-8"))
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
                for related in diagnostic.related_information:
                    digest.update(b"\0related\0")
                    digest.update(related.uri.encode("utf-8"))
                    digest.update(b":")
                    digest.update(str(related.span.start).encode("ascii"))
                    digest.update(b":")
                    digest.update(str(related.span.end).encode("ascii"))
                    digest.update(b"\0")
                    digest.update(related.message.encode("utf-8"))
        return f"{version_token}:{digest.hexdigest()[:24]}"
